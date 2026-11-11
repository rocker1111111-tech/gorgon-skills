#!/usr/bin/env python3
"""마크다운 → HWPX 변환기 v2 (고르곤 사내 스킬).

v2 신규:
  1. 이미지 블록 지원
     - ![캡션](파일경로)          → 이미지 삽입 + 캡션
     - > [사진] 지시문            → 사진 자리 박스(생성 전)
     - > [사진:파일경로] 캡션     → 이미지 삽입 + 캡션
  2. 위키 경로 모드 버그 수정 — /wiki/<회사폴더>/ 하위 경로 자동 탐색,
     경로처럼 보이는 입력을 찾지 못하면 오류로 종료(직접입력 오판 제거)

인수: 1. 마크다운 원본(위키 상대경로/절대경로/본문)  2. (선택) 출력 파일명
결과: 실행 폴더 output/<이름>.hwpx
"""
import os
import re
import sys
from pathlib import Path

WIKI_ROOTS = ['/wiki', '/obsidian']
WS_ROOT = '/data/workspaces/b4e40ed248aa46119baccf97aefb4c26'
PATHLIKE = re.compile(r'\.(md|markdown|txt)$|/|\\', re.I)


def resolve_file(p):
    """이미지/원본 파일 경로를 여러 후보에서 탐색해 실제 경로 반환(없으면 None)."""
    p = p.strip().strip('"\'')
    cands = []
    if os.path.isabs(p):
        cands.append(p)
    else:
        cands.append(os.path.join(os.getcwd(), p))
        for root in WIKI_ROOTS + [WS_ROOT]:
            cands.append(os.path.join(root, p))
        norm = p.replace('\\', '/').strip('/')
        for root in WIKI_ROOTS:
            if os.path.isdir(root):
                for dirpath, _dirs, files in os.walk(root):
                    for f in files:
                        fp = os.path.relpath(os.path.join(dirpath, f), root).replace('\\', '/')
                        if fp == norm or fp.endswith('/' + norm):
                            cands.append(os.path.join(dirpath, f))
    for c in cands:
        try:
            if os.path.isfile(c):
                return c
        except OSError:
            continue
    return None


def find_source(arg):
    """인수가 가리키는 마크다운 원본을 찾는다. (본문, 기본이름) 반환."""
    hit = resolve_file(arg)
    if hit and hit.lower().endswith(('.md', '.markdown', '.txt')):
        return Path(hit).read_text(encoding='utf-8-sig'), Path(hit).stem
    if '\n' in arg or len(arg) > 400:
        return arg, '직접입력'
    if PATHLIKE.search(arg):
        raise SystemExit('원본 파일을 찾지 못했습니다: ' + arg[:200])
    raise SystemExit('원본을 찾지 못했습니다: ' + arg[:120])


def parse_markdown(text):
    """마크다운을 블록 목록으로 변환. 각 블록은 (종류, 내용) 튜플."""
    blocks = []
    lines = text.replace('\r\n', '\n').split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        m = re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)', s)
        if m:
            blocks.append(('image', {'path': m.group(2), 'caption': m.group(1).strip(),
                                     'prompt': ''}))
            i += 1
            continue
        if s.startswith('### '):
            blocks.append(('h3', s[4:].strip())); i += 1; continue
        if s.startswith('## '):
            blocks.append(('h2', s[3:].strip())); i += 1; continue
        if s.startswith('# '):
            blocks.append(('h1', s[2:].strip())); i += 1; continue
        if re.fullmatch(r'-{3,}', s):
            blocks.append(('pagebreak', '')); i += 1; continue
        if s.startswith('> '):
            quote = []
            while i < len(lines) and lines[i].strip().startswith('> '):
                quote.append(lines[i].strip()[2:]); i += 1
            q = ' '.join(quote)
            pm = re.match(r'\[사진[:\s:]*([^\]]*)\]\s*(.*)', q)
            if pm:
                blocks.append(('image', {'path': pm.group(1).strip(),
                                         'caption': pm.group(2).strip() or pm.group(1).strip(),
                                         'prompt': pm.group(2).strip() or pm.group(1).strip()}))
            else:
                blocks.append(('quote', q))
            continue
        if s.startswith('|') and s.endswith('|'):
            table = []
            while i < len(lines) and lines[i].strip().startswith('|') and lines[i].strip().endswith('|'):
                row = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                table.append(row); i += 1
            table = [r for r in table if not all(re.fullmatch(r':?-{2,}:?', c) for c in r)]
            if table:
                blocks.append(('table', table))
            continue
        m = re.fullmatch(r'\*\*(.+)\*\*', s)
        if m:
            blocks.append(('subtitle', m.group(1).strip())); i += 1; continue
        para = [s]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(('#', '>', '|')) or re.fullmatch(r'-{3,}', nxt)
                    or re.fullmatch(r'\*\*(.+)\*\*', nxt)
                    or re.fullmatch(r'!\[[^\]]*\]\([^)]+\)', nxt)):
                break
            para.append(nxt); i += 1
        blocks.append(('para', ' '.join(para)))
    return blocks


