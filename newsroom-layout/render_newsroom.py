"""Company-scoped newsroom PDF renderer, 2026-11-24 revision 2.2.

Preserves manuscript content, balances columns, keeps figures with captions,
and blocks default publication of sparse continuation pages. No network calls.
"""
import argparse
import copy
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
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, HRFlowable
from reportlab.platypus.flowables import BalancedColumns, KeepInFrame

INTERNAL_NOTE_RE=re.compile(r'^\s*[(【\[]?\s*(이미지\s*생성|이미지\s*생성용|생성)\s*프롬프트|^\s*(프롬프트|prompt)\s*[:：]|^\s*내부\s*(지시|메모|기록|참고)')
PRODUCTION_DIRECTIVE_RE=re.compile(r'^\s*(?:#{1,6}\s*)?(?:\*\*)?[\[【〔]\s*(?:이미지|사진)\s*(?:생성\s*)?(?:지시|프롬프트)\s*[\]】〕]')

def parse(text):
    lines=text.replace('\r\n','\n').splitlines(); result=[]; i=0
    while i<len(lines):
        line=lines[i].strip(); i+=1
        if not line:continue
        if line=='<!-- pagebreak -->':
            if result and result[-1][0]=='break':raise ValueError('연속 pagebreak — 빈 면을 만듭니다. 불필요한 면 나눔을 제거하세요.')
            result.append(('break',''));continue
        if re.fullmatch(r'-{3,}',line):result.append(('rule',''));continue
        m=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
        if m:result.append(('image',(m[1],m[2])));continue
        m=re.match(r'^(#{1,3})\s+(.+)',line)
        if m:result.append(('h'+str(len(m[1])),m[2]));continue
        if line.startswith('- ') or line.startswith('· '):
            result.append(('li',line[2:].strip()));continue
        if line.startswith('|') and line.endswith('|'):
            rows=[line]
            while i<len(lines) and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip());i+=1
            cells=[[c.strip() for c in r.strip('|').split('|')] for r in rows]
            cells=[r for r in cells if not all(re.fullmatch(r':?-{2,}:?',c) for c in r)]
            if cells:result.append(('table',cells))
            continue
        if line.startswith('> '):result.append(('quote',line[2:]));continue
        if re.fullmatch(r'\*\*.+\*\*',line):result.append(('deck',line[2:-2]));continue
        paragraph=[line]
        while i<len(lines) and lines[i].strip() and not re.match(r'^(#|\||>|!\[|<!--|---|\*\*|-\s|·\s)',lines[i].strip()):
            paragraph.append(lines[i].strip());i+=1
        result.append(('body',' '.join(paragraph)))
    if result and result[-1][0]=='break':raise ValueError('마지막 블록이 pagebreak — 거의 빈 마지막 면을 만듭니다. 제거하세요.')
    return result

def inline(text):
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

class TrackingDoc(BaseDocTemplate):
    def __init__(self,*a,**kw):
        self.layout_columns=kw.pop('layout_columns',2)
        BaseDocTemplate.__init__(self,*a,**kw)
        self.page_fill=[]
    def beforePage(self):
        self._column_fill=[0.0]*self.layout_columns
    def afterFlowable(self,flowable):
        # BalancedColumns draws each actual column through KeepInFrame, then
        # restores the original full-width frame. Observe the drawn columns,
        # not the reset frame at afterPage(). Headers are drawn separately.
        if isinstance(flowable,KeepInFrame):
            fr=self.frame
            index=round((fr._x1-self._layout_left)/(self._layout_width/self.layout_columns))
            if 0<=index<self.layout_columns:
                used=max(0.0,self._layout_top-fr._y)
                self._column_fill[index]=max(self._column_fill[index],min(1.0,used/self._body_height))
    def afterPage(self):
        self.page_fill.append([round(v,3) for v in self._column_fill])

