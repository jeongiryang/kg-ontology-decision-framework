from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from src.pdf_parser.extractor import extract_pdf_pages

from .base import ExtractionAdapter


class PyMuPDFExtractionAdapter(ExtractionAdapter):
    name = "PyMuPDF text PDF"

    def supports(self, source_path: Path) -> bool:
        return source_path.suffix.lower() == ".pdf"

    def extract(self, source_path: Path, pages: Sequence[int]) -> dict[str, Any]:
        return extract_pdf_pages(source_path, pages)
