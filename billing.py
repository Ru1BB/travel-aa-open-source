"""Integer-cent settlement, bounded personal statement parsing, XLSX output."""
import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

MAX_FILE = 5 * 1024 * 1024
MAX_ROWS = 10000

def amount(value):
    text = str(value).strip().lstrip('¥￥').strip()
    if not re.fullmatch(r'\d+(\.\d{1,2})?', text):
        raise ValueError('金额必须为正数，最多两位小数')
    cents = int(Decimal(text) * 100)
    if not 0 < cents <= 10000000000:
        raise ValueError('单笔金额须大于零且不超过一亿元')
    return cents

def settle(members, bills):
    rows = [dict(m, paid=0, share=0, balance=0) for m in members]
    lookup = {m['id']: m for m in rows}
    total = 0
    for b in bills:
        if b['kind'] != 'shared':
            continue
        ids = set(b['participants'])
        selected = [r for r in rows if r['id'] in ids]
        if b['payer'] not in lookup or not selected or len(selected) != len(ids):
            raise ValueError('账单成员数据无效')
        total += b['cents']
        lookup[b['payer']]['paid'] += b['cents']
        base, extra = divmod(b['cents'], len(selected))
        for i, row in enumerate(selected):
            row['share'] += base + (i < extra)
    creditors, debtors = [], []
    for r in rows:
        r['balance'] = r['paid'] - r['share']
        if r['balance'] > 0:
            creditors.append([r['id'], r['balance']])
        elif r['balance'] < 0:
            debtors.append([r['id'], -r['balance']])
    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        cents = min(debtors[i][1], creditors[j][1])
        transfers.append(dict(from_id=debtors[i][0], to_id=creditors[j][0], cents=cents))
        debtors[i][1] -= cents
        creditors[j][1] -= cents
        i += debtors[i][1] == 0
        j += creditors[j][1] == 0
    return dict(total=total, rows=rows, transfers=transfers)

def xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('不支持含实体定义的 XML')
    return ET.fromstring(data)

def read_xlsx(data):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = z.infolist()
            if len(entries) > 200 or sum(i.file_size for i in entries) > 20 * 1024 * 1024 or any(i.flag_bits & 1 for i in entries):
                raise ValueError('Excel 压缩内容过大或已加密')
            if any(i.filename.startswith('/') or '..' in i.filename.split('/') for i in entries):
                raise ValueError('Excel 内部路径无效')
            strings = []
            if 'xl/sharedStrings.xml' in z.namelist():
                strings = [''.join(n.itertext()) for n in xml(z.read('xl/sharedStrings.xml')).findall('m:si', ns)]
            book = xml(z.read('xl/workbook.xml'))
            if book.find('m:workbookPr', ns) is not None and book.find('m:workbookPr', ns).get('date1904') in ('1', 'true'):
                raise ValueError('暂不支持 1904 日期系统，请另存为 CSV')
            first = book.find('m:sheets/m:sheet', ns)
            rel_id = first.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            rels = xml(z.read('xl/_rels/workbook.xml.rels'))
            rel = next(r for r in rels if r.get('Id') == rel_id)
            if rel.get('TargetMode') == 'External':
                raise ValueError('不支持外部工作表')
            target = rel.get('Target', '')
            path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
            if '..' in path.split('/') or not path.startswith('xl/worksheets/'):
                raise ValueError('工作表路径无效')
            rows = []
            for node in xml(z.read(path)).findall('m:sheetData/m:row', ns):
                cells = {}
                for c in node.findall('m:c', ns):
                    ref = c.get('r', '')
                    column = re.match(r'[A-Z]+', ref)
                    if not column:
                        raise ValueError('Excel 单元格位置缺失')
                    index = 0
                    for ch in column[0]:
                        index = index * 26 + ord(ch) - 64
                    if index > 100:
                        raise ValueError('Excel 列数过多')
                    if c.find('m:f', ns) is not None:
                        raise ValueError('不接受含公式的账单，请导出为原始数值 CSV')
                    v = c.find('m:v', ns)
                    value = v.text or '' if v is not None else ''
                    if c.get('t') == 's':
                        value = strings[int(value)]
                    elif c.get('t') == 'inlineStr':
                        value = ''.join(c.find('m:is', ns).itertext())
                    cells[index - 1] = value
                rows.append([cells.get(i, '') for i in range(max(cells, default=-1) + 1)])
                if len(rows) > MAX_ROWS + 100:
                    raise ValueError('账单最多支持一万行')
            return rows
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, StopIteration, TypeError) as e:
        raise ValueError('无法解析 XLSX，请使用未加密的个人对账文件') from e

def clean(value):
    return str(value).strip().lstrip('\ufeff').strip().strip('`').strip()

def transaction_time(value):
    text = clean(value)
    try:
        if re.fullmatch(r'\d{5}(\.\d+)?', text):
            # Excel stores naive dates. Treat as China local statement date.
            return (datetime(1899, 12, 30) + timedelta(days=float(text))).strftime('%Y-%m-%d %H:%M:%S')
        parsed = datetime.fromisoformat(text.replace('/', '-'))
        if parsed.tzinfo:
            raise ValueError('账单日期不能含时区，请使用原始本地时间')
        return parsed.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, OverflowError) as e:
        raise ValueError('无法识别交易时间') from e