def keep_headings(flowables,width,max_height):
    """Keep a heading with the opening lines, while long text remains splittable.

    BalancedColumns does not apply DocTemplate's keepWithNext grouping itself.
    Do not use KeepTogether here: its split exposes the heading again.
    """
    result=[];i=0
    while i<len(flowables):
        item=flowables[i];i+=1
        if not isinstance(item,Paragraph) or not item.getKeepWithNext():
            result.append(item);continue
        heading=[item]
        while i<len(flowables) and isinstance(flowables[i],Paragraph) and flowables[i].getKeepWithNext():
            heading.append(flowables[i]);i+=1
        if i>=len(flowables):
            raise ValueError('소제목 뒤에 본문이 없습니다. 원고를 확인하세요.')
        lead=flowables[i];i+=1;rest=[]
        if isinstance(lead,Paragraph):
            lead.wrap(width,1000000)
            fragments=lead.split(width,2*lead.style.leading+.1)
            if fragments:
                lead=fragments[0];rest=fragments[1:]
                if rest:
                    lead.style=copy.copy(lead.style);lead.style.spaceAfter=0
        elif isinstance(lead,Table):
            lead.wrap(width,1000000)
            fragments=lead.split(width,sum(lead._rowHeights[:2])+.1)
            if fragments:lead=fragments[0];rest=fragments[1:]
        result.append(KeepInFrame(width,max_height,heading+[lead],mode='error',hAlign='LEFT'))
        result.extend(rest)
    return result

