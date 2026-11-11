#!/usr/bin/env python3
"""마크다운 → DOCX(워드) 변환기 (고르곤 사내 스킬, 무설치 표준라이브러리).

블록 규약은 hwpx-report와 동일 (md_to_hwpx 파서 재사용):
  # 제목 / ## 면제목 / ### 기사제목 / **부제** / 표 / > 인용 / --- 페이지나눔
  ![캡션](파일경로) · > [사진:파일경로] 캡션 → 이미지 삽입
  > [사진] 지시문 → 사진 자리 표시

인수: 1. 마크다운 원본(위키 경로/절대경로/본문)  2. (선택) 출력 파일명
결과: 실행 폴더 output/<이름>.docx
"""
import re
import sys
import zipfile
from pathlib import Path

from md_to_hwpx import find_source, image_size, parse_markdown, resolve_file, shrink_image

EMU_PX = 9525
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
WP = 'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PIC = 'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
CT_TYPES = ('application/vnd.openxmlformats-officedocument.wordprocessingml.'
            'document.main+xml')
STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles %s><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Malgun Gothic" w:eastAsia="&#xB9D1;&#xC740; &#xACE0;&#xB515;" w:hAnsi="Malgun Gothic"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="264" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>""" % W


def esc(t):
    return (t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def para(text, sz=None, bold=False, center=False, color=None, indent=None, page_break=False):
    run = ''
    if text:
        rpr = ''
        if bold or sz or color:
            rpr = '<w:rPr>' + ('<w:b/>' if bold else '') + (
                '<w:color w:val="%s"/>' % color if color else '') + (
                '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (sz, sz) if sz else '') + '</w:rPr>'
        run = '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, esc(text))
    if not run and not (page_break or center or indent):
        return '<w:p/>'
    p = ['<w:p>']
    if page_break or center or indent:
        p.append('<w:pPr>')
        if page_break:
            p.append('<w:pageBreakBefore/>')
        if indent:
            p.append('<w:ind w:left="%d"/>' % indent)
        if center:
            p.append('<w:jc w:val="center"/>')
        p.append('</w:pPr>')
    p.append(run)
    p.append('</w:p>')
    return ''.join(p)


def drawing(img_px_w, img_px_h, rid, idx, max_w_emu=15 * 360000, max_h_emu=22 * 360000):
    nat_w = max(1, img_px_w) * EMU_PX
    nat_h = max(1, img_px_h) * EMU_PX
    ratio = min(max_w_emu / nat_w, max_h_emu / nat_h)
    cx, cy = int(nat_w * ratio), int(nat_h * ratio)
    return ('<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="%d" cy="%d"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            '<wp:docPr id="%d" name="Picture %d"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic><pic:nvPicPr><pic:cNvPr id="%d" name="img%d"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
            '</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
            ) % (cx, cy, idx, idx, idx, idx, rid, cx, cy)


def table_xml(rows):
    cols = max(len(r) for r in rows)
    edge = '<w:top w:val="single" w:sz="4" w:color="999999"/>' \
           '<w:left w:val="single" w:sz="4" w:color="999999"/>' \
           '<w:bottom w:val="single" w:sz="4" w:color="999999"/>' \
           '<w:right w:val="single" w:sz="4" w:color="999999"/>' \
           '<w:insideH w:val="single" w:sz="4" w:color="999999"/>' \
           '<w:insideV w:val="single" w:sz="4" w:color="999999"/>'
    out = ['<w:tbl><w:tblPr><w:tblW w:w="5000" w:type="pct"/><w:tblBorders>%s</w:tblBorders>'
           '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
           '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tblCellMar>'
           '</w:tblPr><w:tblGrid>%s</w:tblGrid>' % (edge, '<w:gridCol/>' * cols)]
    for row in rows:
        out.append('<w:tr>')
        for ci in range(cols):
            txt = esc(row[ci]) if ci < len(row) else ''
            out.append('<w:tc><w:tcPr/><w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p></w:tc>' % txt)
        out.append('</w:tr>')
    out.append('</w:tbl><w:p/>')
    return ''.join(out)


FMT_EXT = {'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg', 'gif': 'gif', 'bmp': 'bmp'}
CT_IMG = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
          'gif': 'image/gif', 'bmp': 'image/bmp'}


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: md_to_docx.py <원본 경로 또는 본문> [출력 이름]')
    text, default_name = find_source(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else default_name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip()[:120] or 'docx-출력'

    blocks = parse_markdown(text)
    if not blocks:
        raise SystemExit('변환할 본문이 없습니다.')

    body, rels, media, cts_used = [], [], [], set()
    n_img = n_ph = 0
    idx = 0
    for kind, payload in blocks:
        if kind == 'h1':
            body.append(para(payload, sz=44, bold=True, center=True))
        elif kind == 'h2':
            body.append(para(payload, sz=32, bold=True, center=True))
        elif kind == 'h3':
            body.append(para(payload, sz=26, bold=True))
        elif kind == 'subtitle':
            body.append(para(payload, sz=22, bold=True, center=True, color='555555'))
        elif kind == 'quote':
            body.append(para(payload, color='666666', indent=360))
        elif kind == 'pagebreak':
            body.append('<w:p><w:pPr><w:pageBreakBefore/></w:pPr></w:p>')
        elif kind == 'table':
            body.append(table_xml(payload))
        elif kind == 'image':
            src = resolve_file(payload['path']) if payload['path'] else None
            caption = payload['caption'] or payload['prompt'] or ''
            if src:
                fmt = FMT_EXT.get(Path(src).suffix.lower().lstrip('.'))
                if fmt:
                    data = Path(src).read_bytes()
                    data, fmt = shrink_image(data, fmt)
                    iw, ih = image_size(data, 'jpg' if fmt == 'jpg' else fmt)
                    idx += 1
                    rid = 'rIdImg%d' % idx
                    media.append(('word/media/image%d.%s' % (idx, fmt), data))
                    rels.append('<Relationship Id="%s" Type="http://schemas.openxmlformats.org/'
                                'officeDocument/2006/relationships/image" Target="media/image%d.%s"/>'
                                % (rid, idx, fmt))
                    cts_used.add(fmt)
                    body.append('<w:p><w:pPr><w:jc w:val="center"/></w:pPr>%s</w:p>'
                                % drawing(iw, ih, rid, idx))
                    if caption:
                        body.append(para('▲ ' + caption, sz=18, center=True, color='444444'))
                    n_img += 1
                else:
                    body.append(para('【사진 자리·미지원 형식】 ' + caption, center=True, color='888888'))
                    n_ph += 1
            else:
                body.append(para('【사진 자리】 ' + (payload['prompt'] or caption),
                                 center=True, color='888888'))
                n_ph += 1
        else:
            body.append(para(payload))

    body.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1200" '
                'w:right="1300" w:bottom="1200" w:left="1300"/></w:sectPr>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document %s %s %s %s %s><w:body>%s</w:body></w:document>'
                % (W, R, WP, A, PIC, ''.join(body)))
    doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rIdStyle" Type="http://schemas.openxmlformats.org/officeDocument/'
                '2006/relationships/styles" Target="styles.xml"/>%s</Relationships>' % ''.join(rels))
    cts = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
           '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
           '<Default Extension="xml" ContentType="application/xml"/>'
           + ''.join('<Default Extension="%s" ContentType="%s"/>' % (e, CT_IMG[e]) for e in sorted(cts_used))
           + '<Override PartName="/word/document.xml" ContentType="%s"/>'
             '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
             'officedocument.wordprocessingml.styles+xml"/></Types>' % CT_TYPES)
    root_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                 '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                 'relationships/officeDocument" Target="word/document.xml"/></Relationships>')

    import xml.dom.minidom
    for x in (document, doc_rels, cts, root_rels, STYLES):
        xml.dom.minidom.parseString(x)

    out_dir = Path.cwd() / 'output'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (name + '.docx')
    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', cts)
        z.writestr('_rels/.rels', root_rels)
        z.writestr('word/document.xml', document)
        z.writestr('word/_rels/document.xml.rels', doc_rels)
        z.writestr('word/styles.xml', STYLES)
        for path, data in media:
            z.writestr(path, data)

    size = out_path.stat().st_size
    print('변환 완료: ' + out_path.name)
    print('크기: %d바이트' % size)
    print('사진 %d, 사진자리 %d' % (n_img, n_ph))


if __name__ == '__main__':
    main()
