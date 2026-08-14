"""Provider-independent contracts for grounded Korean curriculum answers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Sequence


class ChatStatus(StrEnum):
    ANSWERABLE = "ANSWERABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNSUPPORTED = "UNSUPPORTED"
    UNRESOLVED = "UNRESOLVED"
    NOT_FOUND = "NOT_FOUND"
    SAFE_FAILURE = "SAFE_FAILURE"


class ChatErrorCode(StrEnum):
    QUERY_SAFE_FAILURE = "QUERY_SAFE_FAILURE"
    # 질의 파이프라인의 어느 관문에서 멈췄는지 남긴다. 사용자 문구는 단계마다 달라도
    # 근거 없는 내용을 만들지 않는다는 점은 같고, 운영자는 이 코드로 원인을 좁힌다.
    QUERY_PLANNING_FAILED = "QUERY_PLANNING_FAILED"
    QUERY_SCHEMA_SELECTION_FAILED = "QUERY_SCHEMA_SELECTION_FAILED"
    QUERY_CYPHER_GENERATION_FAILED = "QUERY_CYPHER_GENERATION_FAILED"
    QUERY_CYPHER_VALIDATION_FAILED = "QUERY_CYPHER_VALIDATION_FAILED"
    QUERY_EXPLAIN_FAILED = "QUERY_EXPLAIN_FAILED"
    QUERY_EXECUTION_FAILED = "QUERY_EXECUTION_FAILED"
    QUERY_RESULT_VALIDATION_FAILED = "QUERY_RESULT_VALIDATION_FAILED"
    ANSWER_CLAIM_VALIDATION_FAILED = "ANSWER_CLAIM_VALIDATION_FAILED"
    ANSWER_RENDERING_UNSUPPORTED = "ANSWER_RENDERING_UNSUPPORTED"
    UNKNOWN_SAFE_FAILURE = "UNKNOWN_SAFE_FAILURE"


# 질의 단계 이름 -> 오류 코드. 단계 이름은 QueryTrace 와 자연어 서비스가 쓰는 값이다.
QUERY_STAGE_ERROR_CODES: dict[str, ChatErrorCode] = {
    "PLANNING": ChatErrorCode.QUERY_PLANNING_FAILED,
    "PLAN_VALIDATION": ChatErrorCode.QUERY_PLANNING_FAILED,
    "SCHEMA_SELECTION": ChatErrorCode.QUERY_SCHEMA_SELECTION_FAILED,
    "CYPHER_GENERATION": ChatErrorCode.QUERY_CYPHER_GENERATION_FAILED,
    "CYPHER_VALIDATION": ChatErrorCode.QUERY_CYPHER_VALIDATION_FAILED,
    "NEO4J_EXPLAIN": ChatErrorCode.QUERY_EXPLAIN_FAILED,
    "EXECUTION": ChatErrorCode.QUERY_EXECUTION_FAILED,
    "RESULT_VALIDATION": ChatErrorCode.QUERY_RESULT_VALIDATION_FAILED,
}

# 계획 모델이 알려 준 "무엇이 부족한가" 코드의 한국어 표기. 사용자에게 보이는 문장은
# 이 표에서 조립하며, 계획 모델이 쓴 자연어를 그대로 내보내지 않는다.
MISSING_SCOPE_LABELS: dict[str, str] = {
    "ACADEMIC_YEAR": "학년도",
    "DEPARTMENT": "학과",
    "COURSE_IDENTITY": "과목명 또는 학수번호",
    "RULE_TOPIC": "어떤 이수요건을 묻는지",
    "QUESTION_INTENT": "무엇을 알고 싶은지",
}
CLARIFICATION_FALLBACK = (
    "질문을 조금 더 구체적으로 알려 주세요. 학년도, 학과, 과목명 가운데 아는 것을 "
    "함께 적어 주시면 확인해 드릴 수 있습니다."
)


# 선택지를 함께 낼 때 쓰는 질문형 문구. 고를 것이 있으면 "무엇이 부족하다"가 아니라
# 무엇을 고르면 되는지 직접 묻는다. 이 문구도 Python 이 통제 코드에서 고른다.
MISSING_SCOPE_QUESTIONS: dict[str, str] = {
    "ACADEMIC_YEAR": "어느 학년도를 말씀하시나요?",
    "DEPARTMENT": "어느 학과를 말씀하시나요?",
    "COURSE_IDENTITY": "어떤 과목을 말씀하시나요?",
    "RULE_TOPIC": "어떤 이수요건을 말씀하시나요?",
    "QUESTION_INTENT": "무엇을 알고 싶으신가요?",
}


# 선택지가 어떤 필터를 채우는지에 따른 질문 문구. 부족 코드로 물었더라도 실제로
# 제시하는 선택지가 다를 수 있어(고를 것이 없어 "무엇을 알고 싶은지"로 되돌아간
# 경우), 문구는 코드가 아니라 **실제 선택지**를 따라간다.
FILTER_QUESTIONS: dict[str, str] = {
    "academic_year": "어느 학년도를 말씀하시나요?",
    "department_id": "어느 학과를 말씀하시나요?",
    "course_code": "어떤 과목을 말씀하시나요?",
    "rule_ids": "어떤 이수요건을 말씀하시나요?",
    "selection_mode": "무엇을 알고 싶으신가요?",
}


def clarification_message(
    missing: Sequence[str] | None,
    options: Sequence["ClarificationOption"] = (),
) -> str:
    """Build the user-facing prompt from controlled codes, never from model prose."""

    codes = [str(code) for code in (missing or [])]
    if options:
        question = FILTER_QUESTIONS.get(options[0].filter_name)
        if question:
            return question
        for code in codes:
            question = MISSING_SCOPE_QUESTIONS.get(code)
            if question:
                return question
    labels = [MISSING_SCOPE_LABELS[code] for code in codes if code in MISSING_SCOPE_LABELS]
    if not labels:
        return CLARIFICATION_FALLBACK
    return "질문을 확인하려면 다음 정보가 더 필요합니다: " + ", ".join(labels) + "."


class ClaimType(StrEnum):
    FIELD_VALUE = "FIELD_VALUE"
    NUMERIC_REQUIREMENT = "NUMERIC_REQUIREMENT"
    BOOLEAN_POLICY = "BOOLEAN_POLICY"
    VERIFIED_RULE_TEXT = "VERIFIED_RULE_TEXT"
    COURSE_LIST = "COURSE_LIST"
    AGGREGATE = "AGGREGATE"
    # 확장 fact family 의 Claim. 값은 모두 승인된 행에서 그대로 오고, 아래 어느
    # 항목도 계산·추론으로 만들어진 값을 담지 않는다.
    ALLOCATION_LIST = "ALLOCATION_LIST"
    ROADMAP_LIST = "ROADMAP_LIST"
    NARRATIVE_LIST = "NARRATIVE_LIST"
    RECOMMENDATION_LIST = "RECOMMENDATION_LIST"
    COMPETENCY_LIST = "COMPETENCY_LIST"
    AGGREGATE_LIST = "AGGREGATE_LIST"
    ALIGNMENT_LIST = "ALIGNMENT_LIST"


class ClaimPolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    EXEMPT = "EXEMPT"


class GroundingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True, order=True)
class FactEvidenceLink:
    fact_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class ClaimSubject:
    entity_id: str
    display_name: str


@dataclass(frozen=True, slots=True)
class CourseClaimItem:
    fact_id: str
    entity_id: str
    display_name: str
    course_code: str | None
    credits: int | float | None


@dataclass(frozen=True, slots=True)
class AllocationClaimItem:
    """One row of the verified credit-allocation table."""

    fact_id: str
    credit_category: str
    allocated_credits: int | float
    grade_year: int | None = None
    semester: str | None = None
    is_total: bool | None = None


@dataclass(frozen=True, slots=True)
class RoadmapClaimItem:
    """One verified entry of the recommended course roadmap."""

    fact_id: str
    raw_label: str
    entry_type: str
    grade_year: int | None = None
    semester: str | None = None
    is_required: bool | None = None


@dataclass(frozen=True, slots=True)
class NarrativeClaimItem:
    """One verified sentence taken verbatim from the source document."""

    fact_id: str
    text: str
    order: int | None = None


@dataclass(frozen=True, slots=True)
class RecommendationClaimItem:
    """One verified department-recommended general-education course."""

    fact_id: str
    course_name_ko: str
    course_code: str | None = None
    area_raw: str | None = None
    recommended_grade_year: int | None = None
    recommended_semester: str | None = None
    credits: int | float | None = None


@dataclass(frozen=True, slots=True)
class CompetencyClaimItem:
    """One verified competency defined by the department or the university."""

    fact_id: str
    name_ko: str
    competency_type: str | None = None
    description_ko: str | None = None
    normalized_name_ko: str | None = None


@dataclass(frozen=True, slots=True)
class AggregateClaimItem:
    """One verified curriculum aggregate row taken verbatim from the source table.

    ``aggregate_type``마다 채워지는 수치가 다르다. 비어 있는 항목은 ``None``으로 두며,
    렌더러는 값이 있는 것만 문장에 넣는다. 항목을 더해 합계를 만들지 않는다.
    """

    fact_id: str
    aggregate_type: str
    is_total: bool
    name_ko: str | None = None
    course_count: int | None = None
    credit_value: int | float | None = None
    lecture_hours: int | float | None = None
    practice_hours: int | float | None = None
    boolean_value: bool | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class AlignmentClaimItem:
    """One verified cell of an alignment matrix.

    ``source_text``는 출발 쪽 항목의 원문 서술이고 ``name_ko``는 도착 쪽 항목의 이름이다.
    ``source_value``는 원문 표에 적힌 표기(예: ``연계성 높음(◉)``)를 그대로 옮긴 값이다.
    """

    fact_id: str
    alignment_type: str
    strength: str
    source_text: str
    name_ko: str
    source_value: str | None = None


@dataclass(frozen=True, slots=True)
class CompetencyAlignmentClaimItem:
    """One alignment cell whose both ends are competencies.

    양끝이 같은 라벨이라 이름 속성이 겹친다. 출발 쪽은 ``normalized_name_ko``,
    도착 쪽은 ``name_ko`` 로 서로 다른 온톨로지 속성을 쓴다.
    """

    fact_id: str
    alignment_type: str
    strength: str
    normalized_name_ko: str
    name_ko: str
    source_value: str | None = None


@dataclass(frozen=True, slots=True)
class GroundedClaim:
    """A semantic assertion derived exclusively from ResultValidator-approved rows."""

    claim_id: str
    claim_type: ClaimType
    provenance: tuple[FactEvidenceLink, ...]
    field: str
    value: Any
    subject: ClaimSubject | None = None
    unit: str | None = None
    operator: str | None = None
    polarity: ClaimPolarity = ClaimPolarity.POSITIVE
    description_ko: str | None = None

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(sorted({link.fact_id for link in self.provenance}))

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(sorted({link.evidence_id for link in self.provenance}))


@dataclass(frozen=True, slots=True)
class Citation:
    evidence_id: str
    fact_ids: tuple[str, ...]
    excerpt_page: int
    source_pdf_page: int
    printed_page: int
    source_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "fact_ids": list(self.fact_ids),
            "excerpt_page": self.excerpt_page,
            "source_pdf_page": self.source_pdf_page,
            "printed_page": self.printed_page,
            "source_text": self.source_text,
        }


SAFE_FAILURE_MESSAGES: dict[ChatErrorCode, str] = {
    ChatErrorCode.QUERY_SAFE_FAILURE: "요청을 안전하게 처리하지 못했습니다.",
    ChatErrorCode.QUERY_PLANNING_FAILED: (
        "질문을 조회 조건으로 옮기지 못했습니다. 학년도나 학과를 넣어 다시 물어봐 주세요."
    ),
    ChatErrorCode.QUERY_SCHEMA_SELECTION_FAILED: (
        "이 질문에 맞는 데이터 범위를 찾지 못했습니다. 현재 범위 안의 항목인지 확인해 주세요."
    ),
    ChatErrorCode.QUERY_CYPHER_GENERATION_FAILED: (
        "조회문을 만들지 못했습니다. 잠시 후 다시 시도해 주세요."
    ),
    ChatErrorCode.QUERY_CYPHER_VALIDATION_FAILED: (
        "생성된 조회문이 안전 규칙을 통과하지 못해 실행하지 않았습니다."
    ),
    ChatErrorCode.QUERY_EXPLAIN_FAILED: (
        "조회 계획 점검에서 중단했습니다. 데이터베이스 상태를 확인해 주세요."
    ),
    ChatErrorCode.QUERY_EXECUTION_FAILED: (
        "데이터베이스 조회에 실패했습니다. 연결 상태를 확인해 주세요."
    ),
    ChatErrorCode.QUERY_RESULT_VALIDATION_FAILED: (
        "조회 결과가 근거 검증을 통과하지 못해 답변하지 않았습니다. "
        "원문에 값이 비어 있거나 검증되지 않은 항목일 수 있습니다."
    ),
    ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED: "답변의 근거를 검증하지 못했습니다.",
    ChatErrorCode.ANSWER_RENDERING_UNSUPPORTED: "현재 조회 결과는 안전한 답변 형식으로 제공할 수 없습니다.",
    ChatErrorCode.UNKNOWN_SAFE_FAILURE: "요청을 안전하게 처리하지 못했습니다.",
}
NON_ANSWERABLE_MESSAGES: dict[ChatStatus, str] = {
    ChatStatus.CLARIFICATION_REQUIRED: "질문을 정확히 확인하려면 추가 정보가 필요합니다.",
    ChatStatus.OUT_OF_SCOPE: "현재 데이터 범위에서는 답변할 수 없습니다.",
    ChatStatus.UNSUPPORTED: "현재 지원하지 않는 질문 유형입니다.",
    ChatStatus.UNRESOLVED: "원문 확인이나 정책 결정이 필요한 항목이므로 확정해서 답변할 수 없습니다.",
    ChatStatus.NOT_FOUND: "현재 검증된 데이터에서 일치하는 결과를 찾지 못했습니다.",
}
_RESPONSE_SEAL = object()
_RESPONSE_KEY = secrets.token_bytes(32)


def normalize_error_code(error_code: ChatErrorCode | str | None) -> ChatErrorCode:
    if isinstance(error_code, ChatErrorCode):
        return error_code
    if isinstance(error_code, str):
        try:
            return ChatErrorCode(error_code)
        except ValueError:
            pass
    return ChatErrorCode.UNKNOWN_SAFE_FAILURE


def safe_failure_message(error_code: ChatErrorCode | str | None) -> str:
    return SAFE_FAILURE_MESSAGES[normalize_error_code(error_code)]


@dataclass(frozen=True, slots=True)
class ClarificationOption:
    """One data-derived choice that completes a missing query scope.

    ``value``는 계획의 필터에 그대로 들어갈 값이고 ``label``은 사용자에게 보일 말이다.
    **둘 다 적재된 데이터에서 나온다.** 사용자가 고를 수 있는 값이 데이터에 있는 것뿐이
    되므로, 되묻기를 거쳐도 없는 값이 계획에 들어갈 수 없다.
    """

    filter_name: str
    value: Any
    label: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "filter": self.filter_name,
            "value": self.value,
            "label": self.label,
            "detail": self.detail,
        }


def _response_digest(state: tuple[Any, ...]) -> str:
    return hmac.new(
        _RESPONSE_KEY, repr(state).encode("utf-8"), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class ChatResponse:
    """Read-only wire DTO issued only by its state factories."""

    request_id: str
    status: ChatStatus
    answer_text: str
    citations: tuple[Citation, ...] = ()
    used_fact_ids: tuple[str, ...] = ()
    used_evidence_ids: tuple[str, ...] = ()
    clarification: str | None = None
    # 무엇이 부족한지에 대한 통제 코드와, 사용자가 고를 수 있는 데이터 유래 선택지.
    # 되묻기에서만 채워지며 답변 응답에는 들어가지 않는다.
    missing: tuple[str, ...] = ()
    options: tuple[ClarificationOption, ...] = ()
    error_code: ChatErrorCode | None = None
    # Claims remain an internal audit contract.  The stable UI JSON contract does not
    # expose them; it exposes their IDs and server-assembled citations instead.
    grounded_claims: tuple[GroundedClaim, ...] = ()
    _approval: str = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        *,
        _state: tuple[Any, ...] | None = None,
        _approval: str = "",
        _seal: object | None = None,
    ) -> None:
        if (
            _state is None
            or _seal is not _RESPONSE_SEAL
            or not hmac.compare_digest(_approval, _response_digest(_state))
        ):
            raise TypeError("ChatResponse can only be issued by its state factories")
        (
            request_id,
            status,
            answer_text,
            citations,
            used_fact_ids,
            used_evidence_ids,
            clarification,
            missing,
            options,
            error_code,
            grounded_claims,
        ) = _state
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "answer_text", answer_text)
        object.__setattr__(self, "citations", citations)
        object.__setattr__(self, "used_fact_ids", used_fact_ids)
        object.__setattr__(self, "used_evidence_ids", used_evidence_ids)
        object.__setattr__(self, "clarification", clarification)
        object.__setattr__(self, "missing", missing)
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "error_code", error_code)
        object.__setattr__(self, "grounded_claims", grounded_claims)
        object.__setattr__(self, "_approval", _approval)
        object.__setattr__(self, "_seal", _seal)
        self._validate_state()

    @classmethod
    def _issue(
        cls,
        *,
        request_id: str,
        status: ChatStatus,
        answer_text: str,
        citations: tuple[Citation, ...] = (),
        used_fact_ids: tuple[str, ...] = (),
        used_evidence_ids: tuple[str, ...] = (),
        clarification: str | None = None,
        missing: tuple[str, ...] = (),
        options: tuple[ClarificationOption, ...] = (),
        error_code: ChatErrorCode | None = None,
        grounded_claims: tuple[GroundedClaim, ...] = (),
    ) -> "ChatResponse":
        state = (
            request_id,
            status,
            answer_text,
            citations,
            used_fact_ids,
            used_evidence_ids,
            clarification,
            missing,
            options,
            error_code,
            grounded_claims,
        )
        return cls(
            _state=state,
            _approval=_response_digest(state),
            _seal=_RESPONSE_SEAL,
        )

    def _is_approved(self) -> bool:
        state = (
            self.request_id,
            self.status,
            self.answer_text,
            self.citations,
            self.used_fact_ids,
            self.used_evidence_ids,
            self.clarification,
            self.missing,
            self.options,
            self.error_code,
            self.grounded_claims,
        )
        return (
            self._seal is _RESPONSE_SEAL
            and hmac.compare_digest(self._approval, _response_digest(state))
        )

    def _validate_state(self) -> None:
        if not self.request_id or not self.answer_text.strip():
            raise ValueError("chat responses require request_id and safe answer_text")
        if self.status is ChatStatus.ANSWERABLE:
            if not (
                self.citations
                and self.used_fact_ids
                and self.used_evidence_ids
                and self.grounded_claims
            ):
                raise ValueError(
                    "ANSWERABLE requires claims, citations, facts, and Evidence"
                )
            if self.clarification is not None or self.error_code is not None:
                raise ValueError("ANSWERABLE cannot contain clarification or error_code")
            if self.missing or self.options:
                raise ValueError("ANSWERABLE cannot contain clarification options")
            claim_facts = {
                item for claim in self.grounded_claims for item in claim.fact_ids
            }
            claim_evidence = {
                item for claim in self.grounded_claims for item in claim.evidence_ids
            }
            if set(self.used_fact_ids) != claim_facts or set(self.used_evidence_ids) != claim_evidence:
                raise ValueError("response IDs must equal grounded Claim provenance")
            return
        if self.citations or self.used_fact_ids or self.used_evidence_ids or self.grounded_claims:
            raise ValueError("non-ANSWERABLE responses cannot contain grounded data")
        if self.status is not ChatStatus.CLARIFICATION_REQUIRED and (
            self.missing or self.options
        ):
            raise ValueError("missing and options are exclusive to CLARIFICATION_REQUIRED")
        if any(
            not isinstance(option, ClarificationOption) or not option.label.strip()
            for option in self.options
        ):
            raise ValueError("clarification options require a readable label")
        if self.status is ChatStatus.CLARIFICATION_REQUIRED:
            if not self.clarification or not self.clarification.strip():
                raise ValueError("CLARIFICATION_REQUIRED requires clarification")
            if self.error_code is not None:
                raise ValueError("CLARIFICATION_REQUIRED cannot contain error_code")
            if self.answer_text != NON_ANSWERABLE_MESSAGES[self.status]:
                raise ValueError("CLARIFICATION_REQUIRED requires its fixed safe message")
        elif self.clarification is not None:
            raise ValueError("clarification is exclusive to CLARIFICATION_REQUIRED")
        if self.status is ChatStatus.SAFE_FAILURE:
            if not isinstance(self.error_code, ChatErrorCode):
                raise ValueError("SAFE_FAILURE requires a normalized safe error_code")
            if self.answer_text != safe_failure_message(self.error_code):
                raise ValueError("SAFE_FAILURE requires the centrally managed safe message")
        elif self.error_code is not None:
            raise ValueError("error_code is exclusive to SAFE_FAILURE")
        elif self.status is not ChatStatus.CLARIFICATION_REQUIRED and (
            self.answer_text != NON_ANSWERABLE_MESSAGES[self.status]
        ):
            raise ValueError("non-ANSWERABLE responses require fixed safe messages")

    @classmethod
    def from_approved_answer(
        cls, request_id: str, approved_payload: object
    ) -> "ChatResponse":
        # Local import avoids a contracts/renderer import cycle.  The payload type and
        # issuer remain internal; public callers can only read the resulting DTO.
        from .renderer import _ApprovedAnswerPayload

        if not isinstance(approved_payload, _ApprovedAnswerPayload) or not (
            approved_payload._is_approved()
        ):
            raise TypeError("ANSWERABLE requires a CitationRenderer-approved payload")
        answer = approved_payload.answer
        return cls._issue(
            request_id=request_id,
            status=ChatStatus.ANSWERABLE,
            answer_text=answer.answer_text,
            citations=approved_payload.citations,
            used_fact_ids=answer.used_fact_ids,
            used_evidence_ids=answer.used_evidence_ids,
            grounded_claims=answer.claims,
        )

    @classmethod
    def clarification_required(
        cls,
        request_id: str,
        clarification: str,
        missing: Sequence[str] = (),
        options: Sequence[ClarificationOption] = (),
    ) -> "ChatResponse":
        if not isinstance(clarification, str) or not clarification.strip():
            raise ValueError("clarification must be a non-empty string")
        return cls._issue(
            request_id=request_id,
            status=ChatStatus.CLARIFICATION_REQUIRED,
            answer_text=NON_ANSWERABLE_MESSAGES[ChatStatus.CLARIFICATION_REQUIRED],
            clarification=clarification.strip(),
            missing=tuple(str(code) for code in missing),
            options=tuple(options),
        )

    @classmethod
    def out_of_scope(cls, request_id: str) -> "ChatResponse":
        return cls._non_answerable(request_id, ChatStatus.OUT_OF_SCOPE)

    @classmethod
    def unsupported(cls, request_id: str) -> "ChatResponse":
        return cls._non_answerable(request_id, ChatStatus.UNSUPPORTED)

    @classmethod
    def unresolved(cls, request_id: str) -> "ChatResponse":
        return cls._non_answerable(request_id, ChatStatus.UNRESOLVED)

    @classmethod
    def not_found(cls, request_id: str) -> "ChatResponse":
        return cls._non_answerable(request_id, ChatStatus.NOT_FOUND)

    @classmethod
    def _non_answerable(
        cls, request_id: str, status: ChatStatus
    ) -> "ChatResponse":
        if status not in NON_ANSWERABLE_MESSAGES or (
            status is ChatStatus.CLARIFICATION_REQUIRED
        ):
            raise ValueError("unsupported non-answerable status factory")
        return cls._issue(
            request_id=request_id,
            status=status,
            answer_text=NON_ANSWERABLE_MESSAGES[status],
        )

    @classmethod
    def safe_failure(
        cls, request_id: str, error_code: ChatErrorCode | str | None
    ) -> "ChatResponse":
        normalized = normalize_error_code(error_code)
        return cls._issue(
            request_id=request_id,
            status=ChatStatus.SAFE_FAILURE,
            answer_text=safe_failure_message(normalized),
            error_code=normalized,
        )

    def to_dict(self) -> dict[str, Any]:
        if not self._is_approved():
            raise TypeError("ChatResponse approval is invalid")
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "answer_text": self.answer_text,
            "citations": [citation.to_dict() for citation in self.citations],
            "used_fact_ids": list(self.used_fact_ids),
            "used_evidence_ids": list(self.used_evidence_ids),
            "clarification": self.clarification,
            "missing": list(self.missing),
            "options": [option.to_dict() for option in self.options],
            "error_code": self.error_code.value if self.error_code else None,
        }
