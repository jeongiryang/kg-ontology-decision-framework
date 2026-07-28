from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from src.normalizer.models import EvidenceRef


def _bounded_rect(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> pymupdf.Rect:
    bounded = rect & page_rect
    if bounded.is_empty or bounded.is_infinite:
        raise ValueError("원문 좌표가 PDF 페이지 범위 밖에 있습니다.")
    return bounded


def render_evidence_image(
    pdf_path: Path,
    evidence: EvidenceRef,
    *,
    full_page: bool = False,
    padding: float = 28.0,
    zoom: float = 2.0,
) -> bytes:
    """Render a page or padded evidence crop and draw a red evidence box."""

    if zoom <= 0:
        raise ValueError("이미지 배율은 0보다 커야 합니다.")
    resolved = pdf_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"원본 PDF를 찾을 수 없습니다: {pdf_path}")

    with pymupdf.open(resolved) as document:
        if evidence.pdf_page < 1 or evidence.pdf_page > document.page_count:
            raise ValueError(
                f"PDF {evidence.pdf_page}쪽은 문서 범위를 벗어났습니다."
            )
        page = document[evidence.pdf_page - 1]
        evidence_rect = _bounded_rect(pymupdf.Rect(evidence.bbox), page.rect)
        if full_page:
            clip = page.rect
        else:
            clip = _bounded_rect(
                pymupdf.Rect(
                    evidence_rect.x0 - padding,
                    evidence_rect.y0 - padding,
                    evidence_rect.x1 + padding,
                    evidence_rect.y1 + padding,
                ),
                page.rect,
            )
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=clip,
            alpha=False,
        )

    image = Image.open(BytesIO(pixmap.tobytes("png"))).convert("RGB")
    highlight = (
        int((evidence_rect.x0 - clip.x0) * zoom),
        int((evidence_rect.y0 - clip.y0) * zoom),
        int((evidence_rect.x1 - clip.x0) * zoom),
        int((evidence_rect.y1 - clip.y0) * zoom),
    )
    line_width = max(3, round(2 * zoom))
    ImageDraw.Draw(image).rectangle(highlight, outline="#e53935", width=line_width)
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