def build(source,output,style_name,fonts,roots,label='르테 뉴스 편집국'):
    text=source.read_text(encoding='utf-8-sig')
    if not text.strip() or len(text)>256000:raise ValueError('원고가 비었거나 256KB 한도를 넘습니다.')
    if any(PRODUCTION_DIRECTIVE_RE.match(ln) for ln in text.splitlines()):
        raise ValueError('독자 원고에 [이미지 지시]/[사진 지시] 제작 표식이 있습니다. 해당 제작 문단 전체를 별도 내부 기록으로 옮기고 독자 원고에서 제거하세요. 표식만 지우거나 다른 지면에 인쇄하지 마세요.')
    blocks=parse(text)
    if not blocks or blocks[0][0]!='h1':raise ValueError('원고 첫 블록에 # 제목이 필요합니다.')
    title=blocks.pop(0)[1]
    if len(title)>180:raise ValueError('첫 제목은 180자 이내로 다듬어 주세요.')
    for name,filename in [('NR','NanumMyeongjo-Regular.ttf'),('NB','NanumMyeongjo-Bold.ttf'),('NS','NanumGothic-Regular.ttf'),('NSB','NanumGothic-Bold.ttf')]:
        pdfmetrics.registerFont(TTFont(name,str(fonts/filename)))
    pdfmetrics.registerFontFamily('NR',normal='NR',bold='NB',italic='NR',boldItalic='NB')
    pdfmetrics.registerFontFamily('NS',normal='NS',bold='NSB',italic='NS',boldItalic='NSB')
    cfg={'classic':(A3,3,11.3,16.9,34,'#fcfbf7','르테 주간'),
         'weekly':(A4,2,10.5,15.5,27,'#fffefa','르테 위클리'),
         'magazine':(A4,2,11.2,17.4,33,'#fffafc','르테 매거진')}[style_name]
    (w,h),cols,fs,leading,ts,paper,masthead=cfg
    margin=38
    gap=22;full=w-2*margin;cw=(full-gap*(cols-1))/cols
    red=HexColor('#8d1827');ink=HexColor('#1b2027');grey=HexColor('#59616a')
    def st(name,size=fs,font='NR',**kw):
        kw.setdefault('spaceAfter',9)
        return ParagraphStyle(name,fontName=font,fontSize=size,leading=kw.pop('leading',size*1.5),textColor=ink,wordWrap='CJK',allowWidows=0,allowOrphans=0,**kw)
    styles={'body':st('body',leading=leading),'h1':st('h1',18,'NB',keepWithNext=True,spaceBefore=13),
            'h2':st('h2',17 if cols>1 else 21,'NB',keepWithNext=True,spaceBefore=13),
            'h3':st('h3',14 if cols>1 else 17,'NSB',keepWithNext=True,spaceBefore=10),
            'deck':st('deck',fs,'NSB',keepWithNext=True),
            'quote':st('quote',fs+1,'NR',leftIndent=10,borderPadding=7,borderColor=red,borderWidth=.6),
            'li':st('li',leading=leading,leftIndent=12,bulletIndent=2,spaceAfter=4),
            'caption':st('caption',8.2,'NS'),
            'cell':st('cell',max(8,fs-2),'NS',leading=12),
            'title':st('title',ts,'NB',leading=ts*1.25)}
    styles['title'].wordWrap=None
    styles['title'].splitLongWords=False
    titlep=Paragraph(inline(title),styles['title']);_,th=titlep.wrap(full,1000)
    if ts*1.25<th<=ts*2.51:
        candidates=[]
        for split in (m.start() for m in re.finditer(' ',title)):
            left,right=title[:split],title[split+1:]
            lw=pdfmetrics.stringWidth(left,'NB',ts);rw=pdfmetrics.stringWidth(right,'NB',ts)
            if max(lw,rw)<=full:candidates.append((abs(lw-rw),left,right))
        if candidates:
            _,left,right=min(candidates)
            titlep=Paragraph(inline(left)+'<br/>'+inline(right),styles['title']);_,th=titlep.wrap(full,1000)
    first_top=h-118-th
    if first_top<200:raise ValueError('제목이 너무 길어 본문 공간이 부족합니다.')
    def canvas_header(c,doc):
        doc._layout_top=first_top if doc.page==1 else h-58
        doc._body_height=doc._layout_top-46
        doc._layout_left=margin
        doc._layout_width=full
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
        return [Frame(margin,46,full,top-46,id=f'{prefix}-0',leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)]
    doc=TrackingDoc(str(output),pagesize=(w,h),title=title,author=label,pageCompression=1,layout_columns=cols)
    doc.addPageTemplates([PageTemplate(id='front',frames=frames(first_top,'front'),onPage=canvas_header,autoNextPageTemplate='inside'),PageTemplate(id='inside',frames=frames(h-58,'inside'),onPage=canvas_header)])
    segments=[];flow=[];images=0;max_image_height=max(120,min(first_top-110,300))
    for kind,payload in blocks:
        if kind=='break':
            if flow:segments.append(flow);flow=[]
            continue
        elif kind=='rule':flow.append(HRFlowable(width='100%',thickness=.5,color=grey,spaceBefore=5,spaceAfter=10))
        elif kind=='image':
            caption,raw=payload;p=scoped_path(raw,[source.parent,*roots])
            from PIL import Image as PILImage
            with PILImage.open(p) as im:iw,ih=im.size
            scale=min(cw/iw,max_image_height/ih)
            fig=Image(str(p),width=iw*scale,height=ih*scale,hAlign='LEFT')
            bundle=[fig,Spacer(1,5)]
            if caption:bundle.append(Paragraph(inline(caption),styles['caption']))
            # KeepTogether.split() can expose the image and caption separately
            # to BalancedColumns. An atomic, non-shrinking bundle keeps them
            # in the same column and fails if a caption truly cannot fit.
            flow.append(KeepInFrame(cw,first_top-46,bundle,mode='error',hAlign='LEFT'));images+=1
        elif kind=='li':
            flow.append(Paragraph(inline(payload),styles['li'],bulletText='•'))
        elif kind=='table':
            n=max(len(r) for r in payload)
            if n>8:raise ValueError('표는 8열 이내로 나눠 주세요.')
            rows=[[Paragraph(inline(v),styles['cell']) for v in r+['']*(n-len(r))] for r in payload]
            table=Table(rows,colWidths=[cw/n]*n,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5),('BACKGROUND',(0,0),(-1,0),HexColor('#eee9e2')),('LINEBELOW',(0,0),(-1,0),.8,red),('LINEBELOW',(0,1),(-1,-1),.3,HexColor('#b9bdc1'))]))
            flow.extend([table,Spacer(1,10)])
        else:flow.append(Paragraph(inline(payload),styles[kind]))
    if flow:segments.append(flow)
    if not segments:raise ValueError('제목만 있는 원고는 발행하지 않습니다.')
    story=[]
    for si,seg in enumerate(segments):
        seg=keep_headings(seg,cw,h-104)
        if cols>1:
            story.append(BalancedColumns(list(seg),nCols=cols,needed=60,spaceBefore=0,spaceAfter=0,leftPadding=0,rightPadding=0,innerPadding=gap,topPadding=0,bottomPadding=0,endSlack=0.05))
        else:
            story.extend(seg)
        if si<len(segments)-1:story.append(PageBreak())
    doc.build(story)
    warnings=[]
    for pi,ratios in enumerate(doc.page_fill):
        if pi>0 and sum(ratios)/cols<0.18:warnings.append(f'sparse_page_{pi+1}')
    hits=[ln.strip()[:120] for ln in text.splitlines() if INTERNAL_NOTE_RE.match(ln)]
    return {'style':style_name,'pages':doc.page,'images':images,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'filename':output.name,'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest(),'visual_review':'required','renderer':'newsroom-20261124-v2.3','layout':{'page_fill':doc.page_fill,'warnings':warnings},'checks':{'internal_note_hits':hits[:5]}}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('source');ap.add_argument('style',choices=['classic','weekly','magazine']);ap.add_argument('name',nargs='?')
    ap.add_argument('--config',type=Path)
    ap.add_argument('--allow-sparse-pages',action='store_true',help='사용자가 짧은 독립면을 명시적으로 요청한 경우에만 사용')
    a=ap.parse_args()
    config=a.config or Path(__file__).resolve().parents[4]/'newsroom-assets/config.json'
    c=json.loads(config.read_text(encoding='utf-8'));roots=[Path(p).resolve() for p in c['input_roots']]
    source=scoped_path(a.source,roots)
    if source.suffix.lower() not in ('.md','.txt','.markdown'):raise ValueError('마크다운 원고만 지원합니다.')
    source_raw=source.read_bytes();source_sha=hashlib.sha256(source_raw).hexdigest()  # build 전 입력 원고 캡처
    name=re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',a.name or source.stem+'-'+a.style)[:100].removesuffix('.pdf')
    out=Path.cwd()/'output';out.mkdir(exist_ok=True)
    target=out/(name+'.pdf');temporary=out/(name+'.part')
    try:
        receipt=build(source,temporary,a.style,Path(c['fonts']),roots,c.get('label','르테 뉴스 편집국'))
        if receipt['layout']['warnings'] and not a.allow_sparse_pages:
            raise ValueError('거의 빈 면이 감지됐습니다: '+', '.join(receipt['layout']['warnings'])+'. 불필요한 면 나눔·내부 편집 기록을 정리하고 원고를 다시 검수하세요. 짧은 독립면을 사용자가 명시한 경우만 --allow-sparse-pages를 사용합니다.')
        if receipt.get('source_sha256')!=source_sha or hashlib.sha256(source.read_bytes()).hexdigest()!=source_sha:
            raise ValueError('조판 중 입력 원고가 변경됐습니다. 출력 없이 중단합니다.')
        receipt['source_sha256']=source_sha  # 재조회SHA 일치검사 통과 후 영수증에 기록
        temporary.replace(target);receipt['filename']=target.name
        # 검수·pin 결속: build 전 캡처한 원본 원고를 PDF와 함께 동시 산출(v2.3 조판 로직 무변경)
        md_target=out/(name+'.md');md_target.write_bytes(source_raw)
        receipt['manuscript_md']={'filename':md_target.name,'bytes':md_target.stat().st_size,'sha256':source_sha}
    except Exception:
        temporary.unlink(missing_ok=True);raise
    logs=Path.cwd()/'receipts';logs.mkdir(exist_ok=True)
    (logs/(name+'.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False))

if __name__=='__main__':main()
