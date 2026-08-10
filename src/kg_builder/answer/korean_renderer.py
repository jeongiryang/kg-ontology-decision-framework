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

    def render(self, validated: ValidatedClaims) -> RenderedAnswer:
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
        else:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "Claim combination is unsupported"
            )
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
        if "grade_year" in by_field and "semester" in by_field:
            grades = by_field["grade_year"].value
            grades = grades if isinstance(grades, tuple) else (grades,)
            grade_text = "·".join(str(item) for item in grades) + "학년"
            semester = ENUM_KO.get(by_field["semester"].value)
            if semester is None:
                raise GroundingError("ANSWER_RENDERING_UNSUPPORTED", "unsupported semester")
            parts.append(f"{subject.display_name}는 {grade_text} {semester}에 개설됩니다.")
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
            parts.append(f"{subject.display_name}은 {by_field['credits'].value}학점입니다.")
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
        course_text = ", ".join(
            f"{item.display_name}({item.credits}학점)"
            if item.credits is not None
            else item.display_name
            for item in items
        )
        result = f"{label} 과목은 {course_text}로 총 {aggregates['fact_count'].value}과목"
        if "credits_sum" in aggregates:
            result += f"이며 합계 {aggregates['credits_sum'].value}학점"
        return result + "입니다."
