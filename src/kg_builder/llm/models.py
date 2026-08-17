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


class MissingScope(StrEnum):
    """무엇이 부족해 계획을 세우지 못했는지에 대한 통제된 코드.

    사용자에게 보여줄 문장은 이 코드로부터 서비스가 만든다. 계획 모델이 쓴 자연어를
    그대로 화면에 내보내지 않기 위한 계약이다.
    """

    ACADEMIC_YEAR = "ACADEMIC_YEAR"
    DEPARTMENT = "DEPARTMENT"
    COURSE_IDENTITY = "COURSE_IDENTITY"
    # 어느 과목인지는 정해졌으나 그 과목의 **무엇을** 묻는지가 비어 있는 경우.
    COURSE_ASPECT = "COURSE_ASPECT"
    RULE_TOPIC = "RULE_TOPIC"
    QUESTION_INTENT = "QUESTION_INTENT"


class AttemptOutcome(StrEnum):
    """계획 시도 하나가 어떻게 끝났는지."""

    ACCEPTED = "ACCEPTED"
    CONTRACT_REJECTED = "CONTRACT_REJECTED"
    CLARIFICATION = "CLARIFICATION"
    NOT_ANSWERABLE = "NOT_ANSWERABLE"
    BROADENED = "BROADENED"


@dataclass(frozen=True, slots=True)
class PlanningAttempt:
    """계획 시도 하나의 골격. 질문에서 온 값은 담지 않는다.

    커버리지가 왜 낮은지 사후에 좁히려면 모델이 어떤 모드를 골랐고 어떤 필터 이름을
    비웠는지를 알아야 한다. 값은 원문 질문과 같은 성격이라 여기 남기지 않고, 이름과
    통제 코드만 남긴다.
    """

    attempt: int
    outcome: AttemptOutcome
    status: str | None = None
    selection_mode: str | None = None
    filter_names: tuple[str, ...] = ()
    requested_fields: tuple[str, ...] = ()
    missing_scope: tuple[str, ...] = ()
    contract_error: str | None = None


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    status: PlanningStatus
    plan: QueryPlan | None = None
    # 계획 모델의 원문. 진단용으로만 보관하며 사용자 화면에는 쓰지 않는다.
    message: str | None = None
    missing: tuple[MissingScope, ...] = ()
    # 계획 단계 진단 기록. 사용자 화면에는 쓰지 않는다.
    attempts: tuple[PlanningAttempt, ...] = ()
    # 되묻기에서 사용자가 고를 수 있는 선택지. 값·표기 모두 적재 데이터에서 나온다.
    options: tuple[Any, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMGeneration:
    payload: dict[str, Any]
    elapsed_seconds: float
    model: str
