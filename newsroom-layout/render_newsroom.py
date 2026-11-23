"""File-based, company-scoped Korean newsroom PDF renderer. No network calls."""
import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, HRFlowable, KeepTogether


def parse(text):
    lines=text.replace('\r\n','\n').splitlines(); result=[]; i=0
    while i<len(lines):
        line=lines[i].strip(); i+=1
        if not line:continue
        if line=='<!-- pagebreak -->':result.append(('break',''));continue
        if re.fullmatch(r'-{3,}',line):result.append(('rule',''));continue
        m=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
        if m:result.append(('image',(m[1],m[2])));continue
        m=re.match(r'^(#{1,3})\s+(.+)',line)
        if m:result.append(('h'+str(len(m[1])),m[2]));continue
        if line.startswith('|') and line.endswith('|'):
            rows=[line]
            while i<len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip());i+=1
            cells=[[c.strip() for c in r.strip('|').split('|')] for r in rows]
            cells=[r for r in cells if not all(re.fullmatch(r':?-{2,}:?',c) for c in r)]
            result.append(('table',cells));continue
        if line.startswith('> '):result.append(('quote',line[2:]));continue
        if re.fullmatch(r'\*\*.+\*\*',line):result.append(('deck',line[2:-2]));continue
        paragraph=[line]
        while i<len(lines) and lines[i].strip() and not re.match(r'^(#|\||>|!\[|<!--|---|\*\*)',lines[i].strip()):
            paragraph.append(lines[i].strip());i+=1
        result.append(('body',' '.join(paragraph)))
    return result


def inline(text):
    # Escape manuscript HTML; only explicit Markdown links/bold are interpreted.
    text=text.replace('−','-').replace('▸','›')
    chunks=[];pos=0
    pattern=r'\[([^\]]+)\]\((https?://[^\s)]+)\)|\*\*([^*]+)\*\*|https?://[^\s<>]+'
    for m in re.finditer(pattern,text):
        chunks.append(html.escape(text[pos:m.start()]))
        if m[1]:chunks.append('<link href="'+html.escape(m[2],quote=True)+'" color="#8d1827">'+html.escape(m[1])+'</link>')
        elif m[3]:chunks.append('<b>'+html.escape(m[3])+'</b>')
        else:
            url=m[0];label=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(url).netloc
            chunks.append('<link href="'+html.escape(url,quote=True)+'" color="#8d1827">'+html.escape(label)+' 원문</link>')
        pos=m.end()
    chunks.append(html.escape(text[pos:]))
    return ''.join(chunks)


def scoped_path(value,roots):
    p=Path(value)
    candidates=[p] if p.is_absolute() else [r/p for r in roots]
    found=[]
    for c in candidates:
        c=c.resolve()
        if c.is_file() and any(c.is_relative_to(r.resolve()) for r in roots):found.append(c)
    found=list(dict.fromkeys(found))
    if len(found)!=1:raise ValueError('파일이 없거나 경로가 모호합니다. 현재 편집국의 정확한 파일 경로를 지정하세요: '+value[:160])
    return found[0]


