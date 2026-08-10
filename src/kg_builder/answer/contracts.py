"""Provider-independent contracts for grounded Korean curriculum answers."""

from __future__ import annotations

from dataclasses import dataclass
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


SAFE_FAILURE_MESSAGES = {
    "QUERY_SAFE_FAILURE": "요청을 안전하게 처리하지 못했습니다.",
    "ANSWER_CLAIM_VALIDATION_FAILED": "답변의 근거를 검증하지 못했습니다.",
    "ANSWER_RENDERING_UNSUPPORTED": "현재 조회 결과는 안전한 답변 형식으로 제공할 수 없습니다.",
}
DEFAULT_SAFE_FAILURE_MESSAGE = "안전한 답변을 생성하지 못했습니다."


def safe_failure_message(error_code: str) -> str:
    return SAFE_FAILURE_MESSAGES.get(error_code, DEFAULT_SAFE_FAILURE_MESSAGE)


@dataclass(frozen=True, slots=True)
class ChatResponse:
    request_id: str
    status: ChatStatus
    answer_text: str
    citations: tuple[Citation, ...] = ()
    used_fact_ids: tuple[str, ...] = ()
    used_evidence_ids: tuple[str, ...] = ()
    clarification: str | None = None
    error_code: str | None = None
    # Claims remain an internal audit contract.  The stable UI JSON contract does not
    # expose them; it exposes their IDs and server-assembled citations instead.
    grounded_claims: tuple[GroundedClaim, ...] = ()

    def __post_init__(self) -> None:
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
        elif self.clarification is not None:
            raise ValueError("clarification is exclusive to CLARIFICATION_REQUIRED")
        if self.status is ChatStatus.SAFE_FAILURE:
            if not isinstance(self.error_code, str) or not self.error_code.strip():
                raise ValueError("SAFE_FAILURE requires an internal error_code")
            if self.answer_text != safe_failure_message(self.error_code):
                raise ValueError("SAFE_FAILURE requires the centrally managed safe message")
        elif self.error_code is not None:
            raise ValueError("error_code is exclusive to SAFE_FAILURE")

    @classmethod
    def safe_failure(cls, request_id: str, error_code: str) -> "ChatResponse":
        return cls(
            request_id=request_id,
            status=ChatStatus.SAFE_FAILURE,
            answer_text=safe_failure_message(error_code),
            error_code=error_code,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "answer_text": self.answer_text,
            "citations": [citation.to_dict() for citation in self.citations],
            "used_fact_ids": list(self.used_fact_ids),
            "used_evidence_ids": list(self.used_evidence_ids),
            "clarification": self.clarification,
            "error_code": self.error_code,
        }
