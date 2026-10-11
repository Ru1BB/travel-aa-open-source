import base64
import csv
import hashlib
import http.client
import io
import json
import os
import random
import sqlite3
import tempfile
import threading
import unittest
import zipfile
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from billing import amount, parse_statement, settle, xlsx, read_xlsx
from web_server import make_server, password_hash, password_matches, Problem, App

SAMPLE=(Path(__file__).parent/'samples'/'wechat-sample.csv').read_bytes()

def alipay_sample():
    """Synthetic values, real export structure; never copy personal samples."""
    rows=[['支付宝交易记录明细查询']]+[['说明行']]*22
    rows += [['交易时间','交易分类','交易对方','对方账号','商品说明','收/支','金额','收/付款方式','交易状态','交易订单号','商家订单号','备注','']]
    def row(time,kind='餐饮美食',direction='支出',status='交易成功',cash=' 10.01 ',order='000001'):
        return [time,kind,'测试商户','test@example.invalid','测试商品,含逗号',direction,cash,'测试方式',status,order+'\t','shop','', '']
    rows += [row('2026-10-01 00:00:00'),row('2026-10-03 23:59:59',status='等待确认收货',order='000002'),
             row('2026-10-04 00:00:00'),row('2026-10-02 12:00:00',direction='收入'),
             row('2026-10-02 12:00:00',direction='不计收支'),row('2026-10-02 12:00:00',status='退款成功'),
             row('2026-10-02 12:00:00',status='交易关闭'),row('2026-10-02 12:00:00',kind='转账红包'),
             row('2026-10-02 12:00:00',kind='信用借还'),row('2026-10-02 12:00:00',status='未知状态'),
             row('invalid-time'),row('2026-10-02 12:00:00',cash='0')]
    out=io.StringIO();csv.writer(out).writerows(rows)
    return out.getvalue().encode('gb18030')

class Client:
    def __init__(self,port):
        self.port=port;self.cookie='';self.csrf=''
    def request(self,path,method='GET',body=None,csrf=True,origin=True,extra_headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.port,timeout=20)
        headers={'Host':f'127.0.0.1:{self.port}'}
        if self.cookie:headers['Cookie']=self.cookie
        if method!='GET':
            headers['Content-Type']='application/json'
            if origin:headers['Origin']=f'http://127.0.0.1:{self.port}'
            if csrf:headers['X-CSRF-Token']=self.csrf
        headers.update(extra_headers or {})
        conn.request(method,path,json.dumps(body).encode() if body is not None else None,headers)
        res=conn.getresponse();raw=res.read();status=res.status
        if res.getheader('Set-Cookie'):self.cookie=res.getheader('Set-Cookie').split(';',1)[0]
        if 'json' in res.getheader('Content-Type',''):
            data=json.loads(raw)
            if 'csrf' in data:self.csrf=data['csrf']
        else:data=raw
        conn.close();return status,data
    def get(self,path):
        status,data=self.request(path);assert status==200,(status,data);return data
    def post(self,path,body):
        status,data=self.request(path,'POST',body);assert status==200,(status,data);return data

