#!/usr/bin/env python3
"""마크다운 → PDF 변환기 (고르곤 사내 스킬, reportlab·인쇄용 A4).

블록 규약은 hwpx-report와 동일 (md_to_hwpx 파서 재사용):
  # 제목 / ## 면제목 / ### 기사제목 / **부제** / 표 / > 인용 / --- 페이지나눔
  ![캡션](파일경로) · > [사진:파일경로] 캡션 → 이미지 삽입
  > [사진] 지시문 → 사진 자리 표시

인수: 1. 마크다운 원본(위키 경로/절대경로/본문)  2. (선택) 출력 파일명
결과: 실행 폴더 output/<이름>.pdf
"""
import io
import re
import sys
from pathlib import Path

from md_to_hwpx import find_source, parse_markdown, resolve_file

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (Image as RLImage, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, BaseDocTemplate, Frame, PageTemplate, FrameBreak)
from reportlab.lib.utils import ImageReader

# Bundled, unmodified OFL font files; see fonts/OFL.txt and fonts-manifest.json.
from reportlab.pdfbase.ttfonts import TTFont
font_root=Path(__file__).resolve().parent/'fonts'
if (font_root/'NanumGothic-Regular.ttf').is_file() and (font_root/'NanumGothic-Bold.ttf').is_file():
    pdfmetrics.registerFont(TTFont('GorgonNewsBody',str(font_root/'NanumGothic-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('GorgonNewsHead',str(font_root/'NanumGothic-Bold.ttf')))
    SERIF='GorgonNewsBody';SANS='GorgonNewsHead'
else:
    pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
    pdfmetrics.registerFont(UnicodeCIDFont('HYGothic-Medium'))
    SERIF='HYSMyeongJo-Medium';SANS='HYGothic-Medium'


S = {
    'h1': ParagraphStyle('h1', fontName=SANS, fontSize=21, leading=27,
                         alignment=1, spaceAfter=10),
    'h2': ParagraphStyle('h2', fontName=SANS, fontSize=15, leading=20,
                         alignment=1, spaceBefore=8, spaceAfter=8),
    'h3': ParagraphStyle('h3', fontName=SANS, fontSize=13, leading=18,
                         spaceBefore=10, spaceAfter=5),
    'subtitle': ParagraphStyle('subtitle', fontName=SANS, fontSize=10.5, leading=15,
                               alignment=1, textColor='#555555', spaceAfter=8),
    'para': ParagraphStyle('para', fontName=SERIF, fontSize=10, leading=16,
                           spaceAfter=7),
    'quote': ParagraphStyle('quote', fontName=SERIF, fontSize=9.5, leading=14,
                            textColor='#666666', leftIndent=18, spaceAfter=6),
    'caption': ParagraphStyle('caption', fontName=SANS, fontSize=8.5, leading=12,
                              alignment=1, textColor='#444444', spaceAfter=8),
    'ph': ParagraphStyle('ph', fontName=SANS, fontSize=9.5, leading=14,
                         alignment=1, textColor='#888888', spaceBefore=4, spaceAfter=8),
}


def newspaper_document(path, story, masthead):
    """Two-column newspaper with repeated masthead, column rule and page number."""
    from reportlab.lib.colors import HexColor
    width,height=A4; margin=1.5*cm; gutter=0.6*cm
    col=(width-2*margin-gutter)/2
    def page(canvas,doc):
        canvas.saveState()
        canvas.setFillColor(HexColor('#fcfbf7'));canvas.rect(0,0,width,height,fill=1,stroke=0)
        canvas.setFillColor(HexColor('#17202a'))
        size=25
        while size>12 and pdfmetrics.stringWidth(masthead,SANS,size)>width-2*margin:size-=1
        canvas.setFont(SANS,size);canvas.drawCentredString(width/2,height-margin-15,masthead)
        canvas.setFont(SERIF,8);canvas.drawString(margin,height-margin-34,'뉴스편집국 | 출처·자료 날짜를 확인한 요약')
        canvas.drawRightString(width-margin,height-margin-34,'GORGON')
        canvas.setStrokeColor(HexColor('#17202a'));canvas.setLineWidth(1.2)
        canvas.line(margin,height-margin-44,width-margin,height-margin-44)
        canvas.setLineWidth(.35);canvas.setStrokeColor(HexColor('#b7b5ae'))
        canvas.line(width/2,margin+15,width/2,height-margin-55)
        canvas.line(margin,margin+5,width-margin,margin+5)
        canvas.setFont(SANS,8);canvas.drawRightString(width-margin,margin-8,str(doc.page))
        canvas.restoreState()
    frames=[Frame(margin,margin+15,col,height-2*margin-70,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0,id='left'),
            Frame(margin+col+gutter,margin+15,col,height-2*margin-70,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0,id='right')]
    doc=BaseDocTemplate(str(path),pagesize=A4,title=masthead)
    doc.addPageTemplates(PageTemplate(id='newspaper',frames=frames,onPage=page))
    doc.build(story)


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: md_to_pdf.py <원본 경로 또는 본문> [출력 이름]')
    text, default_name = find_source(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else default_name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip()[:120] or 'pdf-출력'

    blocks = parse_markdown(text)
    if not blocks:
        raise SystemExit('변환할 본문이 없습니다.')

    newspaper='--newspaper' in sys.argv[3:]
    content_width=(A4[0]-3*cm-.6*cm)/2 if newspaper else A4[0]-4*cm
    masthead=next((value for kind,value in blocks if kind=='h1'),name)
    if newspaper:
        blocks=[(kind,value) for kind,value in blocks if kind!='h1']
        for style in S.values():style.wordWrap='CJK'
        S['para'].fontSize=9.6;S['para'].leading=15
        S['h2'].fontSize=14;S['h2'].leading=20
        S['h3'].fontSize=12.5;S['h3'].leading=18
    story = []
    n_img = n_ph = 0
    temps = []
    for kind, payload in blocks:
        if newspaper and kind=='para' and payload=='[[COLUMN_BREAK]]':
            story.append(FrameBreak())
        elif kind == 'h1':
            story.append(Paragraph(payload, S['h1']))
        elif kind == 'h2':
            story.append(Paragraph(payload, S['h2']))
        elif kind == 'h3':
            story.append(Paragraph(payload, S['h3']))
        elif kind == 'subtitle':
            story.append(Paragraph(payload, S['subtitle']))
        elif kind == 'quote':
            story.append(Paragraph(payload, S['quote']))
        elif kind == 'pagebreak':
            story.append(PageBreak())
        elif kind == 'table':
            rows = len(payload); cols = max(len(r) for r in payload)
            data = [[Paragraph(r[ci] if ci < len(r) else '', S['caption']) for ci in range(cols)] for r in payload]
            t = Table(data, colWidths=[content_width / cols] * cols)
            t.setStyle([('GRID', (0, 0), (-1, -1), 0.5, '#999999'),
                        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
                        ('FONTNAME', (0, 0), (-1, -1), SANS),
                        ('TOPPADDING', (0, 0), (-1, -1), 3),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 3)])
            story.append(t)
            story.append(Spacer(1, 8))
        elif kind == 'image':
            src = resolve_file(payload['path']) if payload['path'] else None
            caption = payload['caption'] or payload['prompt'] or ''
            if src:
                try:
                    from md_to_hwpx import shrink_image
                    fmt0 = 'png' if Path(src).suffix.lower().lstrip('.') == 'png' else 'jpg'
                    raw = Path(src).read_bytes()
                    data, fmt0 = shrink_image(raw, fmt0)
                    use = src
                    if data is not raw:
                        import tempfile
                        tf = tempfile.NamedTemporaryFile(delete=False, suffix='.png' if fmt0 == 'png' else '.jpg')
                        tf.write(data)
                        tf.close()
                        use = tf.name
                        temps.append(use)
                    ir = ImageReader(use)
                    iw, ih = ir.getSize()
                    w = min(15 * cm, content_width)
                    h = w * ih / iw
                    if h > 22 * cm:
                        h = 22 * cm; w = h * iw / ih
                    story.append(RLImage(use, width=w, height=h))
                    if caption:
                        story.append(Paragraph('▲ ' + caption, S['caption']))
                    n_img += 1
                except Exception as e:
                    story.append(Paragraph('【사진 삽입 실패: %s】' % e, S['ph']))
                    n_ph += 1
            else:
                story.append(Paragraph('【사진 자리】 %s' % (payload['prompt'] or caption), S['ph']))
                n_ph += 1
        else:
            story.append(Paragraph(payload, S['para']))

    out_dir = Path.cwd() / 'output'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (name + '.pdf')
    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            title=name)
    if newspaper:newspaper_document(out_path,story,masthead)
    else:doc.build(story)
    for tmp in temps:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
    size = out_path.stat().st_size
    print('변환 완료: ' + out_path.name)
    print('크기: %d바이트' % size)
    print('사진 %d, 사진자리 %d' % (n_img, n_ph))


if __name__ == '__main__':
    main()
