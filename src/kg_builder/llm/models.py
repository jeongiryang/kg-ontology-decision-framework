"""Contracts shared by the local planner and Cypher generator."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from kg_builder.query.query_plan import QueryPlan


class PlanningStatus(StrEnum):
    READY = "READY"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNSUPPORTED = "UNSUPPORTED"
    UNRESOLVED = "UNRESOLVED"


class GraduationQuestionClass(StrEnum):
    """Scope classification only; it never supplies an academic answer value."""

    GENERAL_RULE = "GENERAL_RULE"
    SINGLE_CONDITION_COMPARISON = "SINGLE_CONDITION_COMPARISON"
    FULL_PERSONAL_HISTORY = "FULL_PERSONAL_HISTORY"
    OTHER = "OTHER"


class UnsupportedReason(StrEnum):
    PERSONAL_HISTORY = "PERSONAL_HISTORY"
    SINGLE_CONDITION_COMPARISON = "SINGLE_CONDITION_COMPARISON"
    GENERAL_FEATURE = "GENERAL_FEATURE"


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    status: PlanningStatus
    plan: QueryPlan | None = None
    message: str | None = None
    unsupported_reason: UnsupportedReason | None = None


@dataclass(frozen=True, slots=True)
class LLMGeneration:
    payload: dict[str, Any]
    elapsed_seconds: float
    model: str
