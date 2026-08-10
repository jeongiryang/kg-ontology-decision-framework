"""Provider-independent contracts for grounded Korean curriculum answers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class ChatStatus(StrEnum):
    ANSWERABLE = "ANSWERABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNSUPPORTED = "UNSUPPORTED"
    UNRESOLVED = "UNRESOLVED"
    NOT_FOUND = "NOT_FOUND"
    SAFE_FAILURE = "SAFE_FAILURE"


class AnswerContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    """The complete output surface granted to the answer-writing model."""

    answer_text: str
    used_fact_ids: tuple[str, ...]
    used_evidence_ids: tuple[str, ...]

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "AnswerDraft":
        if not isinstance(payload, Mapping):
            raise AnswerContractError("ANSWER_DRAFT_INVALID", "answer draft must be an object")
        expected = {"answer_text", "used_fact_ids", "used_evidence_ids"}
        unknown = set(payload) - expected
        missing = expected - set(payload)
        if unknown or missing:
            raise AnswerContractError(
                "ANSWER_DRAFT_INVALID",
                "answer draft fields do not match the restricted contract",
            )
        answer_text = payload["answer_text"]
        if not isinstance(answer_text, str):
            raise AnswerContractError("ANSWER_DRAFT_INVALID", "answer_text must be text")
        fact_ids = cls._id_array(payload["used_fact_ids"], "used_fact_ids")
        evidence_ids = cls._id_array(payload["used_evidence_ids"], "used_evidence_ids")
        return cls(answer_text.strip(), fact_ids, evidence_ids)

    @staticmethod
    def _id_array(value: Any, field_name: str) -> tuple[str, ...]:
        if (
            not isinstance(value, list)
            or not value
            or any(not isinstance(item, str) or not item.strip() for item in value)
        ):
            raise AnswerContractError(
                "ANSWER_DRAFT_INVALID", f"{field_name} must be a non-empty string array"
            )
        normalized = tuple(item.strip() for item in value)
        if len(set(normalized)) != len(normalized):
            raise AnswerContractError(
                "ANSWER_DRAFT_INVALID", f"{field_name} must not contain duplicates"
            )
        return normalized


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

    def __post_init__(self) -> None:
        if self.status is ChatStatus.ANSWERABLE and (
            not self.answer_text.strip() or not self.citations
        ):
            raise ValueError("ANSWERABLE chat responses require text and citations")
        if self.status is not ChatStatus.ANSWERABLE and self.citations:
            raise ValueError("non-ANSWERABLE chat responses cannot contain citations")

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