def image_size(data, fmt):
    """PNG/JPEG 크기 (가로, 세로) 픽셀. 실패 시 (1344, 768)."""
    try:
        if fmt == 'png' and data[:8] == b'\x89PNG\r\n\x1a\n':
            import struct
            w, h = struct.unpack('>II', data[16:24])
            return int(w), int(h)
        if fmt == 'jpg' and data[:2] == b'\xff\xd8':
            j = 2
            while j < len(data) - 9:
                if data[j] != 0xFF:
                    j += 1; continue
                mk = data[j + 1]
                if mk in (0xC0, 0xC1, 0xC2, 0xC3):
                    import struct
                    h, w = struct.unpack('>HH', data[j + 5:j + 9])
                    return int(w), int(h)
                if mk in (0xD8, 0xD9, 0x01) or 0xD0 <= mk <= 0xD7:
                    j += 2
                else:
                    ln = int.from_bytes(data[j + 2:j + 4], 'big')
                    j += 2 + ln
    except Exception:
        pass
    return 1344, 768


FMT = {'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg', 'gif': 'gif', 'bmp': 'bmp'}


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: md_to_hwpx.py <원본 경로 또는 본문> [출력 이름]')
    text, default_name = find_source(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else default_name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip()[:120] or 'hwpx-출력'

    import hwpx

    blocks = parse_markdown(text)
    if not blocks:
        raise SystemExit('변환할 본문이 없습니다.')

    doc = hwpx.HwpxDocument.new()
    counts = {'h1': 0, 'h2': 0, 'h3': 0, 'subtitle': 0, 'para': 0, 'quote': 0,
              'table': 0, 'pagebreak': 0, 'image': 0, 'placeholder': 0}
    for kind, payload in blocks:
        if kind in counts and kind != 'image':
            counts[kind] += 1
        if kind == 'table':
            rows = len(payload); cols = max(len(r) for r in payload)
            t = doc.add_table(rows=rows, cols=cols)
            for ri, row in enumerate(payload):
                for ci in range(cols):
                    cell = row[ci] if ci < len(row) else ''
                    try:
                        t.set_cell_text(ri, ci, cell)
                    except Exception:
                        pass
        elif kind == 'pagebreak':
            doc.add_paragraph('【페이지 나눔】')
        elif kind == 'image':
            src = resolve_file(payload['path']) if payload['path'] else None
            caption = payload['caption'] or payload['prompt'] or ''
            if src:
                fmt = FMT.get(Path(src).suffix.lower().lstrip('.'))
                if fmt:
                    data = Path(src).read_bytes()
                    iw, ih = image_size(data, fmt)
                    w_mm = 140.0
                    h_mm = min(180.0, w_mm * ih / iw)
                    try:
                        doc.add_picture(data, fmt, width_mm=w_mm, height_mm=h_mm,
                                        align='center')
                        counts['image'] += 1
                        cap = '▲ ' + caption if caption else ''
                        if cap:
                            doc.add_paragraph(cap)
                    except Exception as e:
                        doc.add_paragraph('【사진 삽입 실패: %s】' % e)
                        counts['placeholder'] += 1
                else:
                    doc.add_paragraph('【사진 자리·미지원 형식 %s】 %s' % (payload['path'], caption))
                    counts['placeholder'] += 1
            else:
                doc.add_paragraph('【사진 자리】 %s' % (payload['prompt'] or caption))
                counts['placeholder'] += 1
        elif kind == 'h1':
            doc.add_heading(payload, level=1)
        elif kind == 'h2':
            doc.add_heading(payload, level=2)
        elif kind == 'h3':
            doc.add_heading(payload, level=3)
        else:
            doc.add_paragraph(payload)

    out_dir = Path.cwd() / 'output'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (name + '.hwpx')
    doc.save_to_path(str(out_path))

    try:
        errs = doc.validate()
    except Exception:
        errs = None
    size = out_path.stat().st_size
    print('변환 완료: ' + out_path.name)
    print('크기: %d바이트' % size)
    print('검증: %s' % ('오류 없음' if not errs else errs))
    print('블록: 제목 %d, 면제목 %d, 기사제목 %d, 부제 %d, 본문 %d, 인용 %d, 표 %d, '
          '페이지나눔 %d, 사진 %d, 사진자리 %d' % (
        counts['h1'], counts['h2'], counts['h3'], counts['subtitle'],
        counts['para'], counts['quote'], counts['table'], counts['pagebreak'],
        counts['image'], counts['placeholder']))


if __name__ == '__main__':
    main()
