"""Tier 2 — 결정론적 검증 게이트 (LLM 없음).

G1 quote grounding   셀 값이 해당 페이지 PDF 텍스트층에 실재하는지
G2 커버리지          pdfplumber가 학수번호를 찾은 페이지에서 Docling 표가 비면 FAIL
                     (행 0개 → 경고 0건이 되는 '조용한 실패'를 차단)
G3 교차검증          Docling vs pdfplumber 학수번호 집합 대조 + 이수구분 보충
G4 정합성            표 내부 학수번호 중복 / 합계행 대조 / 빈값·단위·다중숫자
G5 스키마            Pydantic (모델 조립 시 자동 — 별도 코드 없음)

원칙: 실패는 조용히 넘기지 않는다. 자동으로 확정 못 하는 값은 review로 강등하고
사람 검토 큐로 보낸다. 사람이 고친 값(extractor="human:*")은 G1을 면제받지만
G4 정합성은 그대로 적용된다 — 사람도 틀릴 수 있으므로.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import fitz  # PyMuPDF

from src.extractor.crosscheck import PageEvidence
from src.extractor.models import Cell, Check, Table
from src.extractor.numeric import parse_numeric
from src.extractor.profiles import (
    COUNT_PATTERN,
    COURSE_CODE,
    TOTAL_ROW,
    classify_header,
    normalize_header,
)

log = logging.getLogger(__name__)

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub("", s)


def _demote(cell: Cell, issue: str) -> None:
    if issue not in cell.issues:
        cell.issues.append(issue)
    if cell.status != "confirmed":
        cell.status = "review"


def run_gates(
    tables: list[Table],
    evidence: dict[int, PageEvidence],
    pdf_path: str | Path,
) -> list[Check]:
    """모든 게이트를 실행하고 검사 결과 목록을 돌려준다. 셀 status/issues는 제자리 갱신."""
    checks: list[Check] = []
    page_texts = _load_page_texts(pdf_path, sorted(evidence.keys()))

    checks += _g1_quote_grounding(tables, page_texts, evidence, pdf_path)
    checks += _g2_coverage(tables, evidence)
    checks += _g3_crosscheck(tables, evidence)
    checks += _g4_consistency(tables)
    return checks


# ── G1 ──────────────────────────────────────────────────────────────────────

def _load_page_texts(pdf_path: str | Path, pages: list[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    with fitz.open(str(pdf_path)) as doc:
        for p in pages:
            if 1 <= p <= doc.page_count:
                out[p] = _norm(doc[p - 1].get_text())
    return out


def _chars_contained(needle: str, haystack: str) -> bool:
    """순서 무관 문자 구성 포함 검사.

    Canon 조판 PDF는 텍스트 스트림 순서가 시각 순서와 다르다(실측: '4주'가
    스트림엔 '주','4' 역순). Docling이 시각 순서로 복원한 값을 스트림과
    비교할 때는 bbox 영역 안의 문자 구성으로 대조한다.
    """
    from collections import Counter

    need = Counter(needle)
    have = Counter(haystack)
    return all(have[ch] >= n for ch, n in need.items())


def _g1_quote_grounding(
    tables: list[Table],
    page_texts: dict[int, str],
    evidence: dict[int, PageEvidence],
    pdf_path: str | Path,
) -> list[Check]:
    checks: list[Check] = []
    n_checked = n_missing = 0
    doc = fitz.open(str(pdf_path))
    for t in tables:
        page_text = page_texts.get(t.page, "")
        ev = evidence.get(t.page)
        fitz_page = doc[t.page - 1] if 1 <= t.page <= doc.page_count else None
        if not page_text or (ev and not ev.has_text_layer):
            # 텍스트층이 없으면 대조 불가 — 값을 확정하지 않고 페이지 단위 경고
            checks.append(
                Check(
                    check_id=f"G1-nolayer-{t.page}",
                    level="WARNING",
                    kind="quote_grounding",
                    message=f"p{t.page}: 텍스트층이 없어 원문 대조 불가 (OCR 필요)",
                    page=t.page,
                    refs=[t.table_id],
                )
            )
            for c in t.cells:
                if c.value:
                    c.provenance.quote_verified = None
                    _demote(c, "no_text_layer")
            continue
        for c in t.cells:
            if not c.value or c.provenance.extractor.startswith("human:"):
                continue
            key = _norm(c.value)
            if len(key) < 2:
                continue  # 한 글자·한 자리 숫자는 대조가 무의미
            n_checked += 1
            ok = key in page_text
            if not ok and c.provenance.bbox is not None and fitz_page is not None:
                # 스트림 순서가 깨진 영역 — 셀 bbox 안의 문자 구성으로 재대조
                x0, y0, x1, y1 = c.provenance.bbox
                clip = fitz.Rect(x0 - 2, y0 - 2, x1 + 2, y1 + 2)
                region = _norm(fitz_page.get_text(clip=clip))
                ok = _chars_contained(key, region)
            c.provenance.quote_verified = ok
            if not ok:
                n_missing += 1
                _demote(c, "quote_not_in_page")
                checks.append(
                    Check(
                        check_id=f"G1-{t.table_id}-r{c.row}c{c.col}",
                        level="WARNING",
                        kind="quote_grounding",
                        message=f"{t.table_id} r{c.row}c{c.col} 값 '{c.value[:30]}' 이(가) "
                        f"p{t.page} 텍스트층에 없음",
                        page=t.page,
                        refs=[f"{t.table_id}:r{c.row}c{c.col}"],
                    )
                )
    doc.close()
    checks.append(
        Check(
            check_id="G1-summary",
            level="PASS" if n_missing == 0 else "WARNING",
            kind="quote_grounding",
            message=f"원문 대조 {n_checked}건 중 불일치 {n_missing}건",
        )
    )
    return checks


# ── G2 ──────────────────────────────────────────────────────────────────────

def _g2_coverage(tables: list[Table], evidence: dict[int, PageEvidence]) -> list[Check]:
    """학수번호가 존재하는 페이지에서 Docling 표가 그것을 못 담았으면 표 미검출."""
    checks: list[Check] = []
    docling_codes_by_page: dict[int, set[str]] = {}
    tables_by_page: dict[int, int] = {}
    for t in tables:
        tables_by_page[t.page] = tables_by_page.get(t.page, 0) + 1
        acc = docling_codes_by_page.setdefault(t.page, set())
        for c in t.cells:
            acc.update(COURSE_CODE.findall(c.value))

    for p, ev in sorted(evidence.items()):
        if not ev.codes:
            continue
        got = docling_codes_by_page.get(p, set())
        missing = ev.codes - got
        if not missing:
            checks.append(
                Check(
                    check_id=f"G2-{p}",
                    level="PASS",
                    kind="coverage",
                    message=f"p{p}: 학수번호 {len(ev.codes)}개 전부 표에 수록",
                    page=p,
                )
            )
        elif tables_by_page.get(p, 0) == 0:
            checks.append(
                Check(
                    check_id=f"G2-{p}",
                    level="FAIL",
                    kind="coverage",
                    message=f"p{p}: 학수번호 {len(ev.codes)}개가 있는데 표가 하나도 "
                    f"검출되지 않음 (표 미검출 — 조용한 실패)",
                    page=p,
                )
            )
        else:
            checks.append(
                Check(
                    check_id=f"G2-{p}",
                    level="WARNING",
                    kind="coverage",
                    message=f"p{p}: 학수번호 {len(missing)}개가 표에 없음: "
                    f"{', '.join(sorted(missing)[:8])}{' …' if len(missing) > 8 else ''}",
                    page=p,
                )
            )
    return checks


# ── G3 ──────────────────────────────────────────────────────────────────────

def _g3_crosscheck(tables: list[Table], evidence: dict[int, PageEvidence]) -> list[Check]:
    """이수구분 보충: Docling이 소실한 카테고리를 pdfplumber 매핑으로 채운다."""
    checks: list[Check] = []
    n_filled = n_conflict = 0

    for t in tables:
        ev = evidence.get(t.page)
        if ev is None:
            continue
        cat_col = _find_category_column(t)
        if cat_col is None:
            continue
        # 행 → 학수번호
        row_codes: dict[int, str] = {}
        for c in t.cells:
            found = COURSE_CODE.findall(c.value)
            if found:
                row_codes[c.row] = found[0]

        missing_refs: list[str] = []
        missing_codes: list[str] = []
        for row, code in sorted(row_codes.items()):
            plumber_cat = ev.code_category.get(code)
            cell = t.cell_at(row, cat_col)
            if plumber_cat is None:
                # 어느 추출기도 이 행의 이수구분을 모른다 — 추측하지 않고 사람에게.
                # (기존 브랜치는 여기서 표 인덱스로 추측해 오분류를 만들었다)
                if cell is None:
                    cell = _new_cell(t, row, cat_col, "", "docling")
                if not cell.value.strip() and cell.status != "confirmed":
                    _demote(cell, "category_missing")
                    missing_refs.append(f"{t.table_id}:r{row}c{cat_col}")
                    missing_codes.append(code)
                continue
            if cell is None:
                cell = _new_cell(t, row, cat_col, "", "pdfplumber")
            if not cell.value.strip():
                # Docling 소실분 보충 — 근거는 pdfplumber 독립 추출 (같은 행 라벨)
                cell.value = plumber_cat
                cell.provenance.extractor = "pdfplumber"
                cell.provenance.source_quote = plumber_cat
                cell.notes.append(f"category_backfilled({code})")
                n_filled += 1
            else:
                from src.extractor.profiles import normalize_category

                docling_cat = normalize_category(cell.value)
                if docling_cat and docling_cat != plumber_cat:
                    n_conflict += 1
                    _demote(cell, f"category_conflict(docling={docling_cat},plumber={plumber_cat})")
                    checks.append(
                        Check(
                            check_id=f"G3-cat-{t.table_id}-r{row}",
                            level="WARNING",
                            kind="crosscheck",
                            message=f"{code}: 이수구분 불일치 — docling '{docling_cat}' vs "
                            f"pdfplumber '{plumber_cat}'",
                            page=t.page,
                            refs=[f"{t.table_id}:r{row}c{cat_col}"],
                            suggestion=plumber_cat,
                        )
                    )
        if missing_refs:
            # 표 단위 일괄 판정 — 셀마다 묻지 않고 한 번에 결정하게 한다.
            # 원인은 대부분 페이지 연속 표(라벨 글리프가 다른 페이지에 인쇄됨).
            preview = ", ".join(missing_codes[:6]) + (" …" if len(missing_codes) > 6 else "")
            checks.append(
                Check(
                    check_id=f"G3-miss-{t.table_id}",
                    level="WARNING",
                    kind="crosscheck",
                    message=f"{t.table_id}(p{t.page}): 과목 {len(missing_codes)}개의 이수구분을 "
                    f"어느 추출기도 확정하지 못함 — 페이지 연속 표로 추정, 원문 확인 후 "
                    f"일괄 판정 가능 ({preview})",
                    page=t.page,
                    refs=missing_refs,
                    suggestion=_neighbor_label_suggestion(evidence, t.page),
                )
            )
    checks.append(
        Check(
            check_id="G3-category-summary",
            level="PASS" if n_conflict == 0 else "WARNING",
            kind="crosscheck",
            message=f"이수구분 보충 {n_filled}건, 두 추출기 불일치 {n_conflict}건",
        )
    )
    return checks


def _neighbor_label_suggestion(evidence: dict[int, PageEvidence], page: int) -> str | None:
    """인접 페이지(±1)의 라벨 집합이 정확히 하나면 제안값으로 쓴다.

    페이지 연속 표에서 라벨은 이웃 페이지에 인쇄되어 있는 경우가 많다.
    후보가 둘 이상이거나 없으면 제안하지 않는다 — 사람이 원문을 본다.
    """
    labels: set[str] = set()
    for p in (page - 1, page, page + 1):
        ev = evidence.get(p)
        if ev is not None:
            labels.update(ev.code_category.values())
    return labels.pop() if len(labels) == 1 else None


def _find_category_column(t: Table) -> int | None:
    """헤더 텍스트로 이수구분 열을 찾는다 (위치 인덱스 하드코딩 금지)."""
    for c in t.cells:
        if c.row <= 2 and classify_header(c.value) == "category":
            return c.col
    return None


def _new_cell(t: Table, row: int, col: int, value: str, extractor: str) -> Cell:
    """Docling이 셀 객체 자체를 만들지 않은 (병합 소실) 위치에 셀을 생성한다."""
    from src.extractor.models import Provenance

    cell = Cell(
        row=row,
        col=col,
        value=value,
        provenance=Provenance(page=t.page, source_quote=value, extractor=extractor),
    )
    t.cells.append(cell)
    return cell


# ── G4 ──────────────────────────────────────────────────────────────────────

def _g4_consistency(tables: list[Table]) -> list[Check]:
    checks: list[Check] = []
    for t in tables:
        checks += _check_duplicate_codes(t)
        checks += _check_numeric_cells(t)
        checks += _check_total_rows(t)
    return checks


def _check_duplicate_codes(t: Table) -> list[Check]:
    """표 내부 중복만 검사한다 — 학수번호는 문서 전체에서 19.7%가 여러 페이지에
    정상적으로 재출현하므로(실측), 표 간 중복은 오류가 아니다."""
    seen: dict[str, int] = {}
    dups: list[str] = []
    for c in t.cells:
        for code in COURSE_CODE.findall(c.value):
            if code in seen and seen[code] != c.row:
                dups.append(code)
            else:
                seen[code] = c.row
    if not dups:
        return []
    return [
        Check(
            check_id=f"G4-dup-{t.table_id}",
            level="WARNING",
            kind="consistency",
            message=f"{t.table_id}: 표 내부 학수번호 중복 {sorted(set(dups))}",
            page=t.page,
            refs=[t.table_id],
        )
    ]


def _check_numeric_cells(t: Table) -> list[Check]:
    """숫자 열에서만 빈값·단위·다중숫자를 검사한다.

    열 단위 판정 이유: '3-2'(학년-학기), '캡스톤디자인2' 같은 정상 텍스트를
    셀 단위로 검사하면 대량 오탐이 난다(실측 60건). 열의 값 과반이 숫자일 때만
    그 열을 숫자 열로 보고, 그 안의 이상값을 잡는다.
    """
    checks: list[Check] = []
    by_col: dict[int, list[Cell]] = {}
    for c in t.cells:
        if c.is_header or not c.value.strip():
            continue
        by_col.setdefault(c.col, []).append(c)

    for _col, cells in sorted(by_col.items()):
        parsed = [(c, parse_numeric(c.value)) for c in cells]
        numeric_like = [p for _, p in parsed if p.kind in ("number", "dual", "unit")]
        if len(numeric_like) < 3 or len(numeric_like) / len(parsed) < 0.6:
            continue  # 숫자 열이 아님 — 텍스트 열은 검사하지 않는다
        for c, p in parsed:
            checks += _flag_numeric_cell(t, c, p)
    return checks


def _flag_numeric_cell(t: Table, c: Cell, numeric) -> list[Check]:
    """숫자 열 안의 개별 이상값 판정."""
    checks: list[Check] = []
    if numeric.kind == "unit":
        _demote(c, f"unit_value({numeric.unit})")
        checks.append(
            Check(
                check_id=f"G4-unit-{t.table_id}-r{c.row}c{c.col}",
                level="WARNING",
                kind="consistency",
                message=f"{t.table_id} r{c.row}c{c.col}: '{c.value}' — 단위가 붙은 값. "
                f"수치 {numeric.value:g}({numeric.unit})를 그대로 쓰면 안 됨",
                page=t.page,
                refs=[f"{t.table_id}:r{c.row}c{c.col}"],
            )
        )
    elif numeric.kind == "ambiguous":
        _demote(c, "ambiguous_number")
        checks.append(
            Check(
                check_id=f"G4-amb-{t.table_id}-r{c.row}c{c.col}",
                level="FAIL",
                kind="consistency",
                message=f"{t.table_id} r{c.row}c{c.col}: '{c.value}' — 숫자 열의 값인데 "
                f"해석 불가 (어느 값이 맞는지 자동 확정 금지)",
                page=t.page,
                refs=[f"{t.table_id}:r{c.row}c{c.col}"],
            )
        )
    return checks


def _check_total_rows(t: Table) -> list[Check]:
    """합계행('계/합계/총계/소계')이 있으면 위 행들의 합과 대조한다."""
    checks: list[Check] = []
    grid = t.grid()

    total_rows = [
        r
        for r in range(t.n_rows)
        if any(TOTAL_ROW.match(normalize_header(grid[r][c]) or "") for c in range(min(2, t.n_cols)))
    ]
    if not total_rows:
        return []

    header_rows = {c.row for c in t.cells if c.is_header}
    prev_end = max(header_rows) if header_rows else -1

    for tr in total_rows:
        body_range = range(prev_end + 1, tr)
        prev_end = tr
        for col in range(t.n_cols):
            stated = parse_numeric(grid[tr][col])
            if stated.kind not in ("number", "dual") or stated.value is None:
                # "43과목" 같은 개수 표기 대조
                m = COUNT_PATTERN.search(grid[tr][col])
                if m:
                    n_body = sum(
                        1 for r in body_range if COURSE_CODE.search(" ".join(grid[r]))
                    )
                    if n_body != int(m.group(1)):
                        checks.append(
                            Check(
                                check_id=f"G4-count-{t.table_id}-r{tr}c{col}",
                                level="WARNING",
                                kind="consistency",
                                message=f"{t.table_id}: 합계행 '{grid[tr][col]}' vs 실제 행 수 "
                                f"{n_body} 불일치",
                                page=t.page,
                                refs=[f"{t.table_id}:r{tr}c{col}"],
                                suggestion=str(n_body),
                            )
                        )
                continue
            vals = [parse_numeric(grid[r][col]) for r in body_range]
            usable = [v.value for v in vals if v.kind in ("number", "dual") and v.value is not None]
            if len(usable) < 2:
                continue  # 합산할 대상이 없으면 대조하지 않는다
            body_sum = sum(usable)
            if abs(body_sum - stated.value) > 1e-9:
                cell = t.cell_at(tr, col)
                if cell is not None:
                    _demote(cell, f"sum_mismatch(stated={stated.value},calc={body_sum})")
                checks.append(
                    Check(
                        check_id=f"G4-sum-{t.table_id}-r{tr}c{col}",
                        level="WARNING",
                        kind="consistency",
                        message=f"{t.table_id} c{col}: 합계행 {stated.value:g} vs "
                        f"본문 합 {body_sum:g} 불일치 — 추출 오류 또는 원문 오류. "
                        f"원문 확인 후 사람이 판정할 것",
                        page=t.page,
                        refs=[f"{t.table_id}:r{tr}c{col}"],
                        suggestion=f"{body_sum:g}",
                    )
                )
            else:
                checks.append(
                    Check(
                        check_id=f"G4-sum-{t.table_id}-r{tr}c{col}",
                        level="PASS",
                        kind="consistency",
                        message=f"{t.table_id} c{col}: 합계 {stated.value:g} 일치",
                        page=t.page,
                    )
                )
    return checks
