"""Explicit HWPX typography; avoid inheriting outline numbering from prior headings."""
from collections import defaultdict,deque
import copy
import re
import zipfile
from pathlib import Path
from lxml import etree as E

NS={'hh':'http://www.hancom.co.kr/hwpml/2011/head','hp':'http://www.hancom.co.kr/hwpml/2011/paragraph','hc':'http://www.hancom.co.kr/hwpml/2011/core'}
def q(prefix,name):return '{'+NS[prefix]+'}'+name

def style_hwpx(path,blocks):
    path=Path(path)
    with zipfile.ZipFile(path) as z:
        entries={i.filename:(i,z.read(i.filename)) for i in z.infolist()}
    header=E.fromstring(entries['Contents/header.xml'][1])
    chars=header.find('.//hh:charProperties',NS);paras=header.find('.//hh:paraProperties',NS)
    base_char=chars.find('hh:charPr',NS);base_para=paras.find('hh:paraPr',NS)
    ci=max(int(x.get('id')) for x in chars)+1;pi=max(int(x.get('id')) for x in paras)+1
    formats={}
    for kind,size,bold in [('body',1100,False),('h1',2200,True),('h2',1550,True),('h3',1350,True),('subtitle',1150,True),('caption',900,False)]:
        c=copy.deepcopy(base_char);c.set('id',str(ci));c.set('height',str(size))
        for node in list(c):
            if node.tag==q('hh','bold'):c.remove(node)
        if bold:E.SubElement(c,q('hh','bold'))
        p=copy.deepcopy(base_para);p.set('id',str(pi))
        p.find('hh:align',NS).set('horizontal','CENTER' if kind in ('h1','subtitle') else 'LEFT')
        heading=p.find('hh:heading',NS);heading.set('type','NONE');heading.set('idRef','0');heading.set('level','0')
        setting=p.find('hh:breakSetting',NS);setting.set('keepWithNext','1' if kind in ('h1','h2','h3','subtitle') else '0');setting.set('widowOrphan','1')
        for margin in p.findall('.//hh:margin',NS):
            for child in margin:
                child.set('value','800' if child.tag==q('hc','next') else '0')
        for spacing in p.findall('.//hh:lineSpacing',NS):spacing.set('type','PERCENT');spacing.set('value','150')
        chars.append(c);paras.append(p);formats[kind]=(str(ci),str(pi));ci+=1;pi+=1
    chars.set('itemCnt',str(len(chars)));paras.set('itemCnt',str(len(paras)))
    kinds=defaultdict(deque)
    for kind,payload in blocks:
        if isinstance(payload,str):kinds[payload].append(kind if kind in formats else 'body')
    for name,(info,raw) in list(entries.items()):
        if not re.fullmatch(r'Contents/section\d+\.xml',name):continue
        sec=E.fromstring(raw);pending_break=False
        for p in sec.findall('hp:p',NS):
            text=''.join(p.itertext())
            if text=='【페이지 나눔】':sec.remove(p);pending_break=True;continue
            kind=kinds[text].popleft() if kinds.get(text) else 'body'
            char_id,para_id=formats[kind]
            p.set('styleIDRef','0');p.set('paraPrIDRef',para_id)
            if pending_break:p.set('pageBreak','1');pending_break=False
            for run in p.findall('hp:run',NS):run.set('charPrIDRef',char_id)
            # Nested table paragraphs must not inherit a preceding heading either.
            for nested in p.findall('.//hp:p',NS):
                nested.set('styleIDRef','0');nested.set('paraPrIDRef',formats['body'][1])
                for run in nested.findall('hp:run',NS):run.set('charPrIDRef',formats['body'][0])
            for t in p.findall('.//hp:t',NS):
                if t.text:t.text=re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)',r'\1 (\2)',t.text)
            # Cached line segments from the starter document do not describe the new typography.
            for child in p.findall('hp:linesegarray',NS):p.remove(child)
        entries[name]=(info,E.tostring(sec,encoding='UTF-8',xml_declaration=True))
    info=entries['Contents/header.xml'][0]
    entries['Contents/header.xml']=(info,E.tostring(header,encoding='UTF-8',xml_declaration=True))
    temp=path.with_suffix('.hwpx.part')
    try:
        with zipfile.ZipFile(temp,'w') as z:
            for name,(info,raw) in entries.items():z.writestr(info,raw)
        with zipfile.ZipFile(temp) as z:
            assert z.testzip() is None
            for name in z.namelist():
                if name.endswith('.xml'):E.fromstring(z.read(name))
        temp.replace(path)
    finally:temp.unlink(missing_ok=True)

