"""Evidence를 발췌 PDF의 페이지 이미지와 강조 영역으로 변환한다.

Verified KG의 `Evidence.bbox`는 현재 전건 null이므로 좌표를 저장된 값에서 가져올 수
없다. 대신 `Evidence.raw_text`를 표 셀 단위로 쪼개 PyMuPDF 텍스트 검색으로 실제
좌표를 찾는다. 찾은 사각형은 페이지 크기로 정규화해 프런트엔드가 빨간 박스로 덮는다.

PDF 원본이 없으면 예외를 던지지 않고 `available=False` 상태를 돌려준다. 화면은
Evidence 원문 텍스트와 페이지 번호만으로 동작한다.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PDF_PATH = ROOT / "data/raw/2026_curriculum_excerpt.pdf"
PDF_PATH_ENV = "CURRICULUM_PDF_PATH"

# data/verified/2026 README와 Document 노드에 기록된 발췌 PDF 해시.
EXPECTED_SHA256 = "8ee5ee9d45fde0b00f8c42dc5aa513a46ec6a28bed4db50af25a049ae2dac004"
EXPECTED_PAGE_COUNT = 19

RENDER_DPI = 144
MIN_NEEDLE_LENGTH = 2
MAX_NEEDLES_PER_EVIDENCE = 12
MAX_RECTS_PER_EVIDENCE = 40
_LABEL_PREFIX = re.compile(r"^(?:이론|실기|개설학기|핵심역량|관련학과|학점)\s*")


class PdfEvidenceError(RuntimeError):
    """PDF를 읽을 수 있지만 요청한 페이지를 처리할 수 없을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class Highlight:
    """페이지 크기로 정규화된 강조 영역. 값은 0.0~1.0이다."""

    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class PdfSource:
    """발췌 PDF의 탑재 상태."""

    path: Path
    available: bool
    page_count: int = 0
    sha256: str | None = None
    sha256_matches: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "available": self.available,
            "page_count": self.page_count,
            "sha256": self.sha256,
            "sha256_matches": self.sha256_matches,
            "expected_sha256": EXPECTED_SHA256,
            "reason": self.reason,
        }


def resolve_pdf_path() -> Path:
    override = os.getenv(PDF_PATH_ENV, "").strip()
    return Path(override).expanduser() if override else DEFAULT_PDF_PATH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_pdf(path: Path | None = None) -> PdfSource:
    """PDF 탑재 여부, 페이지 수와 해시 일치를 확인한다."""
    target = path or resolve_pdf_path()
    if not target.is_file():
        return PdfSource(
            path=target,
            available=False,
            reason=(
                f"발췌 PDF가 없습니다. {target} 위치에 두거나 "
                f"{PDF_PATH_ENV} 환경변수로 경로를 지정하세요."
            ),
        )
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - 의존성 누락 환경
        return PdfSource(path=target, available=False, reason="pymupdf가 설치되지 않았습니다.")
    try:
        with pymupdf.open(target) as document:
            page_count = document.page_count
    except Exception as exc:  # pymupdf는 구체 예외 타입을 보장하지 않는다.
        return PdfSource(path=target, available=False, reason=f"PDF를 열 수 없습니다: {exc}")
    digest = _sha256(target)
    return PdfSource(
        path=target,
        available=True,
        page_count=page_count,
        sha256=digest,
        sha256_matches=digest == EXPECTED_SHA256,
        reason=(
            None
            if digest == EXPECTED_SHA256
            else "PDF 해시가 Verified 기준과 다릅니다. 근거 위치가 어긋날 수 있습니다."
        ),
    )


def needles_from_raw_text(raw_text: str) -> list[str]:
    """Evidence 원문을 페이지에서 찾을 만한 검색어로 쪼갠다.

    원문은 `기초교양 | 미래설계 | GEA8001 | 대학생활의설계(CDP-C) | ...` 형태이므로
    구분자로 나눈 뒤 라벨 접두어를 떼고 긴 조각을 우선한다.
    """
    if not raw_text:
        return []
    parts: list[str] = []
    for chunk in re.split(r"\s*[|·/]\s*|\s{2,}", raw_text):
        cleaned = _LABEL_PREFIX.sub("", chunk).strip(" .,()[]")
        if len(cleaned) < MIN_NEEDLE_LENGTH:
            continue
        if cleaned.isdigit() and len(cleaned) <= 2:
            # '1', '3' 같은 값은 페이지 전체에 흔해서 강조 대상이 될 수 없다.
            continue
        parts.append(cleaned)
    unique = list(dict.fromkeys(parts))
    unique.sort(key=len, reverse=True)
    return unique[:MAX_NEEDLES_PER_EVIDENCE]


