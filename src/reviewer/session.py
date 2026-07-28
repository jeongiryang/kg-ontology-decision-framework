from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf

from src.extraction import default_extraction_router
from src.normalizer import (
    normalize_curriculum_pdf,
    write_normalized_json,
    write_review_markdown,
)
from src.normalizer.models import CorrectionSet, NormalizationResult
from src.pdf_parser.extractor import write_extraction_json

from .instruction import write_correction_proposal


CURRICULUM_PAGES = (128, 129, 130)


@dataclass(frozen=True)
class ReviewSessionPaths:
    directory: Path
    source_pdf: Path
    metadata_json: Path
    raw_json: Path
    normalized_json: Path
    review_markdown: Path
    corrections_json: Path
    events_jsonl: Path
    history_directory: Path


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _paths(directory: Path) -> ReviewSessionPaths:
    return ReviewSessionPaths(
        directory=directory,
        source_pdf=directory / "source.pdf",
        metadata_json=directory / "session.json",
        raw_json=directory / "raw.json",
        normalized_json=directory / "normalized.json",
        review_markdown=directory / "review.md",
        corrections_json=directory / "corrections.json",
        events_jsonl=directory / "events.jsonl",
        history_directory=directory / "history",
    )


def create_review_session(
    pdf_bytes: bytes,
    original_name: str,
    *,
    sessions_root: Path = Path("data/review_sessions"),
) -> ReviewSessionPaths:
    if not pdf_bytes:
        raise ValueError("업로드한 PDF가 비어 있습니다.")
    try:
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as document:
            page_count = document.page_count
    except Exception as error:
        raise ValueError("열 수 있는 PDF 파일이 아닙니다.") from error
    if page_count < CURRICULUM_PAGES[-1]:
        raise ValueError(
            f"현재 교육과정 프로필은 PDF 128–130쪽이 필요하지만 이 문서는 {page_count}쪽입니다."
        )

    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    directory = sessions_root / source_sha256[:16]
    directory.mkdir(parents=True, exist_ok=True)
    paths = _paths(directory)
    if paths.source_pdf.exists():
        existing_hash = hashlib.sha256(paths.source_pdf.read_bytes()).hexdigest()
        if existing_hash != source_sha256:
            raise ValueError("같은 세션 경로에 다른 원본 PDF가 있습니다.")
    else:
        paths.source_pdf.write_bytes(pdf_bytes)
    if not paths.metadata_json.exists():
        paths.metadata_json.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "original_name": Path(original_name).name,
                    "source_sha256": source_sha256,
                    "page_count": page_count,
                    "profile": "2022_curriculum_pages_128_130",
                    "created_at": _utc_now(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return paths


def _append_event(paths: ReviewSessionPaths, event: dict[str, Any]) -> None:
    payload = {"recorded_at": _utc_now(), **event}
    with paths.events_jsonl.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_review_result(paths: ReviewSessionPaths) -> NormalizationResult:
    if not paths.normalized_json.is_file():
        raise FileNotFoundError("아직 생성된 정규화 JSON이 없습니다.")
    return NormalizationResult.model_validate_json(
        paths.normalized_json.read_text(encoding="utf-8")
    )


def _normalize_with_blank_cell_retry(
    pdf_path: Path, corrections_path: Path
) -> NormalizationResult:
    marker = "invalid literal for int() with base 10: ''"
    for attempt in range(2):
        try:
            return normalize_curriculum_pdf(pdf_path, corrections_path)
        except ValueError as error:
            if marker not in str(error):
                raise
            if attempt == 0:
                continue
            raise ValueError(
                "PDF 표의 필수 숫자 셀을 두 번 연속 읽지 못했습니다. "
                "원본 PDF 128–130쪽의 학점·시수 셀이 비어 있거나 표 인식이 불안정합니다."
            ) from error
    raise AssertionError("숫자 셀 재시도 흐름이 종료되지 않았습니다.")


def initialize_review_session(
    paths: ReviewSessionPaths,
    *,
    force: bool = False,
) -> NormalizationResult:
    if paths.normalized_json.exists() and not force:
        return load_review_result(paths)

    raw = default_extraction_router().extract(paths.source_pdf, CURRICULUM_PAGES)
    write_extraction_json(raw, paths.raw_json)
    source_sha256 = raw["source"]["sha256"]
    if not paths.corrections_json.exists():
        write_correction_proposal(
            CorrectionSet(
                schema_version="1.0",
                source_sha256=source_sha256,
                description="검토 화면에서 승인한 보정값을 누적 저장한다.",
                corrections=[],
            ),
            paths.corrections_json,
        )
    result = _normalize_with_blank_cell_retry(
        paths.source_pdf, paths.corrections_json
    )
    write_normalized_json(result, paths.normalized_json)
    write_review_markdown(result, paths.review_markdown)
    _append_event(
        paths,
        {
            "event": "extracted",
            "validation_status": result.validation_status,
            "selected_pdf_pages": list(CURRICULUM_PAGES),
        },
    )
    return result


def _archive_current(paths: ReviewSessionPaths) -> None:
    if not paths.normalized_json.exists():
        return
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    archive = paths.history_directory / timestamp
    archive.mkdir(parents=True, exist_ok=False)
    for source in (
        paths.normalized_json,
        paths.review_markdown,
        paths.corrections_json,
    ):
        if source.exists():
            shutil.copy2(source, archive / source.name)


def apply_correction_proposal(
    paths: ReviewSessionPaths,
    proposal: CorrectionSet,
    *,
    instruction: str,
    check_id: str,
) -> NormalizationResult:
    current = load_review_result(paths)
    if proposal.source_sha256 != current.source.sha256:
        raise ValueError("보정 제안과 현재 검토 문서의 SHA-256이 다릅니다.")

    pending_corrections = paths.directory / "corrections.pending.json"
    pending_normalized = paths.directory / "normalized.pending.json"
    pending_review = paths.directory / "review.pending.md"
    write_correction_proposal(proposal, pending_corrections)
    try:
        revised = _normalize_with_blank_cell_retry(
            paths.source_pdf, pending_corrections
        )
        write_normalized_json(revised, pending_normalized)
        write_review_markdown(revised, pending_review)
        _archive_current(paths)
        pending_corrections.replace(paths.corrections_json)
        pending_normalized.replace(paths.normalized_json)
        pending_review.replace(paths.review_markdown)
    finally:
        for pending in (pending_corrections, pending_normalized, pending_review):
            pending.unlink(missing_ok=True)

    _append_event(
        paths,
        {
            "event": "correction_approved",
            "check_id": check_id,
            "instruction": instruction,
            "corrections": [
                correction.model_dump(mode="json")
                for correction in proposal.corrections
            ],
            "validation_status": revised.validation_status,
        },
    )
    return revised
