from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence


class ExtractionAdapter(ABC):
    """A source-specific extractor that returns the common raw JSON shape."""

    name: str

    @abstractmethod
    def supports(self, source_path: Path) -> bool:
        """Return whether this adapter can read the source."""

    @abstractmethod
    def extract(self, source_path: Path, pages: Sequence[int]) -> dict[str, Any]:
        """Extract the requested one-based pages with evidence coordinates."""


class ExtractionRouter:
    def __init__(self, adapters: Sequence[ExtractionAdapter]) -> None:
        if not adapters:
            raise ValueError("추출 어댑터가 하나 이상 필요합니다.")
        self._adapters = list(adapters)

    def select(self, source_path: Path) -> ExtractionAdapter:
        for adapter in self._adapters:
            if adapter.supports(source_path):
                return adapter
        supported = ", ".join(adapter.name for adapter in self._adapters)
        raise ValueError(
            f"지원하지 않는 문서 형식입니다: {source_path.suffix or '(확장자 없음)'}. "
            f"현재 어댑터: {supported}"
        )

    def extract(self, source_path: Path, pages: Sequence[int]) -> dict[str, Any]:
        return self.select(source_path).extract(source_path, pages)
