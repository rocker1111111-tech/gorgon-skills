#!/usr/bin/env python3
"""마크다운 → HWPX 변환기 (고르곤 사내 스킬).

인수:
  1. 마크다운 원본 — 위키 상대 경로(예: 편집장/정리/문서.md), 절대 경로, 또는 본문 텍스트
  2. (선택) 출력 파일명 (확장자 제외)

결과: 실행 폴더 output/<이름>.hwpx
"""
import os
import re
import sys
from pathlib import Path

WIKI_ROOTS = ['/wiki', '/obsidian']


def find_source(arg):
    """인수가 가리키는 마크다운 원본을 찾는다. (본문, 기본이름) 반환."""
    p = Path(arg)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.append(Path.cwd() / arg)
        for root in WIKI_ROOTS:
            candidates.append(Path(root) / arg)
    for c in candidates:
        try:
            if c.is_file() and c.suffix.lower() in ('.md', '.markdown', '.txt'):
                return c.read_text(encoding='utf-8-sig'), c.stem
        except OSError:
            continue
    if '\n' in arg or len(arg) > 3:
        return arg, '직접입력'
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
            blocks.append(('quote', ' '.join(quote))); continue
        if s.startswith('|') and s.endswith('|'):
            table = []
            while i < len(lines) and lines[i].strip().startswith('|') and lines[i].strip().endswith('|'):
                row = [c.strip() for c in lines[i].strip().strip('|').split('|')]
                table.append(row); i += 1
            # 헤더 구분 행(|---|) 제거
            table = [r for r in table if not all(re.fullmatch(r':?-{2,}:?', c) for c in r)]
            if table:
                blocks.append(('table', table))
            continue
        m = re.fullmatch(r'\*\*(.+)\*\*', s)
        if m:
            blocks.append(('subtitle', m.group(1).strip())); i += 1; continue
        # 연속된 본문 행을 하나의 문단으로
        para = [s]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(('#', '>', '|')) or re.fullmatch(r'-{3,}', nxt)
                    or re.fullmatch(r'\*\*(.+)\*\*', nxt)):
                break
            para.append(nxt); i += 1
        blocks.append(('para', ' '.join(para)))
    return blocks


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: md_to_hwpx.py <원본 경로 또는 본문> [출력 이름]')
    text, default_name = find_source(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else default_name
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip()[:120] or 'hwpx-출력'

    import hwpx  # python-hwpx (requirements.txt로 설치됨)

    blocks = parse_markdown(text)
    if not blocks:
        raise SystemExit('변환할 본문이 없습니다.')

    doc = hwpx.HwpxDocument.new()
    # 첫 빈 문단은 그대로 두고 뒤에 추가 (최소 1문단 유지 규칙)
    counts = {'h1': 0, 'h2': 0, 'h3': 0, 'subtitle': 0, 'para': 0, 'quote': 0, 'table': 0, 'pagebreak': 0}
    for kind, payload in blocks:
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
        else:
            doc.add_paragraph(payload)

    out_dir = Path.cwd() / 'output'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (name + '.hwpx')
    doc.save_to_path(str(out_path))

    size = out_path.stat().st_size
    print('변환 완료: ' + out_path.name)
    print('크기: %d바이트' % size)
    print('블록: 제목 %d, 면제목 %d, 기사제목 %d, 부제 %d, 본문 %d, 인용 %d, 표 %d, 페이지나눔 %d' % (
        counts['h1'], counts['h2'], counts['h3'], counts['subtitle'],
        counts['para'], counts['quote'], counts['table'], counts['pagebreak']))


if __name__ == '__main__':
    main()
