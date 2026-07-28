"""Document extraction adapters selected by source type."""

from .base import ExtractionAdapter, ExtractionRouter
from .pdf import PyMuPDFExtractionAdapter


def default_extraction_router() -> ExtractionRouter:
    return ExtractionRouter([PyMuPDFExtractionAdapter()])


__all__ = [
    "ExtractionAdapter",
    "ExtractionRouter",
    "PyMuPDFExtractionAdapter",
    "default_extraction_router",
]
