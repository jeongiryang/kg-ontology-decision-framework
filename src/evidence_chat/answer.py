"""QueryResponse를 화면에 띄울 한국어 답변으로 구성한다.

값을 추정하거나 보간하지 않는다. 질의 계층이 준 값만 사람이 읽는 문장으로 바꾸고,
확정 답변이 불가한 상태는 그 사실을 그대로 표시한다.
"""

from __future__ import annotations

from typing import Any

from kg_builder.query_contracts import Answerability, Intent


SEMESTER_LABELS = {
    "FIRST": "1학기",
    "SECOND": "2학기",
    "BOTH": "1·2학기",
    "SUMMER": "하계 계절수업",
    "WINTER": "동계 계절수업",
}
COMPLETION_TYPE_LABELS = {
    "GENERAL_REQUIRED": "교양필수",
    "GENERAL_ELECTIVE": "교양선택",
    "MAJOR_REQUIRED": "전공필수",
    "MAJOR_ELECTIVE": "전공선택",
    "FREE_ELECTIVE": "자유선택",
}
UNIT_LABELS = {
    "CREDIT": "학점",
    "COURSE": "과목",
    "COURSE_PER_AREA": "영역별 과목",
}
ANSWERABILITY_LABELS = {
    Answerability.ANSWERABLE: "확정 답변",
    Answerability.CLARIFICATION_REQUIRED: "추가 정보 필요",
    Answerability.UNRESOLVED: "검토 보류",
    Answerability.OUT_OF_SCOPE: "지원 범위 밖",
    Answerability.NOT_FOUND: "해당 사실 없음",
}
INTENT_LABELS = {
    Intent.GET_GENERAL_EDUCATION_MIN_CREDITS: "교양 최소 이수학점",
    Intent.GET_BALANCED_GENERAL_REQUIREMENT: "균형교양 이수요건",
    Intent.GET_TRANSFER_GENERAL_EXEMPTION: "편입생 교양 면제",
    Intent.GET_COURSE_OFFERING: "과목 편성 조회",
    Intent.GET_MAJOR_REQUIRED_COURSES: "전공필수 과목 목록",
    Intent.GET_COURSE_COMPLETION_TYPE: "과목 이수구분",
}


def _semester(value: Any) -> str:
    return SEMESTER_LABELS.get(value, str(value)) if value is not None else "미지정"