def parse_statement(filename, data, start, end):
    if len(data) > MAX_FILE or not data:
        raise ValueError('文件须非空且不超过 5 MB')
    ext = filename.lower().rsplit('.', 1)[-1]
    if ext == 'csv':
        text = None
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if text is None or '\x00' in text:
            raise ValueError('CSV 编码不支持，请保存为 UTF-8')
        csv.field_size_limit(MAX_FILE)
        try:
            rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as e:
            raise ValueError('CSV 格式损坏') from e
    elif ext == 'xlsx':
        rows = read_xlsx(data)
    else:
        raise ValueError('只接受 .csv 或 .xlsx；请先解压账单附件，不支持 .xls/截图/ZIP')
    if len(rows) > MAX_ROWS + 100:
        raise ValueError('账单最多支持一万行')
    header = None
    aliases = {'金额（元）': '金额(元)', '金额': '金额(元)', '商品名称': '商品',
               '商品说明': '商品', '交易状态': '当前状态', '交易订单号': '交易单号',
               '商家订单号': '商户单号', '收/支/不计收支': '收/支'}
    required = {'交易时间', '商品', '收/支', '金额(元)'}
    for i, row in enumerate(rows[:100]):
        names = [aliases.get(clean(v), clean(v)) for v in row]
        if required.issubset(names):
            header = {n: j for j, n in enumerate(names)}
            source = 'alipay' if any(clean(v) in ('商品说明', '交易订单号', '交易分类') for v in row) else 'wechat'
            begin = i + 1
            break
    if header is None:
        raise ValueError('未找到微信/支付宝个人账单表头：交易时间、商品或商品说明、收/支、金额；商户 API 账单不支持')
    accepted, warnings = [], []
    counts = dict(outside=0, non_expense=0, invalid=0, refund=0, transfer=0)
    for number, row in enumerate(rows[begin:], begin + 1):
        if not any(clean(v) for v in row):
            continue
        get = lambda key: clean(row[header[key]]) if key in header and header[key] < len(row) else ''
        if not get('交易时间') or not re.match(r'^(\d{4}[-/]\d{2}[-/]\d{2}|\d{5})', get('交易时间')):
            if get('收/支') in ('支出', '收入', '中性交易', '不计收支', '/'):
                counts['invalid'] += 1
                warnings.append(f'第 {number} 行交易时间无效，已跳过')
            continue
        try:
            when = transaction_time(get('交易时间'))
            if not start <= when[:10] <= end:
                counts['outside'] += 1
                continue
            if get('收/支') != '支出':
                counts['non_expense'] += 1
                continue
            status = get('当前状态')
            if any(word in status for word in ('退款', '退还', '失败', '关闭', '撤销')):
                counts['refund'] += 1
                warnings.append(f'第 {number} 行存在退款/失败状态，请核实净支出后手动记账')
                continue
            category = get('交易分类') if source == 'alipay' else get('交易类型')
            if status in ('已转账', '对方已收钱', '已存入零钱') or any(word in category for word in ('转账', '转入', '转出', '红包', '群收款', '充值', '提现', '账户存取', '信用借还', '投资理财', '理财通', '零钱通')):
                counts['transfer'] += 1
                warnings.append(f'第 {number} 行为转账/红包/资金往来，已跳过；如确属旅程消费请核实后手动记账')
                continue
            valid_statuses = {'支付成功', '交易成功', '等待确认收货'}
            if status and status not in valid_statuses:
                counts['invalid'] += 1
                warnings.append(f'第 {number} 行交易状态未确认，已跳过，请核实后手动记账')
                continue
            cents = amount(get('金额(元)'))
            title = (get('商品') or get('交易对方') or '账单消费')[:80]
            accepted.append(dict(title=title, cents=cents, occurred=when, external_id=get('交易单号')[:100], counterparty=get('交易对方')[:100], status=status[:60], source=source))
        except ValueError:
            counts['invalid'] += 1
            warnings.append(f'第 {number} 行日期或金额无效，已跳过')
    return accepted, dict(**counts, parsed=len(accepted), source=source, warnings=warnings[:100])

def xlsx(sheets):
    """Write typed OOXML cells. Never treat uploaded text as formulas."""
    def column(n):
        text = ''
        while n:
            n, r = divmod(n - 1, 26)
            text = chr(65 + r) + text
        return text
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        overrides = ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
        z.writestr('[Content_Types].xml', f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{overrides}</Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + f'<Relationship Id="rId{len(sheets)+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr('xl/styles.xml', f'<styleSheet xmlns="{ns}"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Microsoft YaHei"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF176B56"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf fontId="0"/><xf fontId="1" fillId="2" applyFill="1" applyFont="1"/><xf fontId="0" numFmtId="4" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i, (_, rows) in enumerate(sheets, 1):
            content = []
            for r, row in enumerate(rows, 1):
                cells = []
                for c, v in enumerate(row, 1):
                    ref = f'{column(c)}{r}'
                    if isinstance(v, (int, float, Decimal)):
                        cells.append(f'<c r="{ref}" s="{1 if r == 1 else 0 if isinstance(v, int) else 2}"><v>{v}</v></c>')
                    else:
                        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(v))
                        cells.append(f'<c r="{ref}" t="inlineStr" s="{1 if r == 1 else 0}"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
                content.append(f'<row r="{r}">{"".join(cells)}</row>')
            width = max((len(r) for r in rows), default=1)
            z.writestr(f'xl/worksheets/sheet{i}.xml', f'<worksheet xmlns="{ns}"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="{width}" width="24" customWidth="1"/></cols><sheetData>{"".join(content)}</sheetData><autoFilter ref="A1:{column(width)}{max(len(rows),1)}"/></worksheet>')
    return output.getvalue()
