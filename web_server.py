"""Local multi-user AA website. Python standard library + SQLite, no external dependencies."""
import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote

from billing import amount, settle, parse_statement, xlsx, MAX_FILE

ROOT = Path(__file__).resolve().parent
log = logging.getLogger('travel-aa')

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

class Connection(sqlite3.Connection):
    pass

def require(condition, message, status=400):
    if not condition:
        raise Problem(message, status)

def text(value, label, limit=80):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, f'{label}不能为空且最多 {limit} 字')
    return value.strip()

def valid_date(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), '日期格式为 YYYY-MM-DD')
    try:
        date.fromisoformat(value)
    except ValueError:
        raise Problem('日期无效')
    return value

def password_hash(password):
    require(isinstance(password, str) and 6 <= len(password) <= 128, '密码须为 6 至 128 个字符')
    salt = secrets.token_bytes(16)
    hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + hashed.hex()

def password_matches(password, stored):
    if not isinstance(password, str) or len(password) > 128:
        return False
    salt, hashed = stored.split(':')
    result = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(result.hex(), hashed)

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

def public_user(row):
    return {k: row[k] for k in ('id', 'username', 'name', 'role', 'active')}

class App:
    def __init__(self, data_dir, bootstrap_admin=True):
        self.directory = Path(data_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.files = self.directory / 'files'
        self.files.mkdir(exist_ok=True)
        self.dbpath = self.directory / 'travel-aa.sqlite3'
        self.setup_csrf = secrets.token_urlsafe(32)
        self.attempts = {}
        self.rate_lock = threading.Lock()
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','member')), active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), csrf TEXT NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS trips(id INTEGER PRIMARY KEY, name TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trip_members(trip_id INTEGER REFERENCES trips(id), user_id INTEGER REFERENCES users(id), position INTEGER NOT NULL, PRIMARY KEY(trip_id,user_id));
            CREATE TABLE IF NOT EXISTS trip_exclusions(trip_id INTEGER REFERENCES trips(id), user_id INTEGER REFERENCES users(id), PRIMARY KEY(trip_id,user_id));
            CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, trip_id INTEGER REFERENCES trips(id), owner INTEGER REFERENCES users(id), name TEXT NOT NULL, stored TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL, created TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY, trip_id INTEGER REFERENCES trips(id), owner INTEGER REFERENCES users(id), payer INTEGER REFERENCES users(id), title TEXT NOT NULL, cents INTEGER NOT NULL CHECK(cents>0), occurred TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('pending','private','shared')), participants TEXT NOT NULL, source_file INTEGER REFERENCES files(id), external_id TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL DEFAULT '', counterparty TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1);
            CREATE UNIQUE INDEX IF NOT EXISTS bill_dedupe ON bills(trip_id,owner,fingerprint) WHERE fingerprint!='';
            CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, trip_id INTEGER REFERENCES trips(id), file_id INTEGER REFERENCES files(id), owner INTEGER REFERENCES users(id), created TEXT NOT NULL, snapshot TEXT NOT NULL);
            ''')
            if bootstrap_admin:
                # Initialize an empty installation once. Never reset an existing
                # account's password when the service restarts or code updates.
                db.execute('BEGIN IMMEDIATE')
                username = os.environ.get('TRAVEL_AA_ADMIN_USERNAME', '')
                password = os.environ.get('TRAVEL_AA_ADMIN_PASSWORD', '')
                require(bool(username) == bool(password), '初始管理员配置必须同时提供账号和密码')
                if username and db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
                    require(re.fullmatch(r'[A-Za-z0-9_-]{3,32}', username), '初始管理员登录名格式无效')
                    db.execute("INSERT INTO users(username,name,password,role) VALUES(?,?,?,'admin')",
                               (username, username, password_hash(password)))

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.dbpath, timeout=20, factory=Connection)
        db.row_factory = sqlite3.Row
        db.new_files = []
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            for target in db.new_files:
                target.unlink(missing_ok=True)
            raise
        finally:
            db.close()

    def auth(self, handler, db):
        cookies = SimpleCookie()
        try:
            cookies.load(handler.headers.get('Cookie', ''))
        except Exception:
            return None, None
        token = cookies.get('aa_session')
        if not token:
            return None, None
        session = db.execute('SELECT * FROM sessions WHERE token=? AND expires>?', (digest(token.value), int(time.time()))).fetchone()
        if not session:
            return None, None
        user = db.execute('SELECT * FROM users WHERE id=? AND active=1', (session['user_id'],)).fetchone()
        return user, session

    def trip(self, db, trip_id, user):
        trip = db.execute('SELECT * FROM trips WHERE id=?', (trip_id,)).fetchone()
        require(trip is not None, '旅程不存在', 404)
        require(user['role'] == 'admin' or db.execute('SELECT 1 FROM trip_members m WHERE trip_id=? AND user_id=? AND NOT EXISTS(SELECT 1 FROM trip_exclusions e WHERE e.trip_id=m.trip_id AND e.user_id=m.user_id)', (trip_id, user['id'])).fetchone(), '你不属于这个旅程', 403)
        return dict(trip)

    def members(self, db, trip_id):
        return [dict(r) for r in db.execute('SELECT u.id,u.name,u.username,u.active,NOT EXISTS(SELECT 1 FROM trip_exclusions e WHERE e.trip_id=m.trip_id AND e.user_id=m.user_id) AS enrolled FROM trip_members m JOIN users u ON u.id=m.user_id WHERE m.trip_id=? ORDER BY m.position', (trip_id,))]

    def bills(self, db, trip_id, user, all_shared=False):
        if all_shared:
            rows = db.execute("SELECT * FROM bills WHERE trip_id=? AND kind='shared' ORDER BY occurred,id", (trip_id,))
        elif user['role'] == 'admin':
            rows = db.execute('SELECT * FROM bills WHERE trip_id=? ORDER BY occurred DESC,id DESC', (trip_id,))
        else:
            rows = db.execute("SELECT * FROM bills WHERE trip_id=? AND (owner=? OR kind='shared') ORDER BY occurred DESC,id DESC", (trip_id,user['id']))
        result = []
        for r in rows:
            row = dict(r)
            row['participants'] = json.loads(row['participants'])
            row.pop('fingerprint')
            result.append(row)
        return result

    def summary(self, db, trip_id):
        return settle(self.members(db, trip_id), self.bills(db, trip_id, None, True))

    def check_bill(self, db, trip, user, body, existing=None):
        title = text(body.get('title'), '消费项目')
        cents = amount(body.get('amount', ''))
        occurred = valid_date(body.get('date'))
        require(trip['start'] <= occurred <= trip['end'], '账单日期必须在当前旅程日期范围内')
        ids = [r['id'] for r in self.members(db, trip['id']) if r['enrolled']]
        require(user['id'] in ids, '只有本旅程成员可以录入自己的账单', 403)
        payer = body.get('payer', user['id'])
        require(type(payer) is int and (payer in ids or existing is not None and payer==existing['payer']), '垫付者必须属于当前旅程')
        kind = body.get('kind', 'shared')
        require(kind in ('shared', 'private'), '请确认共同消费或私人消费')
        participants = body.get('participants', ids)
        allowed=set(ids)
        if existing is not None and existing['kind']=='shared':allowed.update(json.loads(existing['participants']))
        require(isinstance(participants, list) and all(type(i) is int and i in allowed for i in participants) and len(set(participants)) == len(participants), '分摊成员必须属于当前旅程且不重复；已退出成员仅可保留原有历史分摊')
        require(kind != 'shared' or len(participants) > 0, '共同消费至少选择一位分摊成员')
        return title, cents, occurred, kind, payer, json.dumps(participants if kind == 'shared' else [])

    def session_response(self, db, user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.execute('DELETE FROM sessions WHERE expires<?', (int(time.time()),))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest(token), user['id'], csrf, int(time.time())+12*3600))
        return dict(user=public_user(user), csrf=csrf), token

    def store_file(self, db, trip_id, owner, name, kind, data):
        stored = secrets.token_hex(20)
        target = self.files / stored
        target.write_bytes(data)
        created = datetime.now(timezone.utc).isoformat()
        try:
            fid = db.execute('INSERT INTO files(trip_id,owner,name,stored,kind,size,created) VALUES(?,?,?,?,?,?,?)', (trip_id,owner,name,stored,kind,len(data),created)).lastrowid
        except Exception:
            target.unlink(missing_ok=True)
            raise
        db.new_files.append(target)
        return fid, created

    def route(self, h, path, body, db):
        user, session = self.auth(h, db)
        if path == '/api/me' and h.command == 'GET':
            setup = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
            local_setup = setup and h.is_local()
            return dict(user=public_user(user) if user else None, csrf=session['csrf'] if session else self.setup_csrf if local_setup else '', setup=local_setup, setup_required=setup, public=bool(h.server.public_hosts)), None
        if path in ('/api/setup', '/api/login') and h.command == 'POST':
            username = text(body.get('username'), '登录名', 32).lower()
            require(re.fullmatch(r'[a-z0-9_-]{3,32}', username), '登录名为 3-32 位字母、数字、下划线或连字符')
            if path == '/api/setup':
                require(h.is_local(), '首次设置只能从本机访问', 403)
                require(hmac.compare_digest(h.headers.get('X-CSRF-Token',''), self.setup_csrf), '请刷新首次设置页面', 403)
                hashed = password_hash(body.get('password'))
                display = text(body.get('name'), '姓名', 30)
                db.execute('BEGIN IMMEDIATE')
                require(db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0, '管理员已设置', 409)
                uid = db.execute("INSERT INTO users(username,name,password,role) VALUES(?,?,?,'admin')", (username, display, hashed)).lastrowid
                user = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            else:
                ip = h.client_ip()
                now = time.time()
                with self.rate_lock:
                    for key in list(self.attempts):
                        self.attempts[key] = [t for t in self.attempts[key] if now-t < 600]
                        if not self.attempts[key]:
                            del self.attempts[key]
                    require(len(self.attempts.get(('ip',ip), [])) < 30 and len(self.attempts.get((ip,username), [])) < 5, '登录失败次数过多，请 10 分钟后重试', 429)
                    self.attempts.setdefault(('ip',ip), []).append(now)
                    self.attempts.setdefault((ip,username), []).append(now)
                user = db.execute('SELECT * FROM users WHERE username=? AND active=1', (username,)).fetchone()
                if not user:
                    hashlib.scrypt(b'dummy', salt=b'0'*16, n=16384,r=8,p=1)
                require(user and password_matches(body.get('password'), user['password']), '登录名或密码错误', 401)
                with self.rate_lock:
                    self.attempts.pop((ip,username), None)
            return self.session_response(db, user)
        require(user is not None, '请先登录', 401)
        if h.command != 'GET':
            require(hmac.compare_digest(h.headers.get('X-CSRF-Token',''), session['csrf']), '会话校验失败，请刷新页面', 403)
        if path == '/api/logout' and h.command == 'POST':
            db.execute('DELETE FROM sessions WHERE token=?', (session['token'],))
            return dict(ok=True), ''
        if path == '/api/password' and h.command == 'POST':
            require(password_matches(body.get('current'), user['password']), '原密码错误', 403)
            db.execute('UPDATE users SET password=? WHERE id=?', (password_hash(body.get('password')), user['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
            return dict(ok=True), ''
        if path.startswith('/api/admin/'):
            require(user['role'] == 'admin', '需要管理员权限', 403)
            if path == '/api/admin/users':
                if h.command == 'GET':
                    return dict(users=[public_user(r) for r in db.execute('SELECT * FROM users ORDER BY id')]), None
                if h.command == 'POST':
                    username = text(body.get('username'), '登录名', 32).lower()
                    require(re.fullmatch(r'[a-z0-9_-]{3,32}', username), '登录名须为 3-32 位字母/数字/下划线/连字符')
                    require(not db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone(), '登录名已存在', 409)
                    uid = db.execute("INSERT INTO users(username,name,password,role) VALUES(?,?,?,'member')", (username,text(body.get('name'),'姓名',30),password_hash(body.get('password')))).lastrowid
                    return dict(id=uid), None
            match = re.fullmatch(r'/api/admin/users/(\d+)', path)
            if match and h.command == 'PATCH':
                uid = int(match[1])
                target = db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                require(target is not None,'账号不存在',404)
                require(target['role'] != 'admin', '管理员请使用自己的修改密码入口')
                if 'active' in body:
                    require(type(body['active']) is bool, '启用状态无效')
                    db.execute('UPDATE users SET active=? WHERE id=?',(int(body['active']),uid))
                if 'name' in body:
                    db.execute('UPDATE users SET name=? WHERE id=?',(text(body['name'],'姓名',30),uid))
                if 'password' in body:
                    db.execute('UPDATE users SET password=? WHERE id=?',(password_hash(body['password']),uid))
                db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
                return dict(ok=True),None
            if path == '/api/admin/files' and h.command == 'GET':
                return dict(files=[dict(r) for r in db.execute('SELECT f.id,f.trip_id,f.owner,f.name,f.kind,f.size,f.created,f.deleted,t.name AS trip_name,u.name AS owner_name FROM files f JOIN trips t ON t.id=f.trip_id JOIN users u ON u.id=f.owner ORDER BY f.id DESC')]),None
            if path == '/api/admin/records' and h.command == 'GET':
                records=[]
                for r in db.execute('SELECT r.*,t.name AS trip_name FROM records r JOIN trips t ON t.id=r.trip_id ORDER BY r.id DESC'):
                    row=dict(r);row['snapshot']=json.loads(row['snapshot']);records.append(row)
                return dict(records=records),None
            match = re.fullmatch(r'/api/admin/files/(\d+)', path)
            if match and h.command == 'DELETE':
                f=db.execute('SELECT * FROM files WHERE id=?',(int(match[1]),)).fetchone()
                require(f is not None,'文件不存在',404)
                # Keep the structured bills and settlement snapshot; delete the underlying file only.
                (self.files/f['stored']).unlink(missing_ok=True)
                db.execute('UPDATE files SET deleted=1 WHERE id=?',(f['id'],))
                return dict(ok=True),None
        if path == '/api/trips':
            if h.command == 'GET':
                query = 'SELECT * FROM trips ORDER BY id DESC' if user['role']=='admin' else 'SELECT t.* FROM trips t JOIN trip_members m ON m.trip_id=t.id WHERE m.user_id=? AND NOT EXISTS(SELECT 1 FROM trip_exclusions e WHERE e.trip_id=m.trip_id AND e.user_id=m.user_id) ORDER BY t.id DESC'
                args=() if user['role']=='admin' else (user['id'],)
                return dict(trips=[dict(r) for r in db.execute(query,args)]),None
            if h.command == 'POST':
                require(user['role']=='admin','只有管理员可新建旅程',403)
                start,end=valid_date(body.get('start')),valid_date(body.get('end'))
                require(start<=end,'结束日期不能早于开始日期')
                ids=body.get('members')
                require(isinstance(ids,list) and 0<len(ids)<=100 and all(type(i) is int for i in ids) and len(set(ids))==len(ids),'旅程须有 1-100 位不重复成员')
                for uid in ids:
                    require(db.execute('SELECT 1 FROM users WHERE id=? AND active=1',(uid,)).fetchone(),'成员账号不存在或已停用')
                tid=db.execute('INSERT INTO trips(name,start,end,created) VALUES(?,?,?,?)',(text(body.get('name'),'旅程名称',60),start,end,datetime.now(timezone.utc).isoformat())).lastrowid
                db.executemany('INSERT INTO trip_members VALUES(?,?,?)',[(tid,uid,i) for i,uid in enumerate(ids)])
                return dict(id=tid),None
        match=re.fullmatch(r'/api/trips/(\d+)(?:/(bills|upload|export|migrate|batch|members))?',path)
        if match:
            tid=int(match[1]);trip=self.trip(db,tid,user);action=match[2]
            if not action and h.command=='GET':
                return dict(trip=trip,members=self.members(db,tid),bills=self.bills(db,tid,user),summary=self.summary(db,tid)),None
            if action=='members' and h.command=='PATCH':
                require(user['role']=='admin','只有管理员可修改旅程成员',403)
                ids=body.get('members');before=body.get('members_before')
                require(isinstance(ids,list) and 0<len(ids)<=100 and all(type(i) is int for i in ids) and len(set(ids))==len(ids),'旅程须有 1-100 位不重复成员')
                require(isinstance(before,list) and all(type(i) is int for i in before) and len(set(before))==len(before),'原成员列表无效')
                db.execute('BEGIN IMMEDIATE')
                previous=self.members(db,tid)
                enrolled={m['id'] for m in previous if m['enrolled']}
                require(set(before)==enrolled,'旅程成员已被其他窗口修改，请重新打开后重试',409)
                known={m['id'] for m in previous}
                for uid in ids:
                    row=db.execute('SELECT active FROM users WHERE id=?',(uid,)).fetchone()
                    require(row is not None and (uid in enrolled or row['active']),'新增成员不存在或已停用')
                position=db.execute('SELECT COALESCE(MAX(position),-1) FROM trip_members WHERE trip_id=?',(tid,)).fetchone()[0]+1
                for uid in ids:
                    if uid not in known:
                        db.execute('INSERT INTO trip_members VALUES(?,?,?)',(tid,uid,position));position+=1
                    db.execute('DELETE FROM trip_exclusions WHERE trip_id=? AND user_id=?',(tid,uid))
                for uid in known-set(ids):
                    db.execute('INSERT OR IGNORE INTO trip_exclusions VALUES(?,?)',(tid,uid))
                return dict(ok=True,members=self.members(db,tid)),None
            if action=='batch' and h.command=='POST':
                items=body.get('items')
                require(isinstance(items,list) and 0<len(items)<=200,'一次请选择 1 至 200 笔账单')
                db.execute('BEGIN IMMEDIATE')
                changes=[];seen=set()
                for item in items:
                    require(isinstance(item,dict) and type(item.get('id')) is int,'账单编号无效')
                    bid=item['id']
                    require(bid not in seen,'不可重复提交同一笔账单')
                    seen.add(bid)
                    bill=db.execute('SELECT * FROM bills WHERE id=? AND trip_id=?',(bid,tid)).fetchone()
                    require(bill is not None,'账单不存在或不属于当前旅程',404)
                    require(bill['owner']==user['id'],'只能上传自己录入的账单',403)
                    require(type(item.get('version')) is int and item['version']==bill['version'],'账单已被其他窗口修改，请刷新后重试',409)
                    values=list(self.check_bill(db,trip,user,item,bill))
                    if values[2]==bill['occurred'][:10]:values[2]=bill['occurred']
                    if bill['source_file']:require(values[4]==user['id'],'导入账单垫付者是上传账号本人')
                    changes.append((values,bid,bill['version']))
                for values,bid,version in changes:
                    updated=db.execute('UPDATE bills SET title=?,cents=?,occurred=?,kind=?,payer=?,participants=?,version=version+1 WHERE id=? AND version=?',(*values,bid,version))
                    require(updated.rowcount==1,'账单已被其他窗口修改，请刷新后重试',409)
                return dict(updated=len(changes)),None
            if action=='bills' and h.command=='POST':
                title,cents,occurred,kind,payer,participants=self.check_bill(db,trip,user,body)
                bid=db.execute('INSERT INTO bills(trip_id,owner,payer,title,cents,occurred,kind,participants) VALUES(?,?,?,?,?,?,?,?)',(tid,user['id'],payer,title,cents,occurred,kind,participants)).lastrowid
                return dict(id=bid),None
            if action=='upload' and h.command=='POST':
                ids=[m['id'] for m in self.members(db,tid) if m['enrolled']]
                require(user['id'] in ids,'只有旅程成员可上传自己的账单',403)
                filename=text(body.get('name'),'文件名',150)
                require('/' not in filename and '\\' not in filename and not any(ord(c)<32 for c in filename),'文件名不能含路径或控制字符')
                encoded=body.get('data')
                require(isinstance(encoded,str) and len(encoded)<=((MAX_FILE+2)//3)*4,'文件过大')
                try:
                    data=base64.b64decode(encoded,validate=True)
                except (ValueError,TypeError):
                    raise Problem('文件编码无效')
                parsed,report=parse_statement(filename,data,trip['start'],trip['end'])
                fid,_=self.store_file(db,tid,user['id'],filename,'upload',data)
                inserted=duplicate=0
                for item in parsed:
                    # transaction ID deduplicates within THIS trip and uploader only.
                    identity=item['external_id'] or json.dumps([item['occurred'],item['cents'],item['title'],item['counterparty']],ensure_ascii=False)
                    fingerprint=digest(('alipay:' if item['source']=='alipay' else '')+identity)
                    if db.execute('SELECT 1 FROM bills WHERE trip_id=? AND owner=? AND fingerprint=?',(tid,user['id'],fingerprint)).fetchone():
                        duplicate+=1;continue
                    db.execute("INSERT INTO bills(trip_id,owner,payer,title,cents,occurred,kind,participants,source_file,external_id,fingerprint,counterparty,status) VALUES(?,?,?,?,?,?,'pending',?,?,?,?,?,?)",(tid,user['id'],user['id'],item['title'],item['cents'],item['occurred'],json.dumps(ids),fid,item['external_id'],fingerprint,item['counterparty'],item['status']))
                    inserted+=1
                return dict(file_id=fid,inserted=inserted,duplicate=duplicate,report=report),None
            if action=='export' and h.command=='POST':
                shared=self.bills(db,tid,user,True);summary=self.summary(db,tid)
                names={m['id']:m['name']+' ('+m['username']+')' for m in self.members(db,tid)}
                sheets=[('个人汇总',[['旅程','开始日期','结束日期','成员','已垫付(元)','应承担(元)','净额(元)','应收/应付']]+[[trip['name'],trip['start'],trip['end'],names[r['id']],Decimal(r['paid'])/100,Decimal(r['share'])/100,Decimal(r['balance'])/100,'应收' if r['balance']>0 else '应付' if r['balance']<0 else '平账'] for r in summary['rows']]),('消费明细',[['旅程','交易时间','项目','垫付者','金额(元)','参与分摊','来源','账单ID']]+[[trip['name'],b['occurred'],b['title'],names[b['payer']],Decimal(b['cents'])/100,'、'.join(names[i] for i in b['participants']),'导入' if b['source_file'] else '手动',b['id']] for b in shared]),('转账清单',[['旅程','付款人','收款人','转账金额(元)']]+[[trip['name'],names[t['from_id']],names[t['to_id']],Decimal(t['cents'])/100] for t in summary['transfers']])]
                data=xlsx(sheets)
                fid,created=self.store_file(db,tid,user['id'],f'trip-{tid}-settlement.xlsx','export',data)
                db.execute('INSERT INTO records(trip_id,file_id,owner,created,snapshot) VALUES(?,?,?,?,?)',(tid,fid,user['id'],created,json.dumps(dict(trip=trip,summary=summary,bills=shared),ensure_ascii=False)))
                return dict(file_id=fid,url=f'/api/files/{fid}'),None
            if action=='migrate' and h.command=='POST':
                # Legacy migration is explicit, only own paid bills; no browser data is removed.
                mapping=body.get('mapping');items=body.get('bills')
                require(isinstance(mapping,dict) and isinstance(items,list) and len(items)<=1000,'旧账本迁移格式无效')
                require(items,'没有可迁移的本人垫付账单')
                inserted=duplicate=0
                for old in items:
                    require(isinstance(old,dict),'旧账单格式无效')
                    require(mapping.get(str(old.get('payer'))) == user['id'],'只能迁移本人实际垫付的旧账单',403)
                    participants=old.get('participants',[])
                    require(isinstance(participants,list),'旧账单成员格式无效')
                    require(type(old.get('cents')) is int,'旧账单金额须为整数分')
                    draft=dict(title=old.get('title'),amount=str(Decimal(old['cents'])/100),date=body.get('date'),kind='shared',payer=user['id'],participants=[mapping.get(str(i)) for i in participants])
                    values=self.check_bill(db,trip,user,draft)
                    fp=digest('legacy:'+str(old.get('id',''))+':'+json.dumps(old,sort_keys=True,ensure_ascii=False))
                    if db.execute('SELECT 1 FROM bills WHERE trip_id=? AND owner=? AND fingerprint=?',(tid,user['id'],fp)).fetchone():
                        duplicate+=1;continue
                    title,cents,when,kind,payer,parts=values
                    db.execute('INSERT INTO bills(trip_id,owner,payer,title,cents,occurred,kind,participants,fingerprint) VALUES(?,?,?,?,?,?,?,?,?)',(tid,user['id'],payer,title,cents,when,kind,parts,fp));inserted+=1
                return dict(inserted=inserted,duplicate=duplicate),None
        match=re.fullmatch(r'/api/bills/(\d+)',path)
        if match and h.command in ('PATCH','DELETE'):
            bill=db.execute('SELECT * FROM bills WHERE id=?',(int(match[1]),)).fetchone()
            require(bill is not None,'账单不存在',404)
            trip=self.trip(db,bill['trip_id'],user)
            require(bill['owner']==user['id'],'只能修改或删除自己上传/录入的账单',403)
            if h.command=='DELETE':
                db.execute('DELETE FROM bills WHERE id=?',(bill['id'],));return dict(ok=True),None
            require(body.get('version')==bill['version'],'账单已被其他窗口修改，请刷新后重试',409)
            values=list(self.check_bill(db,trip,user,body,bill))
            if values[2] == bill['occurred'][:10]:
                values[2] = bill['occurred']
            if bill['source_file']:
                require(values[4]==user['id'],'导入账单垫付者是上传账号本人')
            updated=db.execute('UPDATE bills SET title=?,cents=?,occurred=?,kind=?,payer=?,participants=?,version=version+1 WHERE id=? AND version=?',(*values,bill['id'],body['version']))
            require(updated.rowcount==1,'账单已被其他窗口修改，请刷新后重试',409)
            return dict(ok=True),None
        match=re.fullmatch(r'/api/files/(\d+)',path)
        if match and h.command=='GET':
            f=db.execute('SELECT * FROM files WHERE id=?',(int(match[1]),)).fetchone()
            require(f is not None,'文件不存在',404)
            self.trip(db,f['trip_id'],user)
            require(user['role']=='admin' or f['owner']==user['id'],'只能下载自己的文件',403)
            require(not f['deleted'],'文件已删除；账单和结算记录仍保留',410)
            target=self.files/f['stored']
            require(target.is_file(),'文件不存在',404)
            return dict(binary=target.read_bytes(),name=f['name']),None
        raise Problem('接口不存在或请求方式不正确',404)

class Handler(BaseHTTPRequestHandler):
    server_version='Tongxing'
    def log_message(self, fmt, *args):
        # Do not log request bodies, cookies, passwords or query parameters.
        pass
    def is_local(self):
        return self.client_address[0] in ('127.0.0.1','::1') and self.headers.get('Host') not in self.server.public_hosts and not (self.server.trust_proxy and self.headers.get('X-Forwarded-For') is not None)
    def client_ip(self):
        if self.server.trust_proxy and self.client_address[0] in ('127.0.0.1','::1') and self.headers.get('X-Forwarded-For'):
            return str(ipaddress.ip_address(self.headers['X-Forwarded-For']))
        return self.client_address[0]
    def respond(self,status,data,content_type='application/json; charset=utf-8',cookie=None,disposition=None):
        if isinstance(data,dict):
            data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        if cookie is not None:
            self.send_header('Set-Cookie',f'aa_session={cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age={43200 if cookie else 0}'+('; Secure' if self.server.secure_cookie else ''))
        if disposition:
            self.send_header('Content-Disposition',disposition)
        self.end_headers();self.wfile.write(data)
    def dispatch(self):
        try:
            host=self.headers.get('Host','')
            path=urlsplit(self.path).path
            body={}
            if self.command!='GET':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                except ValueError:
                    raise Problem('请求长度无效')
                require(0<length<=7*1024*1024,'请求体过大或为空',413)
                self.connection.settimeout(20)
                # Drain the bounded request body before returning a header error.
                # Closing with unread bytes can reset the connection on Windows,
                # hiding the useful 403/415 response from the browser.
                raw=self.rfile.read(length)
                require(len(raw)==length,'请求体不完整')
                origin=self.headers.get('Origin')
                allowed='http://'+host
                origins=('https://'+host,) if host in self.server.public_hosts else (allowed,'https://'+host)
                require(origin in origins,'请求来源无效',403)
                require(self.headers.get('Content-Type','').split(';')[0]=='application/json','请求须为 JSON',415)
                try:
                    body=json.loads(raw)
                except (json.JSONDecodeError,UnicodeDecodeError):
                    raise Problem('JSON 无效')
                require(isinstance(body,dict),'请求须为 JSON 对象')
            require(host in self.server.allowed_hosts,'访问主机不允许',403)
            if path.startswith('/api/'):
                with self.server.app.db() as db:
                    result,cookie=self.server.app.route(self,path,body,db)
                if 'binary' in result:
                    from urllib.parse import quote
                    mime=mimetypes.guess_type(result['name'])[0] or 'application/octet-stream'
                    self.respond(200,result['binary'],mime,disposition="attachment; filename*=UTF-8''"+quote(result['name']))
                else:
                    self.respond(200,result,cookie=cookie)
                return
            require(self.command=='GET','请求方式无效',405)
            paths={'/':'index.html','/index.html':'index.html','/app.js':'app.js','/style.css':'style.css','/settlement.js':'settlement.js','/legacy/':'legacy/index.html','/legacy/index.html':'legacy/index.html','/legacy/app.js':'legacy/app.js','/legacy/style.css':'legacy/style.css','/legacy/settlement.js':'legacy/settlement.js','/samples/wechat-sample.csv':'samples/wechat-sample.csv','/samples/alipay-sample.csv':'samples/alipay-sample.csv'}
            require(path in paths,'页面不存在',404)
            require(not (self.server.public_hosts and path.startswith('/legacy')),'页面不存在',404)
            source=ROOT/paths[path]
            require(source.is_file(),'页面不存在',404)
            mime=mimetypes.guess_type(source.name)[0] or 'application/octet-stream'
            self.respond(200,source.read_bytes(),mime+'; charset=utf-8')
        except (Problem,ValueError) as e:
            self.respond(getattr(e,'status',400),dict(error=getattr(e,'message',str(e))))
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            pass
        except Exception:
            log.exception('Request failed (no request body logged)')
            self.respond(500,dict(error='服务处理失败，请稍后重试'))
    do_GET=dispatch
    do_POST=dispatch
    do_PATCH=dispatch
    do_DELETE=dispatch

def make_server(host='127.0.0.1',port=5173,data_dir=None,public_hosts=(),secure_cookie=False,trust_proxy=False,bootstrap_admin=True):
    require(host in ('127.0.0.1','::1'),'后端只可绑定本机回环地址')
    require(all(isinstance(h,str) and re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?',h) for h in public_hosts),'公网主机格式无效')
    require(not public_hosts or secure_cookie,'公网模式必须启用 Secure Cookie')
    server=ThreadingHTTPServer((host,port),Handler)
    server.daemon_threads=True
    server.app=App(data_dir or ROOT/'data',bootstrap_admin=bootstrap_admin)
    actual=server.server_port
    server.public_hosts=set(public_hosts)
    server.allowed_hosts={f'localhost:{actual}',f'127.0.0.1:{actual}'} | server.public_hosts
    server.secure_cookie=secure_cookie
    server.trust_proxy=trust_proxy
    return server

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=5173)
    parser.add_argument('--data-dir',default=str(ROOT/'data'))
    parser.add_argument('--public-host',action='append',default=[])
    parser.add_argument('--secure-cookie',action='store_true')
    parser.add_argument('--trust-proxy',action='store_true')
    args=parser.parse_args()
    server=make_server(port=args.port,data_dir=args.data_dir,public_hosts=args.public_host,secure_cookie=args.secure_cookie,trust_proxy=args.trust_proxy)
    print(f'同行后端：http://localhost:{server.server_port} ；数据目录：{server.app.directory}。初始管理员由私有部署配置提供；已有账号保持不变。',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