class BillingTests(unittest.TestCase):
    def test_alipay_real_header_encoding_and_payment_filters(self):
        rows,r=parse_statement('alipay.csv',alipay_sample(),'2026-10-01','2026-10-03')
        self.assertEqual(r['source'],'alipay');self.assertEqual(r['parsed'],2)
        self.assertEqual(r['outside'],1);self.assertEqual(r['non_expense'],2)
        self.assertEqual(r['refund'],2);self.assertEqual(r['transfer'],2);self.assertEqual(r['invalid'],3)
        self.assertEqual(rows[0]['external_id'],'000001');self.assertEqual(rows[0]['cents'],1001)
        self.assertEqual(rows[1]['occurred'],'2026-10-03 23:59:59')
        self.assertTrue(all('测试' not in w for w in r['warnings']))

    def test_wechat_numeric_dates_and_fund_movements(self):
        header=['交易时间','交易类型','交易对方','商品','收/支','金额(元)','支付方式','当前状态','交易单号','商户单号','备注']
        rows=[['说明']]*15+[header]
        rows += [[46296.0,'商户消费','测试商户','早餐','支出',10.01,'测试','支付成功','000001','',''],
                 [46296.5,'转账','测试人','转账','支出',20,'测试','对方已收钱','000002','',''],
                 [46296.5,'商户消费','测试商户','退款','支出',10,'测试','已全额退款','000003','','']]
        items,r=parse_statement('wechat.xlsx',xlsx([('微信',rows)]),'2026-10-01','2026-10-03')
        self.assertEqual(len(items),1);self.assertEqual(items[0]['occurred'],'2026-10-01 00:00:00')
        self.assertEqual(r['source'],'wechat');self.assertEqual(r['transfer'],1);self.assertEqual(r['refund'],1)

    def test_six_character_password_boundary(self):
        hashed=password_hash('abc123');self.assertTrue(password_matches('abc123',hashed))
        for password in ('abc12','x'*129):
            with self.assertRaises(Problem):password_hash(password)

    def test_cent_and_random_conservation(self):
        self.assertEqual(amount('100.01'),10001)
        for v in ('0','-2','0.001','1e3','nan','100000000.01'):
            with self.assertRaises(ValueError):amount(v)
        rand=random.Random(20261008);members=[{'id':i,'name':str(i)} for i in range(1,6)]
        for _ in range(1000):
            bills=[]
            for n in range(rand.randint(1,50)):
                bills.append(dict(cents=rand.randint(1,100000),payer=rand.randint(1,5),participants=rand.sample(range(1,6),rand.randint(1,5)),kind='shared'))
            r=settle(members,bills);self.assertEqual(sum(x['share'] for x in r['rows']),r['total'])
            balances={x['id']:x['balance'] for x in r['rows']}
            for t in r['transfers']:balances[t['from_id']]+=t['cents'];balances[t['to_id']]-=t['cents']
            self.assertTrue(all(v==0 for v in balances.values()))
        result=settle(members,[dict(cents=100,payer=5,participants=[3,2,1],kind='shared')])
        self.assertEqual([r['share'] for r in result['rows']],[34,33,33,0,0])

    def test_csv_dates_and_xlsx_roundtrip(self):
        rows,r=parse_statement('wechat.csv',SAMPLE,'2026-10-01','2026-10-03')
        self.assertEqual(len(rows),3);self.assertEqual(r['outside'],2);self.assertEqual(r['non_expense'],1);self.assertEqual(r['refund'],1)
        self.assertEqual(rows[0]['occurred'],'2026-10-01 00:00:00');self.assertEqual(rows[-1]['occurred'],'2026-10-03 23:59:59')
        import csv
        csvrows=list(csv.reader(io.StringIO(SAMPLE.decode())))
        data=xlsx([('微信账单',csvrows)])
        imported,_=parse_statement('wechat.xlsx',data,'2026-10-01','2026-10-03')
        self.assertEqual(imported,rows)
        with self.assertRaises(ValueError):parse_statement('bill.xls',SAMPLE,'2026-10-01','2026-10-03')
        with self.assertRaises(ValueError):parse_statement('bill.csv',b'bad header','2026-10-01','2026-10-03')

    def test_spreadsheet_injection_and_bounded_archive(self):
        data=xlsx([('安全文本',[['项目','金额'],['=HYPERLINK("https://example.com")',1]])])
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertNotIn(b'<f>',z.read('xl/worksheets/sheet1.xml'))
            for name in z.namelist():
                if name.endswith('.xml') or name.endswith('.rels'):ET.fromstring(z.read(name))
        self.assertEqual(read_xlsx(data)[1][0],'=HYPERLINK("https://example.com")')
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as z:z.writestr('../evil.xml','x')
        with self.assertRaises(ValueError):read_xlsx(output.getvalue())

