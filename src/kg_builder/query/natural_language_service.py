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

from .safety_pipeline import PipelineOutcome, SafetyPipeline, SafetyPipelineError
from .schema_selector import QuerySchemaSelector
from .progress import (
    ProgressCallback,
    ProgressPhase,
    ProgressState,
    emit_progress,
)


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
    message: str | None = None

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

    def ask(
        self, question: str, progress_callback: ProgressCallback | None = None
    ) -> NaturalLanguageResult:
        started = perf_counter()
        provisional_id = str(uuid.uuid4())
        phase_started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.QUESTION_ANALYSIS,
            ProgressState.STARTED,
            0,
        )
        try:
            planning = self.planner.plan(question)
        except Exception as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.QUESTION_ANALYSIS,
                ProgressState.FAILED,
                (perf_counter() - phase_started) * 1000,
                error_code=getattr(exc, "code", exc.__class__.__name__),
            )
            return self._failure(
                provisional_id,
                started,
                "PLANNING",
                getattr(exc, "code", exc.__class__.__name__),
            )
        analysis_details: dict[str, Any] = {"planning_status": planning.status.value}
        if planning.plan is not None:
            analysis_details["query_plan"] = public_plan_payload(planning.plan)
        emit_progress(
            progress_callback,
            ProgressPhase.QUESTION_ANALYSIS,
            ProgressState.COMPLETED,
            (perf_counter() - phase_started) * 1000,
            **analysis_details,
        )
        if planning.status is not PlanningStatus.READY or planning.plan is None:
            return NaturalLanguageResult(
                request_id=provisional_id,
                status=planning.status.value,
                model=self.model,
                elapsed_seconds=perf_counter() - started,
                message=planning.message,
            )

        plan = planning.plan
        public_plan = public_plan_payload(plan)
        phase_started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.SCHEMA_SELECTION,
            ProgressState.STARTED,
            0,
        )
        try:
            schema_subset = self.selector.select(plan)
        except Exception as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.SCHEMA_SELECTION,
                ProgressState.FAILED,
                (perf_counter() - phase_started) * 1000,
                error_code=getattr(exc, "code", exc.__class__.__name__),
            )
            return self._failure(
                provisional_id,
                started,
                "SCHEMA_SELECTION",
                getattr(exc, "code", exc.__class__.__name__),
                query_plan=public_plan,
            )
        emit_progress(
            progress_callback,
            ProgressPhase.SCHEMA_SELECTION,
            ProgressState.COMPLETED,
            (perf_counter() - phase_started) * 1000,
            labels=sorted(
                item.get("label")
                for item in schema_subset.get("nodes", ())
                if isinstance(item, dict) and isinstance(item.get("label"), str)
            ),
            relationship_types=sorted(
                item.get("type")
                for item in schema_subset.get("relationships", ())
                if isinstance(item, dict) and isinstance(item.get("type"), str)
            ),
        )

        previous_error: str | None = None
        for attempt in range(self.generator_retries + 1):
            phase_started = perf_counter()
            emit_progress(
                progress_callback,
                ProgressPhase.CYPHER_GENERATION,
                ProgressState.STARTED,
                0,
            )
            try:
                cypher = self.generator.generate(
                    plan,
                    schema_subset,
                    previous_error_code=previous_error,
                )
            except LLMResponseError as exc:
                emit_progress(
                    progress_callback,
                    ProgressPhase.CYPHER_GENERATION,
                    ProgressState.FAILED,
                    (perf_counter() - phase_started) * 1000,
                    error_code=exc.code,
                )
                return self._failure(
                    provisional_id,
                    started,
                    "CYPHER_GENERATION",
                    exc.code,
                    query_plan=public_plan,
                )
            except Exception as exc:
                emit_progress(
                    progress_callback,
                    ProgressPhase.CYPHER_GENERATION,
                    ProgressState.FAILED,
                    (perf_counter() - phase_started) * 1000,
                    error_code=getattr(exc, "code", exc.__class__.__name__),
                )
                return self._failure(
                    provisional_id,
                    started,
                    "CYPHER_GENERATION",
                    getattr(exc, "code", exc.__class__.__name__),
                    query_plan=public_plan,
                )
            emit_progress(
                progress_callback,
                ProgressPhase.CYPHER_GENERATION,
                ProgressState.COMPLETED,
                (perf_counter() - phase_started) * 1000,
            )
            try:
                outcome: PipelineOutcome = self.pipeline.run(
                    {
                        "question": plan.question,
                        **public_plan,
                    },
                    cypher,
                    progress_callback=progress_callback,
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
