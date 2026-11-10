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
                                SimpleDocTemplate, Spacer, Table)
from reportlab.lib.utils import ImageReader

pdfmetrics.registerFont(UnicodeCIDFont('HYSMyeongJo-Medium'))
pdfmetrics.registerFont(UnicodeCIDFont('HYGothic-Medium'))
SERIF = 'HYSMyeongJo-Medium'
SANS = 'HYGothic-Medium'

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


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: md_to_pdf.py <원본 경로 또는 본문> [출력 이름]')
    text, default_name = find_source(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else default_name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip()[:120] or 'pdf-출력'

    blocks = parse_markdown(text)
    if not blocks:
        raise SystemExit('변환할 본문이 없습니다.')

    story = []
    n_img = n_ph = 0
    for kind, payload in blocks:
        if kind == 'h1':
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
            data = [[(r[ci] if ci < len(r) else '') for ci in range(cols)] for r in payload]
            t = Table(data, colWidths=[(A4[0] - 4 * cm) / cols] * cols)
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
                    ir = ImageReader(src)
                    iw, ih = ir.getSize()
                    w = min(15 * cm, A4[0] - 4 * cm)
                    h = w * ih / iw
                    if h > 22 * cm:
                        h = 22 * cm; w = h * iw / ih
                    story.append(RLImage(ir, width=w, height=h))
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
    doc.build(story)
    size = out_path.stat().st_size
    print('변환 완료: ' + out_path.name)
    print('크기: %d바이트' % size)
    print('사진 %d, 사진자리 %d' % (n_img, n_ph))


if __name__ == '__main__':
    main()
