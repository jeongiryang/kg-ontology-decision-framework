"""Tier 2 교차검증용 — pdfplumber 독립 재추출.

Docling과 완전히 다른 방식(텍스트층 직접 읽기)으로 같은 페이지를 다시 추출해
두 결과를 대조할 근거를 만든다. 추가로, 실측에서 확인된 상호보완 지점:
pdfplumber는 세로 병합된 이수구분 라벨('전공 필수' 등)을 보존하는 반면
Docling(TableFormer)은 이를 소실한다 — 그래서 학수번호→이수구분 매핑을
여기서 만들어 Docling 결과의 빈 카테고리를 근거 있게 보충한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from src.extractor.profiles import COURSE_CODE, normalize_category

log = logging.getLogger(__name__)


@dataclass
class PageEvidence:
    """페이지 하나에 대한 독립 추출 근거."""

    page: int
    codes: set[str] = field(default_factory=set)  # 텍스트층 전체에서 찾은 학수번호
    n_tables: int = 0  # pdfplumber가 인식한 표 개수
    code_category: dict[str, str] = field(default_factory=dict)  # 학수번호 → 이수구분
    has_text_layer: bool = True


def collect(pdf_path: str | Path, pages: list[int]) -> dict[int, PageEvidence]:
    """지정 페이지들의 독립 추출 근거를 수집한다."""
    out: dict[int, PageEvidence] = {}
    with pdfplumber.open(str(pdf_path)) as pdf:
        for p in pages:
            if p < 1 or p > len(pdf.pages):
                continue
            page = pdf.pages[p - 1]
            ev = PageEvidence(page=p)
            text = page.extract_text() or ""
            ev.has_text_layer = len(text.strip()) >= 20
            ev.codes = set(COURSE_CODE.findall(text))

            try:
                raw_tables = page.extract_tables() or []
            except Exception:
                raw_tables = []
            ev.n_tables = len(raw_tables)
            for rt in raw_tables:
                _merge_code_categories(rt, ev.code_category)
            out[p] = ev
    return out


def _merge_code_categories(rows: list[list[str | None]], acc: dict[str, str]) -> None:
    """pdfplumber 표 한 개에서 학수번호→이수구분 매핑을 뽑아 누적한다.

    매핑 규칙 두 가지 — 둘 다 "경계가 확정된 경우"에만 채운다:
      1) 같은 행에 라벨과 코드가 함께 있으면 그 행의 코드를 매핑 (병합 붕괴 blob 케이스)
      2) 두 라벨 행 **사이**의 라벨 없는 행은 앞 라벨로 채운다 — 다음 라벨이
         구간의 끝을 확정해 주기 때문에 안전하다.
    단, **마지막 라벨 이후의 행은 채우지 않는다.** 그 구간은 페이지 경계로
    잘렸을 수 있고(실측: CDA0155/0156/0157이 정확히 이 케이스 — 다음 페이지에서
    시작하는 전공선택 소속), 직전 라벨로 추측하면 기존 docling 브랜치의
    오분류를 재현한다. 미확정 코드는 검증 게이트가 사람 검토로 보낸다.
    """
    label_rows: list[tuple[int, str]] = []  # (행 번호, 라벨)
    for ri, r in enumerate(rows):
        for v in r:
            found = normalize_category(v or "")
            if found:
                label_rows.append((ri, found))
                break
    if not label_rows:
        return

    def _codes_in(ri: int) -> list[str]:
        return COURSE_CODE.findall(" ".join(x or "" for x in rows[ri]))

    # 규칙 1: 라벨과 같은 행의 코드
    for ri, label in label_rows:
        for code in _codes_in(ri):
            acc[code] = label

    # 규칙 2: 두 라벨 사이의 행 (마지막 라벨 이후는 제외)
    for (ri, label), (rj, _next) in zip(label_rows, label_rows[1:]):
        for mid in range(ri + 1, rj):
            for code in _codes_in(mid):
                acc[code] = label
