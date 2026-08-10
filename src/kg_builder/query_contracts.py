"""Typed request and response contracts for the verified KG query layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class QueryValidationError(ValueError):
    """Raised when a structured query request violates its intent contract."""


class Intent(StrEnum):
    GET_GENERAL_EDUCATION_MIN_CREDITS = "GET_GENERAL_EDUCATION_MIN_CREDITS"
    GET_BALANCED_GENERAL_REQUIREMENT = "GET_BALANCED_GENERAL_REQUIREMENT"
    GET_TRANSFER_GENERAL_EXEMPTION = "GET_TRANSFER_GENERAL_EXEMPTION"
    GET_COURSE_OFFERING = "GET_COURSE_OFFERING"
    GET_MAJOR_REQUIRED_COURSES = "GET_MAJOR_REQUIRED_COURSES"
    GET_COURSE_COMPLETION_TYPE = "GET_COURSE_COMPLETION_TYPE"


class Answerability(StrEnum):
    ANSWERABLE = "ANSWERABLE"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    NOT_FOUND = "NOT_FOUND"


MAJOR_TYPES = frozenset(
    {"SINGLE_MAJOR", "MINOR", "DOUBLE_MAJOR", "LINKED_MAJOR", "CONVERGENCE_MAJOR"}
)
ADMISSION_TYPES = frozenset({"TRANSFER", "REGULAR", "READMISSION"})


@dataclass(frozen=True, slots=True)
class IntentContract:
    required: frozenset[str]
    optional: frozenset[str] = frozenset()
    exactly_one: tuple[str, ...] = ()

    @property
    def allowed(self) -> frozenset[str]:
        return self.required | self.optional | frozenset(self.exactly_one)


INTENT_CONTRACTS: dict[Intent, IntentContract] = {
    Intent.GET_GENERAL_EDUCATION_MIN_CREDITS: IntentContract(
        frozenset({"academic_year"}),
        frozenset({"department", "major_type", "admission_type"}),
    ),
    Intent.GET_BALANCED_GENERAL_REQUIREMENT: IntentContract(
        frozenset({"academic_year"})
    ),
    Intent.GET_TRANSFER_GENERAL_EXEMPTION: IntentContract(
        frozenset({"academic_year", "admission_type"})
    ),
    Intent.GET_COURSE_OFFERING: IntentContract(
        frozenset({"academic_year", "department"}),
        exactly_one=("course_code", "course_name"),
    ),
    Intent.GET_MAJOR_REQUIRED_COURSES: IntentContract(
        frozenset({"academic_year", "department"}),
        frozenset({"major_type"}),
    ),
    Intent.GET_COURSE_COMPLETION_TYPE: IntentContract(
        frozenset({"academic_year", "department"}),
        exactly_one=("course_code", "course_name"),
    ),
}


@dataclass(frozen=True, slots=True)
class QueryRequest:
    intent: Intent
    parameters: dict[str, Any]

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "QueryRequest":
        if not isinstance(payload, Mapping):
            raise QueryValidationError("request must be a JSON object")
        unknown_root = set(payload) - {"intent", "parameters"}
        if unknown_root:
            raise QueryValidationError(f"unknown request fields: {sorted(unknown_root)}")
        try:
            intent = Intent(payload.get("intent"))
        except (TypeError, ValueError) as exc:
            raise QueryValidationError("unsupported intent") from exc
        raw_parameters = payload.get("parameters")
        if not isinstance(raw_parameters, Mapping):
            raise QueryValidationError("parameters must be a JSON object")

        contract = INTENT_CONTRACTS[intent]
        parameters = dict(raw_parameters)
        unknown = set(parameters) - contract.allowed
        if unknown:
            raise QueryValidationError(f"unknown parameters for {intent}: {sorted(unknown)}")
        missing = contract.required - set(parameters)
        if missing:
            raise QueryValidationError(f"missing required parameters: {sorted(missing)}")
        if contract.exactly_one:
            supplied = [name for name in contract.exactly_one if name in parameters]
            if len(supplied) != 1:
                raise QueryValidationError(
                    "exactly one course selector is required: "
                    + ", ".join(contract.exactly_one)
                )

        year = parameters.get("academic_year")
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
            raise QueryValidationError("academic_year must be a four-digit integer")
        for key in ("department", "course_code", "course_name"):
            if key in parameters:
                value = parameters[key]
                if not isinstance(value, str) or not value.strip():
                    raise QueryValidationError(f"{key} must be a non-empty string")
                parameters[key] = value.strip()
        if "course_code" in parameters:
            parameters["course_code"] = parameters["course_code"].upper()
        if "major_type" in parameters and parameters["major_type"] not in MAJOR_TYPES:
            raise QueryValidationError("major_type is not in the ontology controlled vocabulary")
        if "admission_type" in parameters and parameters["admission_type"] not in ADMISSION_TYPES:
            raise QueryValidationError(
                "admission_type is not in the ontology controlled vocabulary"
            )
        if (
            intent is Intent.GET_TRANSFER_GENERAL_EXEMPTION
            and parameters["admission_type"] != "TRANSFER"
        ):
            raise QueryValidationError(
                "GET_TRANSFER_GENERAL_EXEMPTION requires admission_type=TRANSFER"
            )
        return cls(intent=intent, parameters=parameters)


@dataclass(frozen=True, slots=True)
class EvidenceDTO:
    evidence_id: str
    excerpt_page: int
    source_pdf_page: int
    printed_page: int
    source_text: str

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "EvidenceDTO | None":
        values = {
            "evidence_id": record.get("evidence_id"),
            "excerpt_page": record.get("excerpt_page"),
            "source_pdf_page": record.get("source_pdf_page"),
            "printed_page": record.get("printed_page"),
            "source_text": record.get("source_text"),
        }
        if any(value is None for value in values.values()):
            return None
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "excerpt_page": self.excerpt_page,
            "source_pdf_page": self.source_pdf_page,
            "printed_page": self.printed_page,
            "source_text": self.source_text,
        }


@dataclass(frozen=True, slots=True)
class QueryResponse:
    intent: Intent
    answerability: Answerability
    answer: dict[str, Any]
    scope: dict[str, Any]
    evidence: tuple[EvidenceDTO, ...] = ()
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.answerability is Answerability.ANSWERABLE and not self.evidence:
            raise ValueError("ANSWERABLE responses require at least one VERIFIED Evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "answerability": self.answerability.value,
            "answer": self.answer,
            "scope": self.scope,
            "evidence": [item.to_dict() for item in self.evidence],
            "warnings": list(self.warnings),
        }
