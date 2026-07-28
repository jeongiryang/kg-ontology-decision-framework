from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.normalizer.models import NormalizationResult, ValidationCheck


@dataclass(frozen=True)
class ReviewValue:
    label: str
    value: Any


@dataclass(frozen=True)
class CorrectionTargetView:
    uid: str
    field: str
    label: str
    current_value: Any


@dataclass(frozen=True)
class ReviewPresentation:
    summary: str
    values: tuple[ReviewValue, ...]
    decision_question: str
    instruction_examples: tuple[str, ...]


_TARGET_PATTERN = re.compile(
    r"^uid=(?P<uid>.+), field=(?P<field>[A-Za-z_][A-Za-z0-9_]*)$"
)
_FIELD_LABELS = {
    "term_credits": "학기별 학점 8개",
    "declared_total": "PDF에 적힌 총계",
    "major_required_credits": "기본이수표의 전공필수 학점",
    "text": "인재상 문장",
    "name_en": "영문 과목명",
}


def build_review_presentation(check: ValidationCheck) -> ReviewPresentation:
    if check.check_id.startswith("allocation_total:"):
        category = check.check_id.rsplit(":", maxsplit=1)[-1]
        difference = (
            check.expected - check.actual
            if isinstance(check.expected, int) and isinstance(check.actual, int)
            else None
        )
        values = [
            ReviewValue("PDF에 적힌 총계", check.expected),
            ReviewValue("학기별 학점 자동 합계", check.actual),
        ]
        if difference is not None:
            values.append(ReviewValue("두 값의 차이", difference))
        return ReviewPresentation(
            summary=f"{category} 행의 PDF 총계와 학기별 학점 합계가 다릅니다.",
            values=tuple(values),
            decision_question=(
                "PDF 총계를 고칠지, 학기별 학점 중 누락되거나 잘못 읽힌 값이 있는지 원문 표를 확인해 결정해 주세요."
            ),
            instruction_examples=(
                f"{category} 총계 {check.expected}을 {check.actual}로 수정해줘",
                "전체 페이지 보여줘",
            ),
        )

    if check.check_id == "required_credit_cross_section":
        actual = check.actual if isinstance(check.actual, dict) else {}
        return ReviewPresentation(
            summary="전공필수 학점이 세 표에서 서로 다르게 적혀 있습니다.",
            values=(
                ReviewValue("전공교육과정표", actual.get("course_catalog")),
                ReviewValue("기본이수 학점구조표", actual.get("degree_requirement")),
                ReviewValue("학기별 학점배분표", actual.get("semester_allocation")),
            ),
            decision_question=(
                "세 표 중 어느 값을 수정해야 하는지 원문을 비교해 결정해 주세요. 졸업 판정 기준 선택과 PDF 표기 수정은 구분해야 합니다."
            ),
            instruction_examples=(
                "학기배분표 전공필수 총계 24를 21로 수정해줘",
                "전체 페이지 보여줘",
            ),
        )

    if check.check_id == "source_spelling_candidates":
        candidates = check.expected if isinstance(check.expected, list) else []
        source_values = [
            item.get("source_text")
            for item in candidates
            if isinstance(item, dict) and item.get("source_text")
        ]
        suggested_values = [
            item.get("candidate")
            for item in candidates
            if isinstance(item, dict) and item.get("candidate")
        ]
        return ReviewPresentation(
            summary="PDF 원문에서 오탈자로 보이는 표기를 찾았습니다.",
            values=(
                ReviewValue("PDF 원문 표기", source_values or check.actual),
                ReviewValue("수정 후보", suggested_values),
            ),
            decision_question=(
                "원문 이미지와 발행기관 기준을 확인한 뒤 수정할 표기만 자연어로 지시해 주세요."
            ),
            instruction_examples=(
                "운리의식을 윤리의식으로 수정해줘",
                "Architechture를 Architecture로 바꿔줘",
                "오탈자 후보를 추천값으로 모두 수정해줘",
            ),
        )

    return ReviewPresentation(
        summary=check.message,
        values=(
            ReviewValue("검사 기준", check.expected),
            ReviewValue("자동 확인 결과", check.actual),
        ),
        decision_question=(
            check.reason
            or "원문과 자동 확인 결과를 비교하고 수정 여부를 결정해 주세요."
        ),
        instruction_examples=("전체 페이지 보여줘",),
    )


def correction_target_views(
    result: NormalizationResult, check: ValidationCheck
) -> list[CorrectionTargetView]:
    curriculum = result.curriculum
    items = (
        curriculum.profile_statements
        + curriculum.program_requirements
        + curriculum.credit_allocations
        + curriculum.designated_courses
        + curriculum.major_courses
    )
    by_uid = {item.uid: item for item in items}
    views: list[CorrectionTargetView] = []
    for encoded_target in check.correction_targets:
        match = _TARGET_PATTERN.fullmatch(encoded_target)
        if match is None:
            continue
        uid = match.group("uid")
        field = match.group("field")
        item = by_uid.get(uid)
        if item is None or field not in type(item).model_fields:
            continue
        views.append(
            CorrectionTargetView(
                uid=uid,
                field=field,
                label=_FIELD_LABELS.get(field, field),
                current_value=getattr(item, field),
            )
        )
    return views
