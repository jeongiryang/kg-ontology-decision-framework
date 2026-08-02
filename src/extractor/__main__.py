"""CLI 진입점.

사용:
    python -m src.extractor --pdf "data/raw/★2022학년도 교육과정(업로드)(2022-3-2).pdf" \
        [--pages 287-289] [--ocr] [--out data/processed/extraction.json]

종료 코드: 0 전체 통과 / 2 WARNING 존재 / 1 FAIL 존재
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.extractor import pipeline


def _parse_pages(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    if "-" in s:
        a, b = s.split("-", 1)
        return (int(a), int(b))
    p = int(s)
    return (p, p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="extractor", description="규정집 PDF 자동 추출기")
    ap.add_argument("--pdf", required=True, help="입력 PDF 경로")
    ap.add_argument("--pages", default=None, help="페이지 범위 (예: 287-289). 생략 시 전체")
    ap.add_argument("--ocr", action="store_true", help="텍스트층 없는 페이지용 OCR 활성화")
    ap.add_argument("--out", default=None, help="결과 JSON 경로 (기본: data/processed/<파일명>.extraction.json)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"입력 파일 없음: {pdf}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else Path("data/processed") / f"{pdf.stem}.extraction.json"

    t0 = time.time()
    result = pipeline.extract(pdf, page_range=_parse_pages(args.pages), ocr=args.ocr)
    pipeline.save(result, out)
    dt = time.time() - t0

    s = result.summary
    print(
        f"\n━━ 추출 완료 ({dt:.1f}s) ━━\n"
        f"  페이지 {s.n_pages} | 표 {s.n_tables} | 셀 {s.n_cells} | 본문 블록 {s.n_text_blocks}\n"
        f"  자동확정 {s.n_auto} | 검토필요 {s.n_review} | 사람확정 {s.n_confirmed}\n"
        f"  검사 PASS {s.checks_pass} / WARNING {s.checks_warning} / FAIL {s.checks_fail}\n"
        f"  → {out}"
    )
    for k in result.checks:
        if k.level in ("WARNING", "FAIL"):
            print(f"  [{k.level}] {k.message}")
    return pipeline.exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
