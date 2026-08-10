"""Safe, observable milestones for the natural-language query pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping


class ProgressPhase(StrEnum):
    QUESTION_ANALYSIS = "QUESTION_ANALYSIS"
    SCHEMA_SELECTION = "SCHEMA_SELECTION"
    CYPHER_GENERATION = "CYPHER_GENERATION"
    STATIC_VALIDATION = "STATIC_VALIDATION"
    NEO4J_EXPLAIN = "NEO4J_EXPLAIN"
    GRAPH_EXECUTION = "GRAPH_EXECUTION"
    RESULT_VALIDATION = "RESULT_VALIDATION"
    CLAIM_BUILDING = "CLAIM_BUILDING"
    ANSWER_RENDERING = "ANSWER_RENDERING"
    COMPLETED = "COMPLETED"


class ProgressState(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


PROGRESS_MESSAGES: Mapping[tuple[ProgressPhase, ProgressState], str] = {
    (ProgressPhase.QUESTION_ANALYSIS, ProgressState.STARTED): "질문 의도를 분석하는 중…",
    (ProgressPhase.QUESTION_ANALYSIS, ProgressState.COMPLETED): "질문 의도를 분석했습니다.",
    (ProgressPhase.SCHEMA_SELECTION, ProgressState.STARTED): "관련 온톨로지 구조를 찾는 중…",
    (ProgressPhase.SCHEMA_SELECTION, ProgressState.COMPLETED): "관련 온톨로지 구조를 선택했습니다.",
    (ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED): "지식그래프 질의를 구성하는 중…",
    (ProgressPhase.CYPHER_GENERATION, ProgressState.COMPLETED): "지식그래프 질의를 구성했습니다.",
    (ProgressPhase.STATIC_VALIDATION, ProgressState.STARTED): "읽기 전용 안전성을 검사하는 중…",
    (ProgressPhase.STATIC_VALIDATION, ProgressState.COMPLETED): "정적 안전성 검사를 통과했습니다.",
    (ProgressPhase.NEO4J_EXPLAIN, ProgressState.STARTED): "Neo4j 실행 계획을 확인하는 중…",
    (ProgressPhase.NEO4J_EXPLAIN, ProgressState.COMPLETED): "Neo4j 실행 계획을 확인했습니다.",
    (ProgressPhase.GRAPH_EXECUTION, ProgressState.STARTED): "지식그래프에서 관련 근거를 탐색하는 중…",
    (ProgressPhase.GRAPH_EXECUTION, ProgressState.COMPLETED): "지식그래프 조회를 마쳤습니다.",
    (ProgressPhase.RESULT_VALIDATION, ProgressState.STARTED): "조회 결과와 Evidence를 검증하는 중…",
    (ProgressPhase.RESULT_VALIDATION, ProgressState.COMPLETED): "조회 결과와 Evidence를 검증했습니다.",
    (ProgressPhase.CLAIM_BUILDING, ProgressState.STARTED): "검증된 사실을 구성하는 중…",
    (ProgressPhase.CLAIM_BUILDING, ProgressState.COMPLETED): "검증된 사실을 구성했습니다.",
    (ProgressPhase.ANSWER_RENDERING, ProgressState.STARTED): "근거 기반 답변을 구성하는 중…",
    (ProgressPhase.ANSWER_RENDERING, ProgressState.COMPLETED): "근거 기반 답변을 구성했습니다.",
    (ProgressPhase.COMPLETED, ProgressState.COMPLETED): "답변 준비를 완료했습니다.",
}


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    phase: ProgressPhase
    state: ProgressState
    elapsed_ms: int
    details: Mapping[str, Any] = field(default_factory=dict)

    def public_payload(self) -> dict[str, Any]:
        return {
            "type": "progress",
            "phase": self.phase.value,
            "state": self.state.value,
            "message": PROGRESS_MESSAGES.get(
                (self.phase, self.state),
                "처리 단계를 안전하게 종료했습니다."
                if self.state is ProgressState.FAILED
                else "처리 중입니다.",
            ),
            "elapsed_ms": self.elapsed_ms,
        }


ProgressCallback = Callable[[ProgressEvent], None]


def emit_progress(
    callback: ProgressCallback | None,
    phase: ProgressPhase,
    state: ProgressState,
    elapsed_ms: int,
    **details: Any,
) -> None:
    if callback is not None:
        callback(ProgressEvent(phase, state, max(0, int(elapsed_ms)), details))
