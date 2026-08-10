"""Evidence를 발췌 PDF의 페이지 이미지와 강조 영역으로 변환한다.

Verified KG의 `Evidence.bbox`는 현재 전건 null이므로 좌표를 저장된 값에서 가져올 수
없다. 대신 `Evidence.raw_text`를 표 셀 단위로 쪼개 PyMuPDF 텍스트 검색으로 실제
좌표를 찾는다. 찾은 사각형은 페이지 크기로 정규화해 프런트엔드가 빨간 박스로 덮는다.

PDF 원본이 없으면 예외를 던지지 않고 `available=False` 상태를 돌려준다. 화면은
Evidence 원문 텍스트와 페이지 번호만으로 동작한다.

기준 해시와 페이지 수는 코드에 적지 않고 Verified bundle의
`metadata.source_document`에서 읽는다. 발췌 PDF를 다시 뜨면 bundle만 갱신하면 된다.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import bundle


DEFAULT_PDF_PATH = bundle.ROOT / "data/raw/2026_curriculum_excerpt.pdf"
PDF_PATH_ENV = "CURRICULUM_PDF_PATH"

RENDER_DPI = 144
MIN_NEEDLE_LENGTH = 2
MAX_NEEDLES_PER_EVIDENCE = 12
MAX_RECTS_PER_EVIDENCE = 40
_LABEL_PREFIX = re.compile(r"^(?:이론|실기|개설학기|핵심역량|관련학과|학점)\s*")


class PdfEvidenceError(RuntimeError):
    """PDF를 읽을 수 있지만 요청한 페이지를 처리할 수 없을 때 발생한다."""


def expected_sha256() -> str | None:
    """Verified bundle이 기록한 발췌 PDF의 기준 해시."""
    value = bundle.source_document().get("sha256")
    return value if isinstance(value, str) else None


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
            "expected_sha256": expected_sha256(),
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


@lru_cache(maxsize=8)
def _inspect_existing(path_key: str, mtime_ns: int, size: int) -> PdfSource:
    """실제로 존재하는 PDF를 검사한다. 파일이 바뀌면 캐시 키가 달라진다."""
    target = Path(path_key)
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
    expected = expected_sha256()
    matches = expected is not None and digest == expected
    return PdfSource(
        path=target,
        available=True,
        page_count=page_count,
        sha256=digest,
        sha256_matches=matches,
        reason=(
            None
            if matches
            else "PDF 해시가 Verified 기준과 다릅니다. 근거 위치가 어긋날 수 있습니다."
        ),
    )


def inspect_pdf(path: Path | None = None) -> PdfSource:
    """PDF 탑재 여부, 페이지 수와 해시 일치를 확인한다.

    검사 결과는 경로와 파일의 mtime·크기로 캐시한다. 같은 질문 한 건에서 근거마다
    전체 파일을 다시 해싱하지 않기 위한 것이다. 파일이 없을 때는 캐시하지 않는다.
    나중에 PDF를 갖다 놓으면 재시작 없이 인식돼야 한다.
    """
    target = path or resolve_pdf_path()
    try:
        stat = target.stat()
    except OSError:
        return PdfSource(
            path=target,
            available=False,
            reason=(
                f"발췌 PDF가 없습니다. {target} 위치에 두거나 "
                f"{PDF_PATH_ENV} 환경변수로 경로를 지정하세요."
            ),
        )
    return _inspect_existing(str(target), stat.st_mtime_ns, stat.st_size)


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

    merged: list[Any] = []
    for rect in sorted(
        (pymupdf.Rect(item) for item in rects), key=lambda item: (round(item.y0, 1), item.x0)
    ):
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


def _highlights_on_page(page: Any, raw_text: str) -> list[Highlight]:
    """이미 열려 있는 페이지에서 원문 조각을 찾아 정규화한다."""
    width = page.rect.width or 1.0
    height = page.rect.height or 1.0
    found: list[Any] = []
    for needle in needles_from_raw_text(raw_text):
        try:
            hits = page.search_for(needle, quads=False)
        except Exception:  # 검색 실패는 강조 없음으로 처리한다.
            continue
        found.extend(hits)
        if len(found) >= MAX_RECTS_PER_EVIDENCE:
            break
    return [
        Highlight(
            x=max(rect.x0, 0.0) / width,
            y=max(rect.y0, 0.0) / height,
            width=min(rect.width, width) / width,
            height=min(rect.height, height) / height,
        )
        for rect in _merge_rects(found[:MAX_RECTS_PER_EVIDENCE])
    ]


def find_highlights(
    raw_text: str, page_number: int, path: Path | None = None
) -> list[Highlight]:
    """1-based 페이지에서 Evidence 원문 조각의 위치를 찾아 정규화해 돌려준다."""
    source = inspect_pdf(path)
    page_index = page_number - 1
    if not source.available or not 0 <= page_index < source.page_count:
        return []

    import pymupdf

    with pymupdf.open(source.path) as document:
        return _highlights_on_page(document[page_index], raw_text)


@lru_cache(maxsize=32)
def _render_cached(path_key: str, mtime_ns: int, page_index: int, dpi: int) -> bytes:
    import pymupdf

    with pymupdf.open(path_key) as document:
        return document[page_index].get_pixmap(dpi=dpi).tobytes("png")


def render_page_png(page_number: int, path: Path | None = None, dpi: int = RENDER_DPI) -> bytes:
    """1-based 페이지를 PNG 바이트로 렌더링한다.

    PDF와 DPI가 같으면 결과가 항상 같으므로 렌더 결과를 캐시한다.
    """
    source = inspect_pdf(path)
    if not source.available:
        raise PdfEvidenceError(source.reason or "PDF를 사용할 수 없습니다.")
    page_index = page_number - 1
    if not 0 <= page_index < source.page_count:
        raise PdfEvidenceError(
            f"페이지 범위를 벗어났습니다: {page_number} (1~{source.page_count})"
        )
    return _render_cached(
        str(source.path), source.path.stat().st_mtime_ns, page_index, dpi
    )


def build_evidence_pages(
    evidence: Iterable[dict[str, Any]], path: Path | None = None
) -> list[dict[str, Any]]:
    """Evidence 목록을 참조 페이지 단위로 묶고 강조 영역을 계산한다.

    참조하지 않는 페이지는 포함하지 않는다. 각 페이지에는 그 페이지를 근거로 쓰는
    Evidence만 담긴다. 강조 계산은 PDF를 한 번만 열고 페이지당 한 번만 가져온다.
    """
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
        bucket["evidence"].append(
            {
                "evidence_id": item.get("evidence_id"),
                "source_text": item.get("source_text", ""),
                "printed_page": item.get("printed_page"),
                "source_pdf_page": item.get("source_pdf_page"),
                "highlights": [],
                "highlight_found": False,
            }
        )

    pages = [grouped[key] for key in sorted(grouped)]
    source = inspect_pdf(path)
    if not source.available:
        return pages

    import pymupdf

    with pymupdf.open(source.path) as document:
        for bucket in pages:
            page_index = bucket["excerpt_page"] - 1
            if not 0 <= page_index < source.page_count:
                continue
            page = document[page_index]
            for entry in bucket["evidence"]:
                highlights = _highlights_on_page(page, entry["source_text"])
                entry["highlights"] = [item.to_dict() for item in highlights]
                entry["highlight_found"] = bool(highlights)
    return pages