class BackendTests(unittest.TestCase):
    def test_alipay_pending_dedup_and_platform_namespaces(self):
        body=dict(name='alipay.csv',data=base64.b64encode(alipay_sample()).decode())
        route=f'/api/trips/{self.trip_a}/upload'
        result=self.alice.post(route,body);self.assertEqual(result['inserted'],2)
        self.assertEqual(self.alice.post(route,body)['duplicate'],2)
        original=list(csv.reader(io.StringIO(SAMPLE.decode())))
        original[4][8]='000001'
        out=io.StringIO();csv.writer(out).writerows(original)
        result=self.alice.post(route,dict(name='wechat.csv',data=base64.b64encode(out.getvalue().encode()).decode()))
        self.assertEqual(result['inserted'],3)
        trip=self.alice.get(f'/api/trips/{self.trip_a}')
        self.assertTrue(all(b['kind']=='pending' for b in trip['bills']))
        self.assertEqual(trip['summary']['total'],0)
        self.assertEqual(len(self.bob.get(f'/api/trips/{self.trip_a}')['bills']),0)

    def test_password_create_reset_and_change_six_characters(self):
        bad=dict(username='short',name='测试',password='abc12')
        self.assertEqual(self.admin.request('/api/admin/users','POST',bad)[0],400)
        uid=self.admin.post('/api/admin/users',{**bad,'password':'abc123'})['id']
        client=Client(self.server.server_port)
        client.post('/api/login',dict(username='short',password='abc123'))
        self.assertEqual(client.request('/api/password','POST',dict(current='abc123',password='abc12'))[0],400)
        client.post('/api/password',dict(current='abc123',password='def456'))
        self.assertEqual(client.request('/api/trips')[0],401)
        self.assertEqual(self.admin.request(f'/api/admin/users/{uid}','PATCH',dict(password='abc12'))[0],400)
        self.admin.request(f'/api/admin/users/{uid}','PATCH',dict(password='ghi789'))
        client.post('/api/login',dict(username='short',password='ghi789'))

    def setUp(self):
        test_dir=Path(__file__).parent/'verification'
        test_dir.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='travel-aa-test-',dir=test_dir)
        self.server=make_server(port=0,data_dir=self.temp.name,bootstrap_admin=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.admin=Client(self.server.server_port);self.admin.get('/api/me')
        self.admin.post('/api/setup',dict(username='admin',name='管理员',password='unit-test-admin-password'))
        self.a=self.admin.post('/api/admin/users',dict(username='alice',name='甲',password='unit-test-alice-password'))['id']
        self.b=self.admin.post('/api/admin/users',dict(username='bob',name='乙',password='unit-test-bob-password'))['id']
        self.c=self.admin.post('/api/admin/users',dict(username='carol',name='丙',password='unit-test-carol-password'))['id']
        self.trip_a=self.admin.post('/api/trips',dict(name='旅程A',start='2026-10-01',end='2026-10-03',members=[self.a,self.b]))['id']
        self.trip_b=self.admin.post('/api/trips',dict(name='旅程B',start='2026-10-01',end='2026-10-03',members=[self.b,self.c]))['id']
        self.alice=Client(self.server.server_port);self.alice.post('/api/login',dict(username='alice',password='unit-test-alice-password'))
        self.bob=Client(self.server.server_port);self.bob.post('/api/login',dict(username='bob',password='unit-test-bob-password'))
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def upload(self,client,trip):
        return client.post(f'/api/trips/{trip}/upload',dict(name='wechat.csv',data=base64.b64encode(SAMPLE).decode()))
    def change(self,client,bill,kind='shared',members=None,payer=None):
        body=dict(version=bill['version'],title=bill['title'],amount=f"{bill['cents']/100:.2f}",date=bill['occurred'][:10],payer=bill['payer'] if payer is None else payer,kind=kind,participants=members if members is not None else bill['participants'])
        return client.request(f"/api/bills/{bill['id']}",'PATCH',body)

    def test_trip_member_changes_preserve_history_and_revoke_access(self):
        bid=self.alice.post(f'/api/trips/{self.trip_a}/bills',dict(title='历史晚餐',amount='100.01',date='2026-10-01',payer=self.b,kind='shared',participants=[self.a,self.b]))['id']
        self.upload(self.bob,self.trip_a)
        exported=self.admin.post(f'/api/trips/{self.trip_a}/export',{})
        before=self.admin.get(f'/api/trips/{self.trip_a}')
        file_bytes=self.admin.request(exported['url'])[1]
        path=f'/api/trips/{self.trip_a}/members'
        body=dict(members=[self.a,self.c],members_before=[self.a,self.b])
        self.assertEqual(self.alice.request(path,'PATCH',body)[0],403)
        self.assertEqual(self.admin.request(path,'PATCH',body,csrf=False)[0],403)
        self.assertEqual(self.admin.request(path,'PATCH',dict(members=[],members_before=[self.a,self.b]))[0],400)
        self.assertEqual(self.admin.request(path,'PATCH',dict(members=[self.a,999],members_before=[self.a,self.b]))[0],400)
        self.admin.request(path,'PATCH',body)
        after=self.admin.get(f'/api/trips/{self.trip_a}')
        self.assertEqual(before['bills'],after['bills'])
        self.assertEqual(before['summary']['total'],after['summary']['total'])
        oldrows={r['id']:(r['paid'],r['share'],r['balance']) for r in before['summary']['rows']}
        self.assertEqual({r['id']:(r['paid'],r['share'],r['balance']) for r in after['summary']['rows'] if r['id'] in oldrows},oldrows)
        self.assertEqual([m['id'] for m in after['members'] if m['enrolled']],[self.a,self.c])
        self.assertEqual(self.admin.request(exported['url'])[1],file_bytes)
        self.assertEqual(len(self.admin.get('/api/admin/records')['records']),1)
        self.assertEqual(self.bob.request(f'/api/trips/{self.trip_a}')[0],403)
        self.assertEqual(self.bob.request(exported['url'])[0],403)
        self.assertNotIn(self.trip_a,[t['id'] for t in self.bob.get('/api/trips')['trips']])
        self.assertEqual(self.admin.request(path,'PATCH',body)[0],409)
        carol=Client(self.server.server_port);carol.post('/api/login',dict(username='carol',password='unit-test-carol-password'))
        self.assertEqual(carol.request(f'/api/trips/{self.trip_a}')[0],200)
        self.assertEqual(self.alice.request(f'/api/trips/{self.trip_a}/bills','POST',dict(title='新消费',amount='10',date='2026-10-01',payer=self.a,kind='shared',participants=[self.b]))[0],400)
        historical=next(b for b in after['bills'] if b['id']==bid)
        self.assertEqual(self.change(self.alice,historical)[0],200)
        self.assertEqual(self.admin.request(path,'PATCH',dict(members=[self.a,self.b,self.c],members_before=[self.a,self.c]))[0],200)
        self.assertEqual(self.bob.request(f'/api/trips/{self.trip_a}')[0],200)
        # Reopening an existing database only adds missing tables; it never resets ledger data.
        old_app=self.server.app
        with old_app.db() as db:
            db.execute('DROP TABLE trip_exclusions')
        reopened=App(old_app.directory,bootstrap_admin=False)
        with reopened.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bills').fetchone()[0],len(before['bills']))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM trip_exclusions').fetchone()[0],0)

    def test_atomic_batch_confirmation_and_permissions(self):
        self.upload(self.alice,self.trip_a)
        bills=self.alice.get(f'/api/trips/{self.trip_a}')['bills']
        def item(b,kind='shared'):
            return dict(id=b['id'],version=b['version'],title=b['title'],amount=f"{b['cents']/100:.2f}",date=b['occurred'][:10],payer=b['payer'],kind=kind,participants=[self.a])
        path=f'/api/trips/{self.trip_a}/batch'
        items=[item(bills[0]),item(bills[1],'private')]
        self.assertEqual(self.bob.request(path,'POST',dict(items=items))[0],403)
        self.assertEqual(self.alice.request(path,'POST',dict(items=items),csrf=False)[0],403)
        stale=[items[0],dict(items[1],version=999)]
        self.assertEqual(self.alice.request(path,'POST',dict(items=stale))[0],409)
        self.assertTrue(all(b['kind']=='pending' and b['version']==1 for b in self.alice.get(f'/api/trips/{self.trip_a}')['bills']))
        self.assertEqual(self.alice.request(path,'POST',dict(items=[items[0],items[0]]))[0],400)
        self.assertEqual(self.alice.request(path,'POST',dict(items=[dict(items[0],participants=[self.c])]))[0],400)
        self.assertEqual(self.alice.request(path,'POST',dict(items=[]))[0],400)
        foreign=self.bob.post(f'/api/trips/{self.trip_b}/bills',dict(title='测试交通',amount='10',date='2026-10-01',payer=self.b,kind='shared',participants=[self.b]))['id']
        self.assertEqual(self.alice.request(path,'POST',dict(items=[dict(items[0],id=foreign)]))[0],404)
        result=self.alice.post(path,dict(items=items));self.assertEqual(result['updated'],2)
        after={b['id']:b for b in self.alice.get(f'/api/trips/{self.trip_a}')['bills']}
        self.assertEqual(after[items[0]['id']]['kind'],'shared')
        self.assertEqual(after[items[1]['id']]['kind'],'private')
        self.assertEqual(after[items[0]['id']]['occurred'],bills[0]['occurred'])
        self.assertEqual(after[items[0]['id']]['participants'],[self.a])
        self.assertEqual(self.alice.get(f'/api/trips/{self.trip_a}')['summary']['total'],bills[0]['cents'])
        self.assertEqual(self.alice.request(path,'POST',dict(items=items))[0],409)

    def test_trip_ownership_private_confirmation_and_settlement(self):
        self.assertEqual([t['id'] for t in self.alice.get('/api/trips')['trips']],[self.trip_a])
        self.assertEqual(self.alice.request(f'/api/trips/{self.trip_b}')[0],403)
        self.assertEqual(self.alice.request('/api/admin/users')[0],403)
        upload=self.upload(self.alice,self.trip_a);self.assertEqual(upload['inserted'],3)
        data=self.alice.get(f'/api/trips/{self.trip_a}');self.assertEqual(data['summary']['total'],0)
        self.assertEqual(self.bob.get(f'/api/trips/{self.trip_a}')['bills'],[])
        bills={b['title']:b for b in data['bills']}
        first=bills['旅程首日早餐'];private=bills['私人零食'];last=bills['旅程末日车费']
        self.assertEqual(self.change(self.admin,first)[0],403)
        self.assertEqual(self.change(self.bob,first)[0],403)
        self.assertEqual(self.change(self.alice,first,members=[self.c])[0],400)
        self.assertEqual(self.change(self.alice,first,members=[self.a,self.b])[0],200)
        self.assertEqual(self.change(self.alice,private,kind='private',members=[])[0],200)
        self.assertEqual(self.change(self.alice,last,members=[self.b])[0],200)
        self.assertEqual(self.change(self.alice,last,members=[self.b])[0],409)
        data=self.bob.get(f'/api/trips/{self.trip_a}');self.assertEqual(len(data['bills']),2)
        self.assertEqual({b['occurred'] for b in data['bills']},{'2026-10-01 00:00:00','2026-10-03 23:59:59'})
        self.assertEqual(data['summary']['total'],16001)
        self.assertEqual(data['summary']['transfers'],[dict(from_id=self.b,to_id=self.a,cents=11000)])
        self.assertEqual(self.bob.get(f'/api/trips/{self.trip_b}')['summary']['total'],0)
        self.assertEqual(self.bob.request(f"/api/files/{upload['file_id']}")[0],403)
        self.assertEqual(self.upload(self.alice,self.trip_a)['duplicate'],3)
        # Same transaction IDs may exist in another journey, never cross-linked.
        self.assertEqual(self.upload(self.bob,self.trip_b)['inserted'],3)

    def test_export_file_deletion_keeps_bills_and_record(self):
        bid=self.alice.post(f'/api/trips/{self.trip_a}/bills',dict(title='晚餐',amount='100.01',date='2026-10-01',payer=self.a,kind='shared',participants=[self.a,self.b]))['id']
        export=self.bob.post(f'/api/trips/{self.trip_a}/export',{})
        status,data=self.bob.request(export['url']);self.assertEqual(status,200)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(len([n for n in z.namelist() if n.startswith('xl/worksheets/')]),3)
            self.assertIn('个人汇总',z.read('xl/workbook.xml').decode())
            sheet=ET.fromstring(z.read('xl/worksheets/sheet3.xml'))
            ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            self.assertEqual(sheet.find("m:sheetData/m:row[@r='2']/m:c[@r='D2']/m:v",ns).text,'50')
        self.assertEqual(self.alice.request(export['url'])[0],403)
        records=self.admin.get('/api/admin/records')['records'];self.assertEqual(records[0]['snapshot']['summary']['total'],10001)
        self.assertEqual(self.admin.request(f"/api/admin/files/{export['file_id']}",'DELETE',{})[0],200)
        self.assertEqual(self.bob.request(export['url'])[0],410)
        self.assertEqual(self.alice.get(f'/api/trips/{self.trip_a}')['summary']['total'],10001)
        self.assertEqual(len(self.admin.get('/api/admin/records')['records']),1)
        upload=self.upload(self.alice,self.trip_a)
        self.assertEqual(self.admin.request(f"/api/admin/files/{upload['file_id']}",'DELETE',{})[0],200)
        self.assertEqual(len(self.alice.get(f'/api/trips/{self.trip_a}')['bills']),4)

    def test_accounts_sessions_csrf_and_upload_guards(self):
        self.assertEqual(self.alice.request(f'/api/trips/{self.trip_a}/bills','POST',{},csrf=False)[0],403)
        self.assertEqual(self.alice.request(f'/api/trips/{self.trip_a}/bills','POST',{},origin=False)[0],403)
        # Rejected uploads must return a readable error even with a queued body.
        status, error = self.alice.request(f'/api/trips/{self.trip_a}/upload','POST',{'data':'x'*65536},origin=False)
        self.assertEqual(status,403)
        self.assertEqual(error['error'],'请求来源无效')
        self.assertEqual(self.alice.request('/data/travel-aa.sqlite3')[0],404)
        self.assertEqual(self.alice.request('/web_server.py')[0],404)
        self.assertEqual(self.admin.request('/api/setup','POST',dict(username='other',name='other',password='unit-test-other-password'))[0],403)
        self.assertEqual(len(self.admin.get('/api/admin/users')['users']),4)
        self.assertEqual(self.alice.request(f'/api/trips/{self.trip_a}/upload','POST',dict(name='../evil.csv',data=base64.b64encode(SAMPLE).decode()))[0],400)
        self.assertEqual(self.alice.request(f'/api/trips/{self.trip_b}/upload','POST',dict(name='ok.csv',data=base64.b64encode(SAMPLE).decode()))[0],403)
        self.admin.request(f'/api/admin/users/{self.a}','PATCH',dict(active=False))
        self.assertEqual(self.alice.request('/api/trips')[0],401)
        self.admin.request(f'/api/admin/users/{self.a}','PATCH',dict(active=True,password='unit-test-new-password'))
        self.assertEqual(Client(self.server.server_port).request('/api/login','POST',dict(username='alice',password='unit-test-alice-password'))[0],401)
        new=Client(self.server.server_port);new.post('/api/login',dict(username='alice',password='unit-test-new-password'))
        self.assertEqual(len(new.get('/api/trips')['trips']),1)
        with closing(sqlite3.connect(self.server.app.dbpath)) as db:
            hashed=db.execute('SELECT password FROM users WHERE id=?',(self.a,)).fetchone()[0]
            self.assertNotIn('unit-test-new-password',hashed);self.assertEqual(len(hashed.split(':')),2)
            for token, in db.execute('SELECT token FROM sessions'):self.assertEqual(len(token),64)
        anonymous=Client(self.server.server_port)
        for _ in range(5):self.assertEqual(anonymous.request('/api/login','POST',dict(username='unknown',password='bad'))[0],401)
        self.assertEqual(anonymous.request('/api/login','POST',dict(username='unknown',password='bad'))[0],429)

    def test_legacy_migration_is_explicit_and_idempotent(self):
        old=dict(id='legacy-1',title='旧晚餐',payer='old-a',cents=10000,participants=['old-a','old-b'])
        body=dict(mapping={'old-a':self.a,'old-b':self.b},bills=[old],date='2026-10-01')
        self.assertEqual(self.alice.post(f'/api/trips/{self.trip_a}/migrate',body)['inserted'],1)
        self.assertEqual(self.alice.post(f'/api/trips/{self.trip_a}/migrate',body)['duplicate'],1)
        self.assertEqual(self.bob.request(f'/api/trips/{self.trip_a}/migrate','POST',body)[0],403)
        self.assertEqual(self.alice.get(f'/api/trips/{self.trip_a}')['summary']['total'],10000)
        self.assertEqual(self.bob.get(f'/api/trips/{self.trip_b}')['summary']['total'],0)

