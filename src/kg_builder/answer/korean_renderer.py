"""Deterministically render validated Claims without an answer-writing model."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass, field
from typing import Sequence

from .claim_validator import ValidatedClaims
from .contracts import ClaimPolarity, ClaimType, GroundedClaim, GroundingError


ENUM_KO = {
    "GENERAL_REQUIRED": "교양필수",
    "GENERAL_ELECTIVE": "교양선택",
    "MAJOR_REQUIRED": "전공필수",
    "MAJOR_ELECTIVE": "전공선택",
    "FREE_ELECTIVE": "자유선택",
    "FIRST": "1학기",
    "SECOND": "2학기",
    "BOTH": "1·2학기",
    "SUMMER": "하계 계절수업",
    "WINTER": "동계 계절수업",
}
ROADMAP_ENTRY_KO = {
    "COURSE": "권장 교과목",
    "EXTRACURRICULAR": "권장 비교과 활동",
    "GUIDANCE": "로드맵 유의사항",
}
# 헤더에 조사를 함께 둔다. 조사를 자동 판정하면 원문 표기와 어긋날 수 있다.
NARRATIVE_HEADERS = {
    "education_goals": "학과 교육목표는",
    "talent_profiles": "학과 인재상은",
    "career_fields": "졸업 후 진출 분야는",
    "university_goals": "대학 교육목표는",
}
COMPETENCY_HEADERS = {
    "major_competencies": "학과 전공능력은",
    "university_competencies": "대학 핵심역량은",
}
AGGREGATE_TYPE_KO = {
    "MAJOR_COMPETENCY_COURSE_CREDIT": "전공능력별 과목 수와 학점",
    "MAJOR_TOTAL_COURSE_CREDIT": "전체 전공과목 수와 학점",
    "MINIMUM_MAJOR_CREDIT_SYSTEM": "최소전공학점제 시행 여부",
    "MAJOR_OFFERING_WORKLOAD": "전공 개설 시수",
}
ALIGNMENT_STRENGTH_KO = {
    "HIGH": "연계성 높음",
    "LOW": "연계성 적음",
    "NONE": "연계 없음",
}
# 연계표는 출발 쪽이 무엇인지에 따라 읽는 방향이 달라진다. Claim 필드명으로 머리말을
# 고르며, 필드명은 selection mode 가 정하므로 대학 것과 학과 것이 섞이지 않는다.
ALIGNMENT_HEADERS = {
    "goal_competency_alignments": ("학과 교육목표", "전공능력"),
    "core_competency_alignments": ("대학 핵심역량", "전공능력"),
    "goal_alignments": ("대학 교육목표", "학과 교육목표"),
}
_INTERNAL_TOKEN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:Cypher|MATCH|RETURN|CREATE|DELETE|MERGE|INSERT|SET|REMOVE|DROP|ALTER|CALL|APOC)(?![A-Za-z0-9_])"
)
_SECRET_TEXT = re.compile(r"(?i)(system\s*prompt|api\s*key|비밀번호|토큰)")
_RENDER_SEAL = object()
_RENDER_KEY = secrets.token_bytes(32)


def _render_digest(answer_text: str, validated: ValidatedClaims) -> str:
    payload = repr((answer_text, validated._approval)).encode("utf-8")
    return hmac.new(_RENDER_KEY, payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class RenderedAnswer:
    """Renderer-issued answer bound to one approved ValidatedClaims value."""

    answer_text: str
    validated_claims: ValidatedClaims
    _approval: str = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        answer_text: str,
        validated_claims: ValidatedClaims,
        *,
        _approval: str = "",
        _seal: object | None = None,
    ) -> None:
        expected = _render_digest(answer_text, validated_claims)
        if (
            _seal is not _RENDER_SEAL
            or not validated_claims._is_approved()
            or not hmac.compare_digest(_approval, expected)
        ):
            raise TypeError("RenderedAnswer can only be issued by KoreanAnswerRenderer")
        object.__setattr__(self, "answer_text", answer_text)
        object.__setattr__(self, "validated_claims", validated_claims)
        object.__setattr__(self, "_approval", _approval)
        object.__setattr__(self, "_seal", _seal)

    @classmethod
    def _issue(cls, answer_text: str, validated: ValidatedClaims) -> "RenderedAnswer":
        return cls(
            answer_text,
            validated,
            _approval=_render_digest(answer_text, validated),
            _seal=_RENDER_SEAL,
        )

    def _is_approved(self) -> bool:
        if self._seal is not _RENDER_SEAL or not self.validated_claims._is_approved():
            return False
        return hmac.compare_digest(
            self._approval, _render_digest(self.answer_text, self.validated_claims)
        )

    @property
    def claims(self) -> tuple[GroundedClaim, ...]:
        return self.validated_claims.claims

    @property
    def used_fact_ids(self) -> tuple[str, ...]:
        return self.validated_claims.fact_ids

    @property
    def used_evidence_ids(self) -> tuple[str, ...]:
        return self.validated_claims.evidence_ids


class KoreanAnswerRenderer:
    def __init__(self, *, max_answer_chars: int = 8_000):
        self.max_answer_chars = max_answer_chars

    def render(
        self, validated: ValidatedClaims, *, notice: str | None = None
    ) -> RenderedAnswer:
        if not isinstance(validated, ValidatedClaims) or not validated._is_approved():
            raise GroundingError(
                "ANSWER_CLAIM_APPROVAL_REQUIRED",
                "renderer accepts only ClaimValidator-issued ValidatedClaims",
            )
        claims = validated.claims
        if not claims:
            raise GroundingError("ANSWER_CLAIM_EMPTY", "cannot render empty Claims")
        kinds = {claim.claim_type for claim in claims}
        if ClaimType.COURSE_LIST in kinds:
            text = self._course_list(claims)
        elif kinds <= {
            ClaimType.NUMERIC_REQUIREMENT,
            ClaimType.BOOLEAN_POLICY,
            ClaimType.VERIFIED_RULE_TEXT,
        }:
            text = self._rules(claims)
        elif kinds == {ClaimType.FIELD_VALUE}:
            text = self._single_course(claims)
        elif kinds == {ClaimType.ALLOCATION_LIST}:
            text = self._allocations(claims)
        elif kinds == {ClaimType.ROADMAP_LIST}:
            text = self._roadmap(claims)
        elif kinds == {ClaimType.NARRATIVE_LIST}:
            text = self._narrative(claims)
        elif kinds == {ClaimType.RECOMMENDATION_LIST}:
            text = self._recommendations(claims)
        elif kinds == {ClaimType.COMPETENCY_LIST}:
            text = self._competencies(claims)
        elif kinds == {ClaimType.AGGREGATE_LIST}:
            text = self._aggregates(claims)
        elif kinds == {ClaimType.ALIGNMENT_LIST}:
            text = self._alignments(claims)
        else:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "Claim combination is unsupported"
            )
        # 조회 범위를 좁히지 못해 넓게 답한 경우 그 사실을 답변 앞에 밝힌다. 문구는
        # 통제 코드에서 Python 이 만들며 모델 문장을 쓰지 않는다.
        if notice:
            text = f"{notice}\n\n{text}"
        if len(text) > self.max_answer_chars:
            raise GroundingError("ANSWER_TOO_LARGE", "rendered answer is too large")
        if _INTERNAL_TOKEN.search(text) or _SECRET_TEXT.search(text):
            raise GroundingError(
                "ANSWER_INTERNAL_DISCLOSURE", "rendered answer contains internal syntax"
            )
        return RenderedAnswer._issue(text, validated)

    @staticmethod
    def _rules(claims: Sequence[GroundedClaim]) -> str:
        ordered = sorted(
            claims,
            key=lambda c: (
                {"COURSE_PER_AREA": 0, "COURSE": 1, "AREA": 2, "CREDIT": 3}.get(c.unit or "", 9),
                c.claim_id,
            ),
        )
        parts: list[str] = []
        for claim in ordered:
            if not claim.description_ko:
                raise GroundingError(
                    "ANSWER_RENDERING_UNSUPPORTED", "Rule Claim has no verified description"
                )
            if claim.claim_type is ClaimType.BOOLEAN_POLICY and not (
                claim.value is True and claim.polarity is ClaimPolarity.EXEMPT
            ):
                raise GroundingError(
                    "ANSWER_RENDERING_UNSUPPORTED", "unsupported Boolean polarity"
                )
            parts.append(claim.description_ko.rstrip(".。") + ".")
        return " ".join(parts)

    @staticmethod
    def _single_course(claims: Sequence[GroundedClaim]) -> str:
        subjects = {claim.subject for claim in claims}
        if len(subjects) != 1 or None in subjects:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "single-course Claims need one subject"
            )
        subject = next(iter(subjects))
        by_field = {claim.field: claim for claim in claims}
        if len(by_field) != len(claims):
            raise GroundingError("ANSWER_CLAIM_DUPLICATE", "duplicate single-course field")
        parts: list[str] = []
        if "course_code" in by_field:
            parts.append(
                f"{subject.display_name}의 학수번호는 {by_field['course_code'].value}입니다."
            )
        if "grade_year" in by_field and "semester" in by_field:
            grades = by_field["grade_year"].value
            grades = grades if isinstance(grades, tuple) else (grades,)
            grade_text = "·".join(str(item) for item in grades) + "학년"
            semester = ENUM_KO.get(by_field["semester"].value)
            if semester is None:
                raise GroundingError("ANSWER_RENDERING_UNSUPPORTED", "unsupported semester")
            parts.append(
                f"{subject.display_name}{_particle(subject.display_name, '은', '는')} "
                f"{grade_text} {semester}에 개설됩니다."
            )
        elif "grade_year" in by_field or "semester" in by_field:
            field = "grade_year" if "grade_year" in by_field else "semester"
            claim = by_field[field]
            if field == "grade_year":
                grades = claim.value if isinstance(claim.value, tuple) else (claim.value,)
                parts.append(
                    f"{subject.display_name}의 개설 학년은 "
                    + "·".join(str(item) for item in grades)
                    + "학년입니다."
                )
            else:
                label = ENUM_KO.get(claim.value)
                if label is None:
                    raise GroundingError("ANSWER_RENDERING_UNSUPPORTED", "unsupported semester")
                parts.append(f"{subject.display_name}의 개설 학기는 {label}입니다.")
        if "completion_type" in by_field:
            label = ENUM_KO.get(by_field["completion_type"].value)
            if label is None:
                raise GroundingError(
                    "ANSWER_RENDERING_UNSUPPORTED", "unsupported completion_type"
                )
            parts.append(f"{subject.display_name}의 이수구분은 {label}입니다.")
        if "credits" in by_field:
            parts.append(
                f"{subject.display_name}{_particle(subject.display_name, '은', '는')} "
                f"{by_field['credits'].value}학점입니다."
            )
        if not parts:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "no renderable single-course field"
            )
        return " ".join(parts)

    @staticmethod
    def _course_list(claims: Sequence[GroundedClaim]) -> str:
        lists = [claim for claim in claims if claim.claim_type is ClaimType.COURSE_LIST]
        if len(lists) != 1:
            raise GroundingError("ANSWER_CLAIM_INVALID", "one course-list Claim is required")
        items = lists[0].value
        aggregates = {
            claim.field: claim
            for claim in claims
            if claim.claim_type is ClaimType.AGGREGATE
        }
        if "fact_count" not in aggregates:
            raise GroundingError("ANSWER_RENDERING_UNSUPPORTED", "course count is required")
        scope = next(
            (claim for claim in claims if claim.field == "completion_type"), None
        )
        label = ENUM_KO.get(scope.value, "해당") if scope else "해당"
        def item_text(item) -> str:
            details: list[str] = []
            # course_code is present only when the approved QueryPlan requested it;
            # ordinary offering lists therefore remain unchanged.
            if item.course_code:
                details.append(item.course_code)
            # Every list item is rendered under its completion-type heading below;
            # repeating the same value in parentheses makes long lists unreadable.
            if item.grade_year not in (None, (), []) and scope is None:
                years = (
                    item.grade_year
                    if isinstance(item.grade_year, (list, tuple))
                    else (item.grade_year,)
                )
                details.append("·".join(str(value) for value in years) + "학년")
            if item.semester and scope is None:
                semesters = (
                    item.semester
                    if isinstance(item.semester, (list, tuple))
                    else (item.semester,)
                )
                details.append(
                    "·".join(ENUM_KO.get(value, str(value)) for value in semesters)
                )
            if item.credits is not None:
                details.append(f"{item.credits}학점")
            return (
                f"{item.display_name}({', '.join(details)})"
                if details
                else item.display_name
            )

        unique_items = list({item.entity_id: item for item in items}.values())
        group_by_area = any(item.area_name for item in unique_items)
        groups: dict[str, list] = {}
        for item in unique_items:
            group = (
                item.area_name
                if group_by_area and item.area_name
                else ENUM_KO.get(item.completion_type, item.completion_type or label)
            )
            groups.setdefault(group, []).append(item)
        lines = [
            "조회한 과목을 영역별로 정리했습니다."
            if group_by_area
            else "조회한 과목을 이수구분별로 정리했습니다."
        ]
        for group, group_items in groups.items():
            lines.extend(("", f"{group} ({len(group_items)}과목)"))
            lines.extend(f"- {item_text(item)}" for item in group_items)
        course_count = aggregates.get("unique_course_count", aggregates["fact_count"])
        total = f"총 {course_count.value}과목입니다."
        if "credits_sum" in aggregates:
            total = (
                f"총 {course_count.value}과목, "
                f"{aggregates['credits_sum'].value}학점입니다."
            )
        lines.extend(("", total))
        return "\n".join(lines)

    @staticmethod
    def _allocations(claims: Sequence[GroundedClaim]) -> str:
        """Render the verified credit-allocation table without summing anything.

        합계는 원문이 ``is_total`` 행으로 따로 제공한다. 항목을 더해 만든 값은 원문에
        근거가 없으므로 여기서도, Claim 단계에서도 계산하지 않는다.
        """

        claim = _single_list_claim(claims, ClaimType.ALLOCATION_LIST)
        groups: dict[str, list] = {}
        for item in claim.value:
            groups.setdefault(item.credit_category, []).append(item)
        parts: list[str] = []
        for category, items in groups.items():
            pieces: list[str] = []
            for item in items:
                credits = f"{item.allocated_credits}학점"
                period = _period_text(item.grade_year, item.semester)
                if item.is_total is True:
                    pieces.append(f"합계 {credits}")
                elif period:
                    pieces.append(f"{period} {credits}")
                else:
                    pieces.append(credits)
            parts.append(f"{category} 배정 학점은 " + ", ".join(pieces) + "입니다.")
        return " ".join(parts)

    @staticmethod
    def _roadmap(claims: Sequence[GroundedClaim]) -> str:
        claim = _single_list_claim(claims, ClaimType.ROADMAP_LIST)
        groups: dict[tuple[str, str], list[str]] = {}
        for item in claim.value:
            kind = ROADMAP_ENTRY_KO.get(item.entry_type)
            if kind is None:
                raise GroundingError(
                    "ANSWER_RENDERING_UNSUPPORTED", "unsupported roadmap entry type"
                )
            groups.setdefault(
                (_period_text(item.grade_year, item.semester), kind), []
            ).append(item.raw_label)
        parts: list[str] = []
        for (period, kind), labels in groups.items():
            prefix = f"{period} " if period else ""
            # ROADMAP_ENTRY_KO 의 값은 모두 받침으로 끝나므로 보조사는 '은'으로 고정된다.
            parts.append(f"{prefix}{kind}은 " + ", ".join(labels) + "입니다.")
        return " ".join(parts)

    @staticmethod
    def _narrative(claims: Sequence[GroundedClaim]) -> str:
        claim = _single_list_claim(claims, ClaimType.NARRATIVE_LIST)
        header = NARRATIVE_HEADERS.get(claim.field)
        if header is None:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "unsupported narrative Claim"
            )
        items = claim.value
        orders = {item.order is not None for item in items}
        if len(orders) != 1:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "narrative order is inconsistent"
            )
        if len(items) == 1:
            return f"{header} {items[0].text}입니다."
        # 원문 순번을 그대로 쓴다. 일부만 조회된 경우에도 표시 번호가 원문과 어긋나지
        # 않아야 하므로 새로 매기지 않는다.
        numbered = items[0].order is not None
        body = " ".join(
            f"{item.order}) {item.text}" if numbered else f"{item.text}."
            for item in items
        )
        return f"{header} 다음과 같습니다. {body}"

    @staticmethod
    def _recommendations(claims: Sequence[GroundedClaim]) -> str:
        claim = _single_list_claim(claims, ClaimType.RECOMMENDATION_LIST)
        pieces: list[str] = []
        for item in claim.value:
            details: list[str] = []
            if item.course_code:
                details.append(item.course_code)
            period = _period_text(item.recommended_grade_year, item.recommended_semester)
            if period:
                details.append(period)
            if item.credits is not None:
                details.append(f"{item.credits}학점")
            if item.area_raw:
                details.append(item.area_raw)
            pieces.append(
                f"{item.course_name_ko}({', '.join(details)})" if details else item.course_name_ko
            )
        return "학과 권장 교양 과목은 " + ", ".join(pieces) + "입니다."


    @staticmethod
    def _competencies(claims: Sequence[GroundedClaim]) -> str:
        claim = _single_list_claim(claims, ClaimType.COMPETENCY_LIST)
        header = COMPETENCY_HEADERS.get(claim.field)
        if header is None:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "unsupported competency Claim"
            )
        pieces = [
            f"{item.name_ko}({item.description_ko})" if item.description_ko else item.name_ko
            for item in claim.value
        ]
        return f"{header} " + ", ".join(pieces) + "입니다."

    @staticmethod
    def _aggregates(claims: Sequence[GroundedClaim]) -> str:
        claim = _single_list_claim(claims, ClaimType.AGGREGATE_LIST)
        lines: list[str] = []
        for item in claim.value:
            kind = AGGREGATE_TYPE_KO.get(item.aggregate_type)
            if kind is None:
                raise GroundingError(
                    "ANSWER_RENDERING_UNSUPPORTED", "unsupported aggregate type"
                )
            # 값이 있는 수치만 문장에 넣는다. 비어 있는 칸을 0 으로 바꾸지 않는다.
            details: list[str] = []
            if item.course_count is not None:
                details.append(f"{item.course_count}과목")
            if item.credit_value is not None:
                details.append(f"{item.credit_value}학점")
            if item.lecture_hours is not None:
                details.append(f"이론 {item.lecture_hours}시간")
            if item.practice_hours is not None:
                details.append(f"실습 {item.practice_hours}시간")
            if item.boolean_value is not None:
                details.append("시행함" if item.boolean_value else "시행하지 않음")
            subject = f"{kind}({item.name_ko})" if item.name_ko else kind
            if not details:
                raise GroundingError(
                    "ANSWER_CLAIM_INVALID", "aggregate row carries no verified value"
                )
            anchor = item.name_ko or kind
            lines.append(
                f"{subject}{_particle(anchor, '은', '는')} " + ", ".join(details) + "입니다."
            )
        return " ".join(lines)

    @staticmethod
    def _alignments(claims: Sequence[GroundedClaim]) -> str:
        claim = _single_list_claim(claims, ClaimType.ALIGNMENT_LIST)
        header = ALIGNMENT_HEADERS.get(claim.field)
        if header is None:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "unsupported alignment Claim"
            )
        source_kind, target_kind = header
        # 출발 항목별로 묶어 읽는다. 항목 순서는 ClaimBuilder 가 이미 확정했으므로
        # 여기서 다시 정렬하지 않는다.
        grouped: dict[tuple[str, str], list[str]] = {}
        for item in claim.value:
            source = getattr(item, "source_text", None) or item.normalized_name_ko
            strength = ALIGNMENT_STRENGTH_KO.get(item.strength)
            if strength is None:
                raise GroundingError(
                    "ANSWER_RENDERING_UNSUPPORTED", "unsupported alignment strength"
                )
            grouped.setdefault((source, strength), []).append(item.name_ko)
        lines = [
            f"{source_kind} '{source}'{_particle(source, '과', '와')} "
            f"{strength}인 {target_kind}{_particle(target_kind, '은', '는')} "
            + ", ".join(targets)
            + "입니다."
            for (source, strength), targets in grouped.items()
        ]
        return " ".join(lines)


def _single_list_claim(
    claims: Sequence[GroundedClaim], claim_type: ClaimType
) -> GroundedClaim:
    matched = [claim for claim in claims if claim.claim_type is claim_type]
    if len(matched) != 1 or not isinstance(matched[0].value, tuple) or not matched[0].value:
        raise GroundingError("ANSWER_CLAIM_INVALID", "one non-empty list Claim is required")
    return matched[0]


# 숫자로 끝나는 표기는 읽는 소리로 받침을 정한다. 과목명에 붙는 일련번호가 그렇다
# (`산학캡스톤디자인1` → "일" → 받침 ㄹ). 0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔은 받침이
# 있고 2 이, 4 사, 5 오, 9 구는 없다.
_DIGIT_HAS_FINAL = {
    "0": True,
    "1": True,
    "2": False,
    "3": True,
    "4": False,
    "5": False,
    "6": True,
    "7": True,
    "8": True,
    "9": False,
}


def _has_final_consonant(text: str) -> bool | None:
    """Return whether the last Hangul syllable has a final consonant.

    한글 음절은 ``(코드 - 0xAC00) % 28`` 이 0 이 아니면 받침이 있다. 마지막 글자가 한글
    음절도 숫자도 아니면 판정하지 않고 ``None`` 을 돌려준다. 조사를 잘못 붙이는 것보다 두
    형태를 함께 적는 쪽이 원문 표기를 덜 훼손한다.
    """

    if not text:
        return None
    last = text[-1]
    if last in _DIGIT_HAS_FINAL:
        return _DIGIT_HAS_FINAL[last]
    if not "가" <= last <= "힣":
        return None
    return (ord(last) - 0xAC00) % 28 != 0


def _particle(text: str, with_final: str, without_final: str) -> str:
    """Pick the Korean particle that matches the preceding word."""

    final = _has_final_consonant(text)
    if final is None:
        return f"{with_final}({without_final})"
    return with_final if final else without_final


def _period_text(grade_year: object, semester: object) -> str:
    parts: list[str] = []
    if isinstance(grade_year, int) and not isinstance(grade_year, bool):
        parts.append(f"{grade_year}학년")
    if semester is not None:
        label = ENUM_KO.get(semester)
        if label is None:
            raise GroundingError("ANSWER_RENDERING_UNSUPPORTED", "unsupported semester")
        parts.append(label)
    return " ".join(parts)