def build(source,output,style_name,fonts,roots,label='르테 뉴스 편집국'):
    text=source.read_text(encoding='utf-8-sig')
    if not text.strip() or len(text)>256000:raise ValueError('원고가 비었거나 256KB 한도를 넘습니다.')
    blocks=parse(text)
    if not blocks or blocks[0][0]!='h1':raise ValueError('원고 첫 블록에 # 제목이 필요합니다.')
    title=blocks.pop(0)[1]
    if len(title)>180:raise ValueError('첫 제목은 180자 이내로 다듬어 주세요.')
    for name,filename in [('NR','NanumMyeongjo-Regular.ttf'),('NB','NanumMyeongjo-Bold.ttf'),('NS','NanumGothic-Regular.ttf'),('NSB','NanumGothic-Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name,str(fonts/filename)))
    pdfmetrics.registerFontFamily('NR',normal='NR',bold='NB',italic='NR',boldItalic='NB')
    pdfmetrics.registerFontFamily('NS',normal='NS',bold='NSB',italic='NS',boldItalic='NSB')
    cfg={'classic':(A3,3,11.3,16.9,34,'#fcfbf7','르테 주간'),
         'weekly':(landscape(A4),3,10.5,15.5,29,'#ffffff','르테 위클리'),
         'magazine':(A4,1,12.2,19.2,33,'#fffaf6','RTE  /  INSIGHT')}[style_name]
    (w,h),cols,fs,leading,ts,paper,masthead=cfg
    margin=38 if style_name!='magazine' else 50
    gap=22;full=w-2*margin;cw=(full-gap*(cols-1))/cols
    red=HexColor('#8d1827');ink=HexColor('#1b2027');grey=HexColor('#59616a')
    def st(name,size=fs,font='NR',**kw):
        return ParagraphStyle(name,fontName=font,fontSize=size,leading=kw.pop('leading',size*1.5),textColor=ink,wordWrap='CJK',allowWidows=0,allowOrphans=0,spaceAfter=9,**kw)
    styles={'body':st('body',leading=leading),'h1':st('h1',18,'NB',keepWithNext=True,spaceBefore=13),
            'h2':st('h2',17 if cols>1 else 21,'NB',keepWithNext=True,spaceBefore=13),
            'h3':st('h3',14 if cols>1 else 17,'NSB',keepWithNext=True,spaceBefore=10),
            'deck':st('deck',fs,'NSB',keepWithNext=True),
            'quote':st('quote',fs+1,'NR',leftIndent=10,borderPadding=7,borderColor=red,borderWidth=.6),
            'caption':st('caption',8.2,'NS'),
            'cell':st('cell',max(8,fs-2),'NS',leading=12),
            'title':st('title',ts,'NB',leading=ts*1.25)}
    styles['title'].wordWrap=None
    styles['title'].splitLongWords=False
    titlep=Paragraph(inline(title),styles['title']);_,th=titlep.wrap(full,1000)
    first_top=h-118-th
    if first_top<200:raise ValueError('제목이 너무 길어 본문 공간이 부족합니다.')
    def canvas_header(c,doc):
        c.saveState();c.setFillColor(HexColor(paper));c.rect(0,0,w,h,fill=1,stroke=0)
        c.setFillColor(red);c.setFont('NB' if style_name=='classic' else 'NSB',28 if doc.page==1 else 12)
        c.drawString(margin,h-(52 if doc.page==1 else 32),masthead)
        c.setStrokeColor(ink);c.setLineWidth(1.1);c.line(margin,h-(66 if doc.page==1 else 44),w-margin,h-(66 if doc.page==1 else 44))
        if doc.page==1:titlep.drawOn(c,margin,h-86-th)
        c.setStrokeColor(HexColor('#b9bdc1'));c.setLineWidth(.4);c.line(margin,33,w-margin,33)
        c.setFillColor(grey);c.setFont('NS',8);c.drawString(margin,21,label);c.drawRightString(w-margin,21,str(doc.page))
        if cols>1:
            top=first_top if doc.page==1 else h-58
            for j in range(1,cols):
                x=margin+j*cw+(j-.5)*gap;c.line(x,46,x,top)
        c.restoreState()
    def frames(top,prefix):
        return [Frame(margin+i*(cw+gap),46,cw,top-46,id=f'{prefix}-{i}',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0) for i in range(cols)]
    doc=BaseDocTemplate(str(output),pagesize=(w,h),title=title,author=label,pageCompression=1)
    doc.addPageTemplates([PageTemplate(id='front',frames=frames(first_top,'front'),onPage=canvas_header,autoNextPageTemplate='inside'),PageTemplate(id='inside',frames=frames(h-58,'inside'),onPage=canvas_header)])
    story=[];images=0;max_image_height=max(120,min(first_top-110,300))
    for kind,payload in blocks:
        if kind=='break':story.append(PageBreak())
        elif kind=='rule':story.append(HRFlowable(width='100%',thickness=.5,color=grey,spaceBefore=5,spaceAfter=10))
        elif kind=='image':
            caption,raw=payload;p=scoped_path(raw,[source.parent,*roots])
            from PIL import Image as PILImage
            with PILImage.open(p) as im:iw,ih=im.size
            scale=min(cw/iw,max_image_height/ih)
            fig=Image(str(p),width=iw*scale,height=ih*scale,hAlign='LEFT')
            bundle=[fig,Spacer(1,5)]
            if caption:bundle.append(Paragraph(inline(caption),styles['caption']))
            story.append(KeepTogether(bundle));images+=1
        elif kind=='table':
            n=max(len(r) for r in payload)
            if n>8:raise ValueError('표는 8열 이내로 나눠 주세요.')
            rows=[[Paragraph(inline(v),styles['cell']) for v in r+['']*(n-len(r))] for r in payload]
            table=Table(rows,colWidths=[cw/n]*n,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('BACKGROUND',(0,0),(-1,0),HexColor('#eee9e2')),('LINEBELOW',(0,0),(-1,0),.8,red),('LINEBELOW',(0,1),(-1,-1),.3,HexColor('#b9bdc1'))]))
            story.extend([table,Spacer(1,10)])
        else:story.append(Paragraph(inline(payload),styles[kind]))
    if not story:raise ValueError('제목만 있는 원고는 발행하지 않습니다.')
    doc.build(story)
    return {'style':style_name,'pages':doc.page,'images':images,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'filename':output.name,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'visual_review':'required'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('source');ap.add_argument('style',choices=['classic','weekly','magazine']);ap.add_argument('name',nargs='?')
    ap.add_argument('--config',type=Path)
    a=ap.parse_args()
    config=a.config or Path(__file__).resolve().parents[4]/'newsroom-assets/config.json'
    c=json.loads(config.read_text(encoding='utf-8'));roots=[Path(p).resolve() for p in c['input_roots']]
    source=scoped_path(a.source,roots)
    if source.suffix.lower() not in ('.md','.txt','.markdown'):raise ValueError('마크다운 원고만 지원합니다.')
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',a.name or source.stem+'-'+a.style)[:100].removesuffix('.pdf')
    out=Path.cwd()/'output';out.mkdir(exist_ok=True)
    target=out/(name+'.pdf');temporary=out/(name+'.part')
    try:
        receipt=build(source,temporary,a.style,Path(c['fonts']),roots,c.get('label','르테 뉴스 편집국'))
        temporary.replace(target);receipt['filename']=target.name
    except Exception:
        temporary.unlink(missing_ok=True);raise
    logs=Path.cwd()/'receipts';logs.mkdir(exist_ok=True)
    (logs/(name+'.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__':main()