class DeploymentTests(unittest.TestCase):
    def test_default_admin_login_and_restart_preserves_password(self):
        with tempfile.TemporaryDirectory(prefix='travel-aa-test-',dir=Path(__file__).parent/'verification') as directory, patch.dict(os.environ, {'TRAVEL_AA_ADMIN_USERNAME':'seed_admin','TRAVEL_AA_ADMIN_PASSWORD':'seedpw6'}):
            server=make_server(port=0,data_dir=directory)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client(server.server_port)
                self.assertFalse(client.get('/api/me')['setup_required'])
                client.post('/api/login',dict(username='seed_admin',password='seedpw6'))
                self.assertEqual(client.get('/api/me')['user']['role'],'admin')
                client.post('/api/password',dict(current='seedpw6',password='changed123'))
            finally:
                server.shutdown();server.server_close();thread.join()
            app=App(directory)
            with app.db() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0],1)
                user=db.execute("SELECT * FROM users WHERE username='seed_admin'").fetchone()
                self.assertTrue(password_matches('changed123',user['password']))
                self.assertFalse(password_matches('seedpw6',user['password']))
                self.assertNotEqual(user['password'],'changed123')

    def test_default_admin_does_not_replace_existing_installation(self):
        with tempfile.TemporaryDirectory(prefix='travel-aa-test-',dir=Path(__file__).parent/'verification') as directory:
            app=App(directory,bootstrap_admin=False)
            hashed=password_hash('existing123')
            with app.db() as db:
                db.execute("INSERT INTO users(username,name,password,role) VALUES(?,?,?,'admin')",('owner','原管理员',hashed))
            app=App(directory)
            with app.db() as db:
                rows=db.execute('SELECT username,password FROM users').fetchall()
                self.assertEqual(len(rows),1);self.assertEqual(rows[0]['username'],'owner')
                self.assertEqual(rows[0]['password'],hashed)

    def test_proxy_cannot_initialize_admin_and_public_origin_requires_https(self):
        with tempfile.TemporaryDirectory(prefix='travel-aa-test-',dir=Path(__file__).parent/'verification') as directory:
            server=make_server(port=0,data_dir=directory,public_hosts=['192.0.2.10'],secure_cookie=True,trust_proxy=True,bootstrap_admin=False)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                client=Client(server.server_port)
                local=client.get('/api/me')
                self.assertTrue(local['setup']);self.assertTrue(local['csrf'])
                proxy={'Host':'192.0.2.10','X-Forwarded-For':'203.0.113.1'}
                status,public=client.request('/api/me',extra_headers=proxy)
                self.assertEqual(status,200);self.assertFalse(public['setup']);self.assertTrue(public['setup_required']);self.assertFalse(public['csrf'])
                status,bare=client.request('/api/me',extra_headers={'Host':'192.0.2.10'})
                self.assertFalse(bare['setup']);self.assertFalse(bare['csrf'])
                local=client.get('/api/me')
                body={'username':'admin','name':'管理员','password':'abc123'}
                proxy['Origin']='https://192.0.2.10'
                self.assertEqual(client.request('/api/setup','POST',body,extra_headers=proxy)[0],403)
                self.assertEqual(client.request('/api/setup','POST',body,extra_headers={'Host':'192.0.2.10','Origin':'https://192.0.2.10'})[0],403)
                self.assertEqual(client.request('/api/login','POST',body,extra_headers={**proxy,'Origin':'http://192.0.2.10'})[0],403)
                client.post('/api/setup',body)
                self.assertFalse(client.get('/api/me')['setup_required'])
                conn=http.client.HTTPConnection('127.0.0.1',server.server_port)
                conn.request('POST','/api/login',json.dumps(body),{'Host':'192.0.2.10','Origin':'https://192.0.2.10','Content-Type':'application/json','X-Forwarded-For':'203.0.113.2'})
                response=conn.getresponse();response.read()
                self.assertEqual(response.status,200);self.assertIn('Secure',response.getheader('Set-Cookie'));conn.close()
            finally:
                server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main(verbosity=2)
