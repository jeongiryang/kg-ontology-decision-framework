from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import pymupdf


PAGE_RANGE_PATTERN = re.compile(r"^(?P<start>[1-9]\d*)-(?P<end>[1-9]\d*)$")


def parse_page_range(value: str) -> list[int]:
    """Parse an inclusive, one-based page range such as ``128-130``."""
    match = PAGE_RANGE_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("페이지 범위는 '128-130' 형식이어야 합니다.")

    start = int(match.group("start"))
    end = int(match.group("end"))
    if start > end:
        raise ValueError("페이지 범위의 시작은 끝보다 클 수 없습니다.")
    return list(range(start, end + 1))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_pages(page_numbers: Iterable[int], total_pages: int) -> list[int]:
    pages = list(page_numbers)
    if not pages:
        raise ValueError("추출할 페이지가 없습니다.")
    if any(page < 1 for page in pages):
        raise ValueError("PDF 페이지 번호는 1 이상이어야 합니다.")
    if len(pages) != len(set(pages)):
        raise ValueError("중복된 PDF 페이지 번호가 있습니다.")
    if pages != sorted(pages):
        raise ValueError("PDF 페이지 번호는 오름차순이어야 합니다.")
    if pages[-1] > total_pages:
        raise ValueError(
            f"요청한 {pages[-1]}쪽이 PDF 전체 페이지 수 {total_pages}를 초과합니다."
        )
    return pages


def _extract_page(document: pymupdf.Document, pdf_page: int) -> dict[str, Any]:
    # PyMuPDF는 0부터 시작하고 사용자가 보는 PDF 페이지는 1부터 시작한다.
    page = document[pdf_page - 1]
    blocks: list[dict[str, Any]] = []

    for raw_block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text, source_block_number, block_type = raw_block[:7]
        cleaned_text = text.strip()
        # block_type 0은 텍스트다. 이미지 블록은 현재 원문 JSON 범위에서 제외한다.
        if block_type != 0 or not cleaned_text:
            continue
        blocks.append(
            {
                "block_index": len(blocks),
                "source_block_number": int(source_block_number),
                "bbox": [round(float(value), 3) for value in (x0, y0, x1, y1)],
                "text": cleaned_text,
            }
        )

    page_text = "\n".join(block["text"] for block in blocks)
    return {
        "pdf_page": pdf_page,
        "page_index": pdf_page - 1,
        "printed_page": None,
        "width": round(float(page.rect.width), 3),
        "height": round(float(page.rect.height), 3),
        "block_count": len(blocks),
        "character_count": len(page_text),
        "text": page_text,
        "blocks": blocks,
    }


def extract_pdf_pages(pdf_path: Path, page_numbers: Iterable[int]) -> dict[str, Any]:
    """Extract only the requested one-based PDF pages with provenance metadata."""
    resolved_path = pdf_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")
    if resolved_path.suffix.lower() != ".pdf":
        raise ValueError(f"PDF 확장자가 아닌 파일입니다: {pdf_path}")

    with pymupdf.open(resolved_path) as document:
        pages = _validate_pages(page_numbers, document.page_count)
        extracted_pages = [_extract_page(document, page) for page in pages]

        if any(page["block_count"] == 0 for page in extracted_pages):
            empty_pages = [
                page["pdf_page"]
                for page in extracted_pages
                if page["block_count"] == 0
            ]
            raise ValueError(
                f"텍스트 블록이 없는 페이지가 있습니다: {empty_pages}. OCR 검토가 필요합니다."
            )

        return {
            "schema_version": "1.0",
            "source": {
                "file_name": resolved_path.name,
                "sha256": _sha256(resolved_path),
                "total_pdf_pages": document.page_count,
                "selected_pdf_pages": pages,
                "page_numbering": "one_based_pdf_page",
                "extractor": f"PyMuPDF {pymupdf.__version__}",
            },
            "pages": extracted_pages,
        }


def write_extraction_json(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