def _grade_year(value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return "개설 학년 미표기"
        return "·".join(f"{item}학년" for item in value)
    if value is None:
        return "개설 학년 미표기"
    return f"{value}학년"


def _completion_type(value: Any) -> str:
    return COMPLETION_TYPE_LABELS.get(value, str(value))


def _unit(value: Any) -> str:
    """통제어휘 단위 코드를 한국어로 바꾼다. 모르는 코드는 그대로 노출한다."""
    if value is None:
        return "학점"
    return UNIT_LABELS.get(value, str(value))


def compose(response: dict[str, Any]) -> dict[str, Any]:
    """질의 응답을 headline, detail 목록, 상태 라벨로 바꾼다."""
    intent = Intent(response["intent"])
    answerability = Answerability(response["answerability"])
    answer = response.get("answer") or {}
    builders = {
        Intent.GET_GENERAL_EDUCATION_MIN_CREDITS: _general_min_credits,
        Intent.GET_BALANCED_GENERAL_REQUIREMENT: _balanced_general,
        Intent.GET_TRANSFER_GENERAL_EXEMPTION: _transfer_exemption,
        Intent.GET_COURSE_OFFERING: _course_offering,
        Intent.GET_MAJOR_REQUIRED_COURSES: _major_required,
        Intent.GET_COURSE_COMPLETION_TYPE: _course_completion_type,
    }
    if answerability is Answerability.ANSWERABLE:
        headline, details = builders[intent](answer)
    else:
        headline, details = _non_answerable(intent, answerability, answer)
    return {
        "intent": intent.value,
        "intent_label": INTENT_LABELS[intent],
        "answerability": answerability.value,
        "answerability_label": ANSWERABILITY_LABELS[answerability],
        "is_confirmed": answerability is Answerability.ANSWERABLE,
        "headline": headline,
        "details": details,
        "warnings": list(response.get("warnings") or ()),
        "scope": response.get("scope") or {},
    }


def _general_min_credits(answer: dict[str, Any]) -> tuple[str, list[str]]:
    requirement = answer.get("requirement") or {}
    value = requirement.get("value")
    headline = f"교양은 최소 {value}{_unit(requirement.get('unit'))}을 이수해야 합니다."
    details = []
    if requirement.get("description_ko"):
        details.append(requirement["description_ko"])
    if requirement.get("rule_id"):
        details.append(f"규칙 ID: {requirement['rule_id']}")
    return headline, details


def _balanced_general(answer: dict[str, Any]) -> tuple[str, list[str]]:
    requirements = answer.get("requirements") or []
    credit_rule = next(
        (item for item in requirements if isinstance(item.get("value"), int)), None
    )
    headline = (
        f"균형교양은 최소 {credit_rule['value']}{_unit(credit_rule.get('unit'))}을 이수해야 합니다."
        if credit_rule
        else "균형교양 이수요건을 조회했습니다."
    )
    details = []
    for item in requirements:
        if item.get("description_ko"):
            details.append(item["description_ko"])
        areas = item.get("areas") or []
        if areas:
            names = ", ".join(area.get("name_ko", area.get("area_id", "")) for area in areas)
            details.append(f"대상 영역 {len(areas)}개: {names}")
    return headline, details


def _transfer_exemption(answer: dict[str, Any]) -> tuple[str, list[str]]:
    rule = answer.get("rule") or {}
    exempt = answer.get("exempt")
    headline = (
        "편입생은 교양 이수요건 면제 대상입니다."
        if exempt
        else "편입생 교양 면제 규칙을 확인했습니다."
    )
    details = []
    if rule.get("description_ko"):
        details.append(rule["description_ko"])
    if rule.get("rule_id"):
        details.append(f"규칙 ID: {rule['rule_id']}")
    return headline, details


def _course_offering(answer: dict[str, Any]) -> tuple[str, list[str]]:
    course = answer.get("course") or {}
    offerings = answer.get("offerings") or []
    name = course.get("course_name", "해당 과목")
    code = course.get("course_code", "")
    if len(offerings) == 1:
        offering = offerings[0]
        headline = (
            f"{name}({code})은 {_grade_year(offering.get('grade_year'))} "
            f"{_semester(offering.get('semester'))}에 개설되며 {offering.get('credits')}학점입니다."
        )
    else:
        headline = f"{name}({code})의 편성 {len(offerings)}건을 찾았습니다."
    details = []
    for offering in offerings:
        details.append(
            f"{_grade_year(offering.get('grade_year'))} {_semester(offering.get('semester'))} · "
            f"{offering.get('credits')}학점 · {_completion_type(offering.get('completion_type'))} · "
            f"이론 {offering.get('lecture_hours')} / 실기 {offering.get('practice_hours')}"
        )
    return headline, details


def _major_required(answer: dict[str, Any]) -> tuple[str, list[str]]:
    courses = answer.get("courses") or []
    headline = (
        f"전공필수는 {answer.get('course_count', len(courses))}과목 "
        f"{answer.get('total_credits')}학점입니다."
    )
    details = [
        f"{item.get('course_name')}({item.get('course_code')}) · "
        f"{_grade_year(item.get('grade_year'))} {_semester(item.get('semester'))} · "
        f"{item.get('credits')}학점"
        for item in sorted(
            courses,
            key=lambda row: (
                (row.get("grade_year") or [99])[0]
                if isinstance(row.get("grade_year"), list)
                else (row.get("grade_year") or 99),
                str(row.get("semester")),
                str(row.get("course_code")),
            ),
        )
    ]
    return headline, details


def _course_completion_type(answer: dict[str, Any]) -> tuple[str, list[str]]:
    course = answer.get("course") or {}
    types = answer.get("completion_types") or []
    labels = ", ".join(_completion_type(item) for item in types) or "확인되지 않음"
    headline = (
        f"{course.get('course_name')}({course.get('course_code')})의 이수구분은 {labels}입니다."
    )
    return headline, [f"원본 통제어휘 값: {', '.join(types)}"] if types else []


def _non_answerable(
    intent: Intent, answerability: Answerability, answer: dict[str, Any]
) -> tuple[str, list[str]]:
    candidates = answer.get("candidates") or []
    if answerability is Answerability.CLARIFICATION_REQUIRED:
        headline = "같은 과목명에 학수번호가 여러 개 있습니다. 학수번호를 지정해 주세요."
        details = [
            f"{item.get('course_name')} ({item.get('course_code')})" for item in candidates
        ]
        return headline, details
    if answerability is Answerability.UNRESOLVED:
        headline = "이 항목은 아직 검토 보류 상태여서 확정 답변을 드릴 수 없습니다."
        details = [
            f"{item.get('course_name')} ({item.get('course_code')}): {item.get('reason') or '보류 사유 미기재'}"
            for item in candidates
        ] or ["Verified KG의 unresolved 목록에 남아 있는 항목입니다."]
        return headline, details
    if answerability is Answerability.OUT_OF_SCOPE:
        return "현재 지원 범위를 벗어난 질문입니다.", []
    return "Verified KG에서 해당 사실을 찾지 못했습니다.", []
