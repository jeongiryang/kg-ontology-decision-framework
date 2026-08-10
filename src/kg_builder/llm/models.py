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


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    status: PlanningStatus
    plan: QueryPlan | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class LLMGeneration:
    payload: dict[str, Any]
    elapsed_seconds: float
    model: str
