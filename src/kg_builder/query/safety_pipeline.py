"""Orchestrate plan validation, schema selection, EXPLAIN, execution, and results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Protocol

from .cypher_validator import CypherValidator, ValidatedCypher
from .query_explainer import ExplainedCypher
from .query_plan import QueryPlan
from .query_trace import DEFAULT_TRACE_DIR, QueryTrace, TracePolicy, TraceStage, TraceStatus
from .result_validator import ResultValidator, ValidatedResult
from .schema_catalog import (
    DEFAULT_QUERY_SCHEMA_PATH,
    DEFAULT_SPEC_PATH,
    SchemaCatalog,
)
from .progress import ProgressCallback, ProgressPhase, ProgressState, emit_progress


class Explainer(Protocol):
    def explain(self, validated: ValidatedCypher) -> ExplainedCypher: ...


class Executor(Protocol):
    def execute(self, explained: ExplainedCypher) -> list[dict[str, Any]]: ...


class SafetyPipelineError(RuntimeError):
    def __init__(self, stage: TraceStage, code: str, message: str, trace_path: Path):
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.trace_path = trace_path


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    request_id: str
    plan: QueryPlan
    result: ValidatedResult
    selected_labels: tuple[str, ...]
    explain_operators: tuple[str, ...]
    validated_cypher: str
    trace_path: Path


class SafetyPipeline:
    def __init__(
        self,
        explainer: Explainer,
        executor: Executor,
        *,
        spec_path: Path = DEFAULT_SPEC_PATH,
        generated_schema_path: Path = DEFAULT_QUERY_SCHEMA_PATH,
        trace_dir: Path = DEFAULT_TRACE_DIR,
        max_rows: int = 100,
        store_raw_question: bool | None = None,
        trace_retention_days: int | None = None,
    ):
        self.explainer = explainer
        self.executor = executor
        self.spec_path = spec_path
        self.generated_schema_path = generated_schema_path
        self.trace_dir = trace_dir
        self.max_rows = max_rows
        trace_policy = TracePolicy.from_env()
        self.store_raw_question = (
            trace_policy.store_raw_question
            if store_raw_question is None
            else store_raw_question
        )
        self.trace_fingerprint_hmac_key = trace_policy.fingerprint_hmac_key
        self.trace_retention_days = (
            trace_policy.retention_days
            if trace_retention_days is None
            else trace_retention_days
        )

    def new_trace(self, question: str, parameters: Mapping[str, Any] | None = None) -> QueryTrace:
        """Open a trace under this pipeline's policy without running a query.

        계획 단계에서 멈춘 요청도 같은 보존·마스킹 정책으로 기록을 남기기 위한 입구다.
        """

        return QueryTrace(
            question=question if isinstance(question, str) else "",
            parameters=parameters or {},
            trace_dir=self.trace_dir,
            store_raw_question=self.store_raw_question,
            fingerprint_hmac_key=self.trace_fingerprint_hmac_key,
            retention_days=self.trace_retention_days,
        )

    def run(
        self,
        payload: Mapping[str, Any],
        cypher: str,
        *,
        progress_callback: ProgressCallback | None = None,
        candidate_attempt: int = 1,
    ) -> PipelineOutcome:
        if candidate_attempt < 1:
            raise ValueError("candidate_attempt must be positive")
        raw_filters = payload.get("filters", {}) if isinstance(payload, Mapping) else {}
        raw_question = payload.get("question", "") if isinstance(payload, Mapping) else ""
        trace = QueryTrace(
            question=raw_question if isinstance(raw_question, str) else "",
            parameters=raw_filters if isinstance(raw_filters, Mapping) else {},
            trace_dir=self.trace_dir,
            store_raw_question=self.store_raw_question,
            fingerprint_hmac_key=self.trace_fingerprint_hmac_key,
            retention_days=self.trace_retention_days,
        )

        started = perf_counter()
        try:
            source_catalog = SchemaCatalog.from_spec(self.spec_path)
            plan = QueryPlan.from_dict(payload, source_catalog)
            trace.ontology_version = source_catalog.schema_version
            self._pass(trace, TraceStage.PLAN_VALIDATION, started)
        except Exception as exc:
            self._raise(trace, TraceStage.PLAN_VALIDATION, started, exc)

        started = perf_counter()
        try:
            catalog = SchemaCatalog.from_generated(
                self.generated_schema_path, self.spec_path
            )
            selected_labels = tuple(
                catalog.relevant_labels(
                    set(plan.requested_fields) | set(plan.filters), plan.evidence_required
                )
            )
            self._pass(trace, TraceStage.SCHEMA_SELECTION, started)
        except Exception as exc:
            self._raise(trace, TraceStage.SCHEMA_SELECTION, started, exc)

        started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.STATIC_VALIDATION,
            ProgressState.STARTED,
            0,
            candidate_attempt=candidate_attempt,
        )
        try:
            validated = CypherValidator(catalog, max_rows=self.max_rows).validate(plan, cypher)
            self._pass(trace, TraceStage.CYPHER_VALIDATION, started)
            emit_progress(
                progress_callback,
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                (perf_counter() - started) * 1000,
                validated_cypher=validated.text,
                parameters=dict(validated.parameters),
                labels=list(validated.labels),
                relationship_types=list(validated.relationship_types),
                limit=validated.limit,
                parameter_binding_verified=True,
                direct_evidence_path_verified=plan.evidence_required,
                candidate_attempt=candidate_attempt,
            )
        except Exception as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.FAILED,
                (perf_counter() - started) * 1000,
                error_code=getattr(exc, "code", exc.__class__.__name__),
                candidate_attempt=candidate_attempt,
            )
            self._raise(trace, TraceStage.CYPHER_VALIDATION, started, exc)

        started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.NEO4J_EXPLAIN,
            ProgressState.STARTED,
            0,
            candidate_attempt=candidate_attempt,
        )
        try:
            explained = self.explainer.explain(validated)
            self._pass(trace, TraceStage.NEO4J_EXPLAIN, started)
            emit_progress(
                progress_callback,
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.COMPLETED,
                (perf_counter() - started) * 1000,
                operators=list(explained.operators),
                candidate_attempt=candidate_attempt,
            )
        except Exception as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.FAILED,
                (perf_counter() - started) * 1000,
                error_code=getattr(exc, "code", exc.__class__.__name__),
                candidate_attempt=candidate_attempt,
            )
            self._raise(trace, TraceStage.NEO4J_EXPLAIN, started, exc)

        started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.GRAPH_EXECUTION,
            ProgressState.STARTED,
            0,
        )
        try:
            rows = self.executor.execute(explained)
            self._pass(trace, TraceStage.EXECUTION, started, row_count=len(rows))
            emit_progress(
                progress_callback,
                ProgressPhase.GRAPH_EXECUTION,
                ProgressState.COMPLETED,
                (perf_counter() - started) * 1000,
                row_count=len(rows),
            )
        except Exception as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.GRAPH_EXECUTION,
                ProgressState.FAILED,
                (perf_counter() - started) * 1000,
                error_code=getattr(exc, "code", exc.__class__.__name__),
            )
            self._raise(trace, TraceStage.EXECUTION, started, exc)

        started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.RESULT_VALIDATION,
            ProgressState.STARTED,
            0,
        )
        try:
            result = ResultValidator(max_rows=self.max_rows).validate(
                plan, rows, validated.provenance
            )
            self._pass(
                trace, TraceStage.RESULT_VALIDATION, started, row_count=result.row_count
            )
            emit_progress(
                progress_callback,
                ProgressPhase.RESULT_VALIDATION,
                ProgressState.COMPLETED,
                (perf_counter() - started) * 1000,
                row_count=result.row_count,
                fact_count=len(
                    {
                        row.get("fact_id")
                        for row in result.rows
                        if isinstance(row.get("fact_id"), str)
                    }
                ),
                evidence_count=result.evidence_count,
                fact_status_verified=True,
                evidence_status_verified=True,
                direct_provenance_verified=True,
                rejected_row_count=0,
            )
        except Exception as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.RESULT_VALIDATION,
                ProgressState.FAILED,
                (perf_counter() - started) * 1000,
                error_code=getattr(exc, "code", exc.__class__.__name__),
            )
            self._raise(trace, TraceStage.RESULT_VALIDATION, started, exc)

        trace_path = trace.write()
        return PipelineOutcome(
            request_id=trace.request_id,
            plan=plan,
            result=result,
            selected_labels=selected_labels,
            explain_operators=explained.operators,
            validated_cypher=validated.text,
            trace_path=trace_path,
        )

    @staticmethod
    def _pass(
        trace: QueryTrace,
        stage: TraceStage,
        started: float,
        *,
        row_count: int | None = None,
    ) -> None:
        trace.record(
            stage,
            TraceStatus.PASS,
            (perf_counter() - started) * 1000,
            row_count=row_count,
        )

    @staticmethod
    def _raise(trace: QueryTrace, stage: TraceStage, started: float, exc: Exception) -> None:
        code = getattr(exc, "code", exc.__class__.__name__)
        trace.record(
            stage,
            TraceStatus.FAIL,
            (perf_counter() - started) * 1000,
            error_code=str(code),
        )
        trace.skip_after(stage)
        trace_path = trace.write()
        raise SafetyPipelineError(stage, str(code), str(exc), trace_path) from exc
