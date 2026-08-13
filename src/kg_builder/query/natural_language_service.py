"""Official natural-language entry point over the Text-to-Cypher safety pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from kg_builder.llm.client import LLMResponseError
from kg_builder.llm.cypher_generator import public_plan_payload
from kg_builder.llm.models import PlanningStatus
from kg_builder.llm.planner import LocalQueryPlanner

from .query_trace import TraceStage, TraceStatus
from .safety_pipeline import PipelineOutcome, SafetyPipeline, SafetyPipelineError
from .schema_selector import QuerySchemaSelector


class Planner(Protocol):
    def plan(self, question: str): ...


class Generator(Protocol):
    def generate(self, plan, schema_subset, *, previous_error_code=None) -> str: ...


@dataclass(frozen=True, slots=True)
class NaturalLanguageResult:
    request_id: str
    status: str
    model: str
    elapsed_seconds: float
    query_plan: dict[str, Any] | None = None
    cypher: str | None = None
    rows: tuple[dict[str, Any], ...] = ()
    evidence_count: int = 0
    error_stage: str | None = None
    error_code: str | None = None
    # 계획 모델 원문. 진단용이며 사용자 화면 문구로 쓰지 않는다.
    message: str | None = None
    # 무엇이 부족한지에 대한 통제 코드. 화면 문구는 이 값으로 서비스가 만든다.
    missing: tuple[str, ...] = ()
    # 조회 범위를 좁히지 못해 넓게 답한 경우의 사유 코드. 화면에 밝힌다.
    broadened: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "status": self.status,
            "model": self.model,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "query_plan": self.query_plan,
            "validated_cypher": self.cypher,
            "result_rows": list(self.rows),
            "evidence_count": self.evidence_count,
            "error": (
                {"stage": self.error_stage, "code": self.error_code}
                if self.error_code
                else None
            ),
            "message": self.message,
        }
        return payload


class NaturalLanguageQueryService:
    """Plan and generate locally, then delegate all DB access to SafetyPipeline."""

    def __init__(
        self,
        planner: Planner,
        generator: Generator,
        pipeline: SafetyPipeline,
        selector: QuerySchemaSelector,
        *,
        model: str,
        generator_retries: int = 1,
    ):
        if generator_retries not in {0, 1}:
            raise ValueError("generator_retries must be 0 or 1")
        self.planner = planner
        self.generator = generator
        self.pipeline = pipeline
        self.selector = selector
        self.model = model
        self.generator_retries = generator_retries

    def ask(self, question: str) -> NaturalLanguageResult:
        started = perf_counter()
        provisional_id = str(uuid.uuid4())
        try:
            planning = self.planner.plan(question)
        except Exception as exc:
            code = getattr(exc, "code", exc.__class__.__name__)
            request_id = self._trace_planning(
                question, getattr(exc, "attempts", ()), code
            )
            return self._failure(
                request_id or provisional_id, started, "PLANNING", code
            )
        if planning.status is not PlanningStatus.READY or planning.plan is None:
            request_id = self._trace_planning(
                question, planning.attempts, planning.status.value
            )
            return NaturalLanguageResult(
                request_id=request_id or provisional_id,
                status=planning.status.value,
                model=self.model,
                elapsed_seconds=perf_counter() - started,
                message=planning.message,
                missing=tuple(str(item) for item in planning.missing),
            )

        plan = planning.plan
        public_plan = public_plan_payload(plan)
        try:
            schema_subset = self.selector.select(plan)
        except Exception as exc:
            return self._failure(
                provisional_id,
                started,
                "SCHEMA_SELECTION",
                getattr(exc, "code", exc.__class__.__name__),
                query_plan=public_plan,
            )

        previous_error: str | None = None
        for attempt in range(self.generator_retries + 1):
            try:
                cypher = self.generator.generate(
                    plan,
                    schema_subset,
                    previous_error_code=previous_error,
                )
            except LLMResponseError as exc:
                return self._failure(
                    provisional_id,
                    started,
                    "CYPHER_GENERATION",
                    exc.code,
                    query_plan=public_plan,
                )
            except Exception as exc:
                return self._failure(
                    provisional_id,
                    started,
                    "CYPHER_GENERATION",
                    getattr(exc, "code", exc.__class__.__name__),
                    query_plan=public_plan,
                )
            try:
                outcome: PipelineOutcome = self.pipeline.run(
                    {
                        "question": plan.question,
                        **public_plan,
                    },
                    cypher,
                )
                return NaturalLanguageResult(
                    request_id=outcome.request_id,
                    status="ANSWERABLE" if outcome.result.row_count else "NOT_FOUND",
                    model=self.model,
                    elapsed_seconds=perf_counter() - started,
                    query_plan=public_plan,
                    cypher=cypher,
                    rows=outcome.result.rows,
                    evidence_count=outcome.result.evidence_count,
                    broadened=planning.broadened,
                )
            except SafetyPipelineError as exc:
                if exc.code == "RESULT_COURSE_AMBIGUOUS":
                    return NaturalLanguageResult(
                        request_id=provisional_id,
                        status=PlanningStatus.CLARIFICATION_REQUIRED.value,
                        model=self.model,
                        elapsed_seconds=perf_counter() - started,
                        query_plan=public_plan,
                        error_stage=exc.stage.value,
                        error_code=exc.code,
                        message=(
                            "동일한 과목명에 서로 다른 학수번호가 있습니다. "
                            "학수번호를 지정해 주세요."
                        ),
                        missing=("COURSE_IDENTITY",),
                    )
                previous_error = exc.code
                if attempt >= self.generator_retries:
                    return self._failure(
                        provisional_id,
                        started,
                        exc.stage.value,
                        exc.code,
                        query_plan=public_plan,
                    )
        raise AssertionError("unreachable retry state")

    def _trace_planning(
        self, question: str, attempts: Any, error_code: str
    ) -> str | None:
        """Write what the planner produced before it stopped.

        계획 단계에서 끝난 요청은 종전까지 아무 기록도 남기지 않았다. 커버리지가 왜
        낮은지 사후에 좁힐 수 없으므로 여기서 시도 골격을 남긴다. 기록 실패가 응답을
        막지는 않는다.
        """

        try:
            trace = self.pipeline.new_trace(question)
            trace.record_planning(attempts)
            trace.record(
                TraceStage.PLANNING, TraceStatus.FAIL, 0.0, error_code=error_code
            )
            trace.skip_after(TraceStage.PLANNING)
            trace.write()
            return trace.request_id
        except Exception:
            return None

    def _failure(
        self,
        request_id: str,
        started: float,
        stage: str,
        code: str,
        *,
        query_plan: dict[str, Any] | None = None,
    ) -> NaturalLanguageResult:
        return NaturalLanguageResult(
            request_id=request_id,
            status="SAFE_FAILURE",
            model=self.model,
            elapsed_seconds=perf_counter() - started,
            query_plan=query_plan,
            error_stage=stage,
            error_code=code,
        )
