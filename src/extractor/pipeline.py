"""파이프라인 오케스트레이션 — 추출 → 교차수집 → 게이트 → 결과 JSON.

사람 보정(apply_corrections)도 여기서 처리한다. 보정된 값은 반드시
게이트를 재통과한다(재검증) — 사람도 틀릴 수 있기 때문이다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import fitz

from src.extractor import crosscheck as xc
from src.extractor import docling_adapter
from src.extractor.gates import run_gates
from src.extractor.models import (
    Check,
    Correction,
    ExtractionResult,
    Summary,
    Table,
    TextBlock,
)
from src.extractor.numeric import parse_numeric

log = logging.getLogger(__name__)


def _sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_list(pdf_path: str | Path, page_range: tuple[int, int] | None) -> list[int]:
    with fitz.open(str(pdf_path)) as doc:
        n = doc.page_count
    if page_range is None:
        return list(range(1, n + 1))
    a, b = page_range
    return list(range(max(1, a), min(n, b) + 1))


def _summarize(
    tables: list[Table], texts: list[TextBlock], checks: list[Check], pages: list[int]
) -> Summary:
    cells = [c for t in tables for c in t.cells]
    return Summary(
        n_pages=len(pages),
        n_tables=len(tables),
        n_cells=len(cells),
        n_text_blocks=len(texts),
        n_auto=sum(1 for c in cells if c.status == "auto"),
        n_review=sum(1 for c in cells if c.status == "review"),
        n_confirmed=sum(1 for c in cells if c.status == "confirmed"),
        checks_pass=sum(1 for k in checks if k.level == "PASS"),
        checks_warning=sum(1 for k in checks if k.level == "WARNING"),
        checks_fail=sum(1 for k in checks if k.level == "FAIL"),
    )


def extract(
    pdf_path: str | Path,
    page_range: tuple[int, int] | None = None,
    ocr: bool = False,
) -> ExtractionResult:
    """PDF 하나를 추출·검증해 ExtractionResult로 돌려준다."""
    pdf_path = Path(pdf_path)
    pages = _page_list(pdf_path, page_range)
    log.info("추출 시작: %s (%d쪽)", pdf_path.name, len(pages))

    tables, texts = docling_adapter.parse_pdf(pdf_path, page_range=page_range, ocr=ocr)
    evidence = xc.collect(pdf_path, pages)
    checks = run_gates(tables, evidence, pdf_path)

    return ExtractionResult(
        source_file=pdf_path.name,
        sha256=_sha256(pdf_path),
        page_range=page_range,
        tables=tables,
        texts=texts,
        checks=checks,
        summary=_summarize(tables, texts, checks, pages),
    )


def revalidate(result: ExtractionResult, pdf_path: str | Path) -> ExtractionResult:
    """현재 표 내용을 기준으로 게이트만 다시 돌린다 (보정 적용 후 재검증용)."""
    pages = _page_list(pdf_path, result.page_range)
    # 이전 검사에서 붙은 review 강등을 초기화하고 다시 판정한다.
    # 사람이 확정한 셀(confirmed)은 유지하되 issues는 재평가 대상.
    for t in result.tables:
        for c in t.cells:
            c.issues = []
            if c.status == "review":
                c.status = "auto"
    evidence = xc.collect(pdf_path, pages)
    checks = run_gates(result.tables, evidence, pdf_path)
    result.checks = checks
    result.revision += 1
    result.summary = _summarize(result.tables, result.texts, checks, pages)
    return result


def apply_corrections(
    result: ExtractionResult,
    corrections: list[Correction],
    pdf_path: str | Path,
) -> ExtractionResult:
    """사람의 보정을 적용하고 전체 재검증한다.

    - keep_original: 값 유지, 셀만 confirmed로 (사람이 원문이 맞다고 판정)
    - use_suggested / manual: 값 교체, provenance를 human:<이름>으로 교체
    적용 후 게이트 재실행 — 보정이 새로운 불일치를 만들면 다시 WARNING이 뜬다.
    """
    for corr in corrections:
        found = result.find_cell(corr.target)
        if found is None:
            log.warning("보정 대상 없음: %s", corr.target)
            continue
        _table, cell = found
        if corr.action == "keep_original":
            cell.status = "confirmed"
            cell.notes.append(f"human_kept({corr.reviewer})")
        else:
            if cell.value != corr.old_value:
                # 보정 기록과 현재 값이 어긋나면 적용하지 않는다 (이중 수정 방지)
                log.warning(
                    "보정 건너뜀 %s: 현재값 %r ≠ 기록된 이전값 %r",
                    corr.target, cell.value, corr.old_value,
                )
                continue
            cell.value = corr.new_value
            num = parse_numeric(corr.new_value)
            cell.numeric = None if num.kind == "text" else num
            cell.provenance.extractor = f"human:{corr.reviewer}"
            cell.provenance.source_quote = corr.new_value
            cell.provenance.quote_verified = None
            cell.status = "confirmed"
            cell.notes.append(f"human_edited({corr.reviewer})")
        result.corrections.append(corr)

    return revalidate(result, pdf_path)


def make_correction(
    target: str,
    action: str,
    old_value: str,
    new_value: str,
    reviewer: str,
    instruction: str | None = None,
) -> Correction:
    return Correction(
        target=target,
        action=action,  # type: ignore[arg-type]
        old_value=old_value,
        new_value=new_value,
        reviewer=reviewer,
        instruction=instruction,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def save(result: ExtractionResult, out_path: str | Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8"
    )
    log.info("저장: %s", out_path)


def exit_code(result: ExtractionResult) -> int:
    """PR #3 컨벤션: 전체 통과 0 / WARNING 2 / FAIL 1."""
    if result.summary.checks_fail > 0:
        return 1
    if result.summary.checks_warning > 0:
        return 2
    return 0