def _merge_rects(rects: Sequence[Any], tolerance: float = 2.0) -> list[Any]:
    """같은 줄에 있는 사각형을 하나로 합쳐 박스 개수를 줄인다."""
    import pymupdf

    remaining = [pymupdf.Rect(rect) for rect in rects]
    merged: list[Any] = []
    for rect in sorted(remaining, key=lambda item: (round(item.y0, 1), item.x0)):
        for index, existing in enumerate(merged):
            same_line = abs(existing.y0 - rect.y0) <= tolerance and abs(
                existing.y1 - rect.y1
            ) <= tolerance
            if same_line:
                merged[index] = existing | rect
                break
        else:
            merged.append(rect)
    return merged


@lru_cache(maxsize=32)
def _page_geometry(path_key: str, mtime: float, page_index: int) -> tuple[float, float]:
    import pymupdf

    with pymupdf.open(path_key) as document:
        page = document[page_index]
        return page.rect.width, page.rect.height


def find_highlights(
    raw_text: str, page_number: int, path: Path | None = None
) -> list[Highlight]:
    """1-based 페이지에서 Evidence 원문 조각의 위치를 찾아 정규화해 돌려준다."""
    source = inspect_pdf(path)
    if not source.available:
        return []
    page_index = page_number - 1
    if not 0 <= page_index < source.page_count:
        return []

    import pymupdf

    with pymupdf.open(source.path) as document:
        page = document[page_index]
        width = page.rect.width or 1.0
        height = page.rect.height or 1.0
        found: list[Any] = []
        for needle in needles_from_raw_text(raw_text):
            try:
                hits = page.search_for(needle, quads=False)
            except Exception:  # 검색 실패는 강조 없음으로 처리한다.
                continue
            if hits:
                found.extend(hits)
            if len(found) >= MAX_RECTS_PER_EVIDENCE:
                break
        if not found:
            return []
        return [
            Highlight(
                x=max(rect.x0, 0.0) / width,
                y=max(rect.y0, 0.0) / height,
                width=min(rect.width, width) / width,
                height=min(rect.height, height) / height,
            )
            for rect in _merge_rects(found[:MAX_RECTS_PER_EVIDENCE])
        ]


def render_page_png(page_number: int, path: Path | None = None, dpi: int = RENDER_DPI) -> bytes:
    """1-based 페이지를 PNG 바이트로 렌더링한다."""
    source = inspect_pdf(path)
    if not source.available:
        raise PdfEvidenceError(source.reason or "PDF를 사용할 수 없습니다.")
    page_index = page_number - 1
    if not 0 <= page_index < source.page_count:
        raise PdfEvidenceError(
            f"페이지 범위를 벗어났습니다: {page_number} (1~{source.page_count})"
        )

    import pymupdf

    with pymupdf.open(source.path) as document:
        pixmap = document[page_index].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")


def build_evidence_pages(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evidence 목록을 참조 페이지 단위로 묶고 강조 영역을 계산한다.

    참조하지 않는 페이지는 포함하지 않는다. 각 페이지에는 그 페이지를 근거로 쓰는
    Evidence만 담긴다.
    """
    source = inspect_pdf()
    grouped: dict[int, dict[str, Any]] = {}
    for item in evidence:
        page_number = item.get("excerpt_page")
        if not isinstance(page_number, int):
            continue
        bucket = grouped.setdefault(
            page_number,
            {
                "excerpt_page": page_number,
                "source_pdf_page": item.get("source_pdf_page"),
                "printed_page": item.get("printed_page"),
                "evidence": [],
            },
        )
        highlights = (
            find_highlights(item.get("source_text", ""), page_number)
            if source.available
            else []
        )
        bucket["evidence"].append(
            {
                "evidence_id": item.get("evidence_id"),
                "source_text": item.get("source_text", ""),
                "printed_page": item.get("printed_page"),
                "source_pdf_page": item.get("source_pdf_page"),
                "highlights": [highlight.to_dict() for highlight in highlights],
                "highlight_found": bool(highlights),
            }
        )
    return [grouped[key] for key in sorted(grouped)]
