"""Provider-independent contracts for grounded Korean curriculum answers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


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
    ANSWER_CLAIM_VALIDATION_FAILED = "ANSWER_CLAIM_VALIDATION_FAILED"
    ANSWER_RENDERING_UNSUPPORTED = "ANSWER_RENDERING_UNSUPPORTED"
    UNKNOWN_SAFE_FAILURE = "UNKNOWN_SAFE_FAILURE"


class ClaimType(StrEnum):
    FIELD_VALUE = "FIELD_VALUE"
    NUMERIC_REQUIREMENT = "NUMERIC_REQUIREMENT"
    BOOLEAN_POLICY = "BOOLEAN_POLICY"
    VERIFIED_RULE_TEXT = "VERIFIED_RULE_TEXT"
    COURSE_LIST = "COURSE_LIST"
    AGGREGATE = "AGGREGATE"


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
    ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED: "답변의 근거를 검증하지 못했습니다.",
    ChatErrorCode.ANSWER_RENDERING_UNSUPPORTED: "현재 조회 결과는 안전한 답변 형식으로 제공할 수 없습니다.",
    ChatErrorCode.UNKNOWN_SAFE_FAILURE: "요청을 안전하게 처리하지 못했습니다.",
}
NON_ANSWERABLE_MESSAGES: dict[ChatStatus, str] = {
    ChatStatus.CLARIFICATION_REQUIRED: "질문을 정확히 확인하려면 추가 정보가 필요합니다.",
    ChatStatus.OUT_OF_SCOPE: "현재 데이터 범위에서는 답변할 수 없습니다.",
    ChatStatus.UNSUPPORTED: (
        "현재는 개인 수강 이력을 이용한 졸업판정을 지원하지 않습니다. "
        "2026학년도 컴퓨터공학과의 전공필수 과목과 이수학점 기준은 안내할 수 있습니다."
    ),
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
        cls, request_id: str, clarification: str
    ) -> "ChatResponse":
        if not isinstance(clarification, str) or not clarification.strip():
            raise ValueError("clarification must be a non-empty string")
        return cls._issue(
            request_id=request_id,
            status=ChatStatus.CLARIFICATION_REQUIRED,
            answer_text=NON_ANSWERABLE_MESSAGES[ChatStatus.CLARIFICATION_REQUIRED],
            clarification=clarification.strip(),
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
            "error_code": self.error_code.value if self.error_code else None,
        }
