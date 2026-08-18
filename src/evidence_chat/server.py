"""Local-only Starlette UI for the approved curriculum chat service.

The app has one backend process and one official query path::

    browser -> /api/ask -> CurriculumChatService -> ChatResponse

It never accepts Cypher, exposes a query plan, or constructs a ChatResponse itself.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import secrets
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from threading import Thread
from typing import Any, Protocol

import anyio
from neo4j import GraphDatabase
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from kg_builder.answer.contracts import ChatResponse
from kg_builder.answer.service import CurriculumChatService
from kg_builder.config import ConfigurationError, Neo4jQuerySettings
from kg_builder.llm.client import LLMConfigurationError, LLMSettings, create_llm_client
from kg_builder.llm.cypher_generator import LocalCypherGenerator
from kg_builder.llm.planner import LocalQueryPlanner
from kg_builder.query.cypher_validator import CypherValidationError, lex_cypher
from kg_builder.query.natural_language_service import NaturalLanguageQueryService
from kg_builder.query.progress import (
    ProgressCallback,
    ProgressEvent,
    ProgressPhase,
    ProgressState,
)
from kg_builder.query.query_executor import DynamicQueryExecutor
from kg_builder.query.query_explainer import QueryExplainer
from kg_builder.query.query_plan import MAX_QUESTION_LENGTH
from kg_builder.query.query_trace import EMAIL_PATTERN, PHONE_PATTERN, STUDENT_ID_PATTERN
from kg_builder.query.safety_pipeline import SafetyPipeline
from kg_builder.query.schema_selector import QuerySchemaSelector

from . import pdf_evidence
from .chat_adapter import ChatResponseAdapter
from .graph_projection import (
    build_provenance_projection,
    build_query_structure_projection,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8501
DEFAULT_CLIENT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_CONCURRENT = 1
MAX_BODY_BYTES = 16 * 1024
# 되묻기로 채울 수 있는 항목 수 상한. 이 이상은 정상 흐름이 아니다.
MAX_RESOLVED_ENTRIES = 8

EXAMPLE_QUESTIONS = (
    "2026학년도 교양 최소 이수학점은?",
    "균형교양 이수요건은?",
    "편입생도 교양을 이수해야 하나?",
    "자료구조는 몇 학년 몇 학기에 개설되나?",
    "컴퓨터공학과 전공필수 과목은?",
    "자료구조의 이수구분은?",
)


class ChatService(Protocol):
    def ask(
        self,
        question: str,
        *,
        resolved: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ChatResponse: ...


class InspectionCollector:
    """Build stage-scoped inspection updates from approved pipeline facts only."""

    _STATIC_DETAIL_KEYS = frozenset(
        {
            "validated_cypher",
            "parameters",
            "labels",
            "relationship_types",
            "limit",
        }
    )
    _SECRET_KEY_MARKERS = ("password", "token", "secret", "api_key", "uri")
    _LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*\Z", re.IGNORECASE)

    def __init__(self) -> None:
        self.stage_timings_ms: dict[str, int] = {}
        self._active_attempt: int | None = None
        self._pending_query: dict[str, Any] = {}
        self._pending_attempt: int | None = None
        self._approved_query: dict[str, Any] = {}
        self._opaque_key = secrets.token_bytes(32)
        self._retry_count = 0
        self._result_validation_approved = False

    @staticmethod
    def _candidate_attempt(event: ProgressEvent) -> int | None:
        value = event.details.get("candidate_attempt")
        return value if isinstance(value, int) and value > 0 else None

    def _discard_candidate(self) -> None:
        self._pending_query = {}
        self._pending_attempt = None
        self._approved_query = {}

    @staticmethod
    def _canonical_cypher(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        try:
            lexed = lex_cypher(value)
        except CypherValidationError:
            return None
        canonical = lexed.canonical
        if not canonical or canonical != value.strip() or lexed.backtick_identifiers:
            return None
        return canonical

    @classmethod
    def _mask_text(cls, value: str) -> str:
        masked = EMAIL_PATTERN.sub("<redacted-email>", value)
        masked = PHONE_PATTERN.sub("<redacted-phone>", masked)
        return STUDENT_ID_PATTERN.sub("<redacted-student-id>", masked)[:256]

    @classmethod
    def _safe_mapping(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        output: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or len(raw_key) > 80:
                continue
            if any(marker in raw_key.lower() for marker in cls._SECRET_KEY_MARKERS):
                output[raw_key] = "<redacted>"
            elif isinstance(raw_value, str):
                output[raw_key] = cls._mask_text(raw_value)
            elif isinstance(raw_value, bool) or raw_value is None:
                output[raw_key] = raw_value
            elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                output[raw_key] = raw_value
            elif isinstance(raw_value, (list, tuple)):
                output[raw_key] = [
                    cls._mask_text(item) if isinstance(item, str) else item
                    for item in raw_value[:100]
                    if isinstance(item, (str, int, float, bool)) or item is None
                ]
        return output

    @classmethod
    def _safe_plan(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        plan: dict[str, Any] = {}
        intent = value.get("intent")
        if isinstance(intent, str):
            plan["intent"] = cls._mask_text(intent)
        plan["filters"] = cls._safe_mapping(value.get("filters"))
        fields = value.get("requested_fields")
        if isinstance(fields, (list, tuple)):
            plan["requested_fields"] = [
                item for item in fields[:100] if isinstance(item, str) and len(item) <= 80
            ]
        for key in ("evidence_required", "selection_mode"):
            item = value.get(key)
            if isinstance(item, (str, bool)):
                plan[key] = item
        return plan

    @staticmethod
    def _safe_strings(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)):
            return []
        return sorted({item for item in value if isinstance(item, str) and len(item) <= 80})

    @staticmethod
    def _safe_count(value: Any) -> int:
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            else 0
        )

    @staticmethod
    def _update(event: ProgressEvent, summary: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "inspection_update",
            "version": 2,
            "stage": event.phase.value,
            "status": event.state.value,
            "summary": summary,
            "elapsed_ms": event.elapsed_ms,
        }
        attempt = InspectionCollector._candidate_attempt(event)
        if attempt is not None:
            payload["attempt"] = attempt
        return payload

    @staticmethod
    def _safe_error_code(event: ProgressEvent) -> str:
        return str(event.public_payload().get("error_code", "PIPELINE_STAGE_FAILED"))

    def record(self, event: ProgressEvent) -> dict[str, Any] | None:
        attempt = self._candidate_attempt(event)
        if (
            event.phase is ProgressPhase.CYPHER_GENERATION
            and event.state is ProgressState.STARTED
        ):
            self._active_attempt = attempt
            self._discard_candidate()
            if attempt is not None and attempt > 1:
                self._retry_count += 1
                return self._update(
                    event,
                    {
                        "retry": True,
                        "discard_previous_candidate": True,
                    },
                )
            return None

        if event.state is ProgressState.FAILED:
            self.stage_timings_ms[event.phase.value] = event.elapsed_ms
            if event.phase in {
                ProgressPhase.STATIC_VALIDATION,
                ProgressPhase.NEO4J_EXPLAIN,
            }:
                self._discard_candidate()
            return self._update(
                event,
                {"error_code": self._safe_error_code(event)},
            )

        if event.phase is ProgressPhase.STATIC_VALIDATION:
            if (
                event.state is ProgressState.COMPLETED
                and attempt is not None
                and attempt == self._active_attempt
            ):
                candidate = {
                    key: event.details[key]
                    for key in self._STATIC_DETAIL_KEYS
                    if key in event.details
                }
                canonical = self._canonical_cypher(candidate.get("validated_cypher"))
                if canonical is not None and "parameters" in candidate:
                    candidate["validated_cypher"] = canonical
                    self._pending_query = candidate
                    self._pending_attempt = attempt
                else:
                    self._discard_candidate()
                self.stage_timings_ms[event.phase.value] = event.elapsed_ms
                return self._update(
                    event,
                    {
                        "read_only_syntax_verified": True,
                        "ontology_schema_verified": True,
                        "parameter_binding_verified": (
                            event.details.get("parameter_binding_verified") is True
                        ),
                        "direct_evidence_path_verified": (
                            event.details.get("direct_evidence_path_verified") is True
                        ),
                        "comment_free_canonical": True,
                        "limit": self._safe_count(event.details.get("limit")),
                    },
                )
            return None

        if event.phase is ProgressPhase.NEO4J_EXPLAIN:
            if (
                event.state is ProgressState.COMPLETED
                and attempt is not None
                and attempt == self._active_attempt == self._pending_attempt
                and self._pending_query
            ):
                operators = event.details.get("operators")
                if isinstance(operators, list) and all(
                    isinstance(item, str) for item in operators
                ):
                    cypher = self._pending_query["validated_cypher"]
                    limit_match = self._LIMIT.search(cypher)
                    self._approved_query = {
                        **self._pending_query,
                        "operators": list(operators),
                        "limit": int(limit_match.group(1)) if limit_match else None,
                    }
                self._pending_query = {}
                self._pending_attempt = None
                self.stage_timings_ms[event.phase.value] = event.elapsed_ms
                if self._approved_query:
                    query_graph = build_query_structure_projection(
                        self._safe_strings(self._approved_query.get("labels")),
                        self._safe_strings(
                            self._approved_query.get("relationship_types")
                        ),
                        opaque_key=self._opaque_key,
                    )
                    return self._update(
                        event,
                        {
                            "approved_cypher": self._approved_query["validated_cypher"],
                            "parameters": self._safe_mapping(
                                self._approved_query["parameters"]
                            ),
                            "operators": self._safe_strings(
                                self._approved_query["operators"]
                            ),
                            "labels": self._safe_strings(
                                self._approved_query.get("labels")
                            ),
                            "relationships": self._safe_strings(
                                self._approved_query.get("relationship_types")
                            ),
                            "limit": self._approved_query["limit"],
                            "query_graph": query_graph,
                        },
                    )
            return None

        if event.state is not ProgressState.COMPLETED:
            return None
        self.stage_timings_ms[event.phase.value] = event.elapsed_ms
        summary: dict[str, Any]
        if event.phase is ProgressPhase.QUESTION_ANALYSIS:
            options = event.details.get("clarification_options")
            summary = {
                "status": event.details.get("planning_status"),
                "query_plan": self._safe_plan(event.details.get("query_plan")),
                "missing": self._safe_strings(event.details.get("missing")),
                "clarification_available": bool(
                    isinstance(options, (list, tuple)) and options
                ),
            }
        elif event.phase is ProgressPhase.SCHEMA_SELECTION:
            labels = self._safe_strings(event.details.get("labels"))
            relationships = self._safe_strings(event.details.get("relationship_types"))
            summary = {
                "labels": labels,
                "relationships": relationships,
                "node_label_count": len(labels),
                "relationship_count": len(relationships),
            }
        elif event.phase is ProgressPhase.CYPHER_GENERATION:
            summary = {
                "candidate_generated": True,
                "candidate_attempt": attempt,
                "retry": bool(attempt is not None and attempt > 1),
                "message": "LLM이 Cypher 후보를 생성했습니다. 안전 검증을 진행합니다.",
            }
        elif event.phase is ProgressPhase.GRAPH_EXECUTION:
            summary = {
                "row_count": self._safe_count(event.details.get("row_count")),
                "query_elapsed_ms": event.elapsed_ms,
            }
        elif event.phase is ProgressPhase.RESULT_VALIDATION:
            fact_status_verified = event.details.get("fact_status_verified") is True
            evidence_status_verified = (
                event.details.get("evidence_status_verified") is True
            )
            direct_provenance_verified = (
                event.details.get("direct_provenance_verified") is True
            )
            self._result_validation_approved = (
                fact_status_verified
                and evidence_status_verified
                and direct_provenance_verified
            )
            summary = {
                "row_count": self._safe_count(event.details.get("row_count")),
                "fact_count": self._safe_count(event.details.get("fact_count")),
                "verified_evidence_count": self._safe_count(
                    event.details.get("evidence_count")
                ),
                "fact_status_verified": fact_status_verified,
                "evidence_status_verified": evidence_status_verified,
                "direct_provenance_verified": direct_provenance_verified,
                "rejected_row_count": self._safe_count(
                    event.details.get("rejected_row_count")
                ),
            }
        elif event.phase is ProgressPhase.CLAIM_BUILDING:
            rows = event.details.get("validated_rows")
            pairs = event.details.get("approved_provenance")
            provenance_graph = None
            if (
                self._result_validation_approved
                and isinstance(rows, (list, tuple))
                and isinstance(pairs, (list, tuple))
            ):
                provenance_graph = build_provenance_projection(
                    rows,
                    pairs,
                    opaque_key=self._opaque_key,
                )
            summary = {
                "claim_count": self._safe_count(event.details.get("claim_count")),
                "claim_types": self._safe_strings(event.details.get("claim_types")),
                "aggregate": event.details.get("aggregate") is True,
                "citation_target_count": self._safe_count(
                    event.details.get("citation_target_count")
                ),
                "provenance_graph": provenance_graph,
            }
        elif event.phase is ProgressPhase.ANSWER_RENDERING:
            summary = {
                "citation_count": self._safe_count(
                    event.details.get(
                        "citation_count", event.details.get("evidence_count", 0)
                    )
                ),
                "deterministic_renderer": (
                    event.details.get("deterministic_renderer") is True
                ),
                "final_answer_llm_calls": self._safe_count(
                    event.details.get("final_answer_llm_calls")
                ),
            }
        elif event.phase is ProgressPhase.COMPLETED:
            summary = {
                "total_elapsed_ms": event.elapsed_ms,
                "stage_timings_ms": dict(sorted(self.stage_timings_ms.items())),
                "final_status": event.details.get("final_status"),
                "retry_count": self._retry_count,
                "citation_count": self._safe_count(
                    event.details.get("citation_count")
                ),
            }
        else:
            return None
        return self._update(event, summary)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


class ChatState:
    """Own long-lived LLM/Neo4j resources for one Starlette application."""

    def __init__(
        self,
        service: ChatService | None = None,
        *,
        trace_dir: Path | None = None,
    ) -> None:
        self.driver: Any | None = None
        self.service = service
        self.error: str | None = None
        self.error_code: str | None = None
        self.debug = False
        self.show_query_details = False
        self.max_concurrent = DEFAULT_MAX_CONCURRENT
        self.client_timeout_seconds = DEFAULT_CLIENT_TIMEOUT_SECONDS
        self.limiter: anyio.Semaphore | None = None
        self.trace_dir = trace_dir

    @property
    def ready(self) -> bool:
        return self.service is not None

    def open(self) -> None:
        try:
            self.debug = _env_bool("KG_CHAT_DEBUG")
            self.show_query_details = _env_bool("KG_CHAT_SHOW_QUERY_DETAILS")
            self.max_concurrent = _env_int(
                "KG_CHAT_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT, 1, 4
            )
            self.client_timeout_seconds = _env_int(
                "KG_CHAT_CLIENT_TIMEOUT_SECONDS",
                DEFAULT_CLIENT_TIMEOUT_SECONDS,
                60,
                900,
            )
            if self.service is not None:
                return
            llm_settings = LLMSettings.from_env()
            neo4j_settings = Neo4jQuerySettings.from_env()
            client = create_llm_client(llm_settings)
            self.driver = GraphDatabase.driver(
                neo4j_settings.uri,
                auth=(neo4j_settings.user, neo4j_settings.password),
            )
            self.driver.verify_connectivity()
            safety_options = {"trace_dir": self.trace_dir} if self.trace_dir else {}
            safety = SafetyPipeline(
                QueryExplainer(self.driver, neo4j_settings.database),
                DynamicQueryExecutor(self.driver, neo4j_settings.database),
                **safety_options,
            )
            query_service = NaturalLanguageQueryService(
                LocalQueryPlanner(client),
                LocalCypherGenerator(client),
                safety,
                QuerySchemaSelector(),
                model=llm_settings.model,
                generator_retries=llm_settings.max_retries,
            )
            self.service = CurriculumChatService(query_service)
        except (ConfigurationError, LLMConfigurationError):
            self.error = "서비스 환경 설정을 확인해 주세요."
            self.error_code = "CHAT_CONFIGURATION_ERROR"
            self._close_driver()
        except Exception:
            self.error = "로컬 질의 서비스에 연결할 수 없습니다."
            self.error_code = "CHAT_STARTUP_ERROR"
            self._close_driver()

    def _close_driver(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def close(self) -> None:
        self._close_driver()


def _state(request: Request) -> ChatState:
    return request.app.state.chat


class _FreshStaticFiles(StaticFiles):
    """Serve the screen assets without letting the browser keep an old copy.

    ``index.html`` 만 캐시를 막아 두면 새 문서가 예전 ``app.js`` 를 부른다. 화면이 멈춘
    이유가 코드에 없어 찾기 어렵다(2026-08-14 실측). 로컬 개발 서버라 매번 내려받아도
    비용이 없다.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


async def index(request: Request) -> Response:
    del request
    # 화면 문서는 캐시하지 않는다. 브라우저가 예전 index.html 을 들고 있으면 새
    # app.js 가 없는 요소를 찾다가 답변 렌더링이 통째로 멈춘다. 로컬 개발 서버라
    # 매번 내려받아도 비용이 없다.
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


async def health(request: Request) -> Response:
    state = _state(request)
    source = pdf_evidence.inspect_pdf()
    payload: dict[str, Any] = {
        "service_ready": state.ready,
        "error": state.error,
        "pdf_mounted": source.available,
        "examples": list(EXAMPLE_QUESTIONS),
        "max_question_length": MAX_QUESTION_LENGTH,
        "client_timeout_seconds": state.client_timeout_seconds,
        "debug": state.debug,
        "show_query_details": state.show_query_details,
    }
    if state.debug:
        payload["error_code"] = state.error_code
    return JSONResponse(payload)


def _sse(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


def _clarification_options_event(event: ProgressEvent) -> dict[str, Any] | None:
    """Build the versioned presentation envelope outside sealed ChatResponse."""

    if (
        event.phase is not ProgressPhase.QUESTION_ANALYSIS
        or event.state is not ProgressState.COMPLETED
        or event.details.get("planning_status") != "CLARIFICATION_REQUIRED"
    ):
        return None
    raw_missing = event.details.get("missing")
    missing = [
        item
        for item in raw_missing if isinstance(item, str) and re.fullmatch(r"[A-Z_]{1,80}", item)
    ] if isinstance(raw_missing, (list, tuple)) else []
    raw_options = event.details.get("clarification_options")
    options: list[dict[str, Any]] = []
    if isinstance(raw_options, (list, tuple)):
        for option in raw_options[:100]:
            filter_name = getattr(option, "filter_name", None)
            value = getattr(option, "value", None)
            label = getattr(option, "label", None)
            detail = getattr(option, "detail", None)
            if (
                not isinstance(filter_name, str)
                or not re.fullmatch(r"[a-z_]{1,80}", filter_name)
                or not isinstance(label, str)
                or not label.strip()
            ):
                continue
            try:
                canonical_value = json.dumps(
                    value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            except (TypeError, ValueError):
                continue
            choice_id = hashlib.sha256(
                f"{filter_name}:{canonical_value}".encode("utf-8")
            ).hexdigest()[:24]
            options.append(
                {
                    "choice_id": f"choice:{choice_id}",
                    "filter": filter_name,
                    "value": value,
                    "label": label.strip()[:160],
                    "detail": detail.strip()[:256]
                    if isinstance(detail, str) and detail.strip()
                    else None,
                }
            )
    return {
        "type": "clarification_options",
        "version": 1,
        "missing": missing,
        "options": options,
    }


async def ask(request: Request) -> Response:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"error": "요청 본문이 너무 큽니다."}, status_code=413)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "JSON 본문을 해석할 수 없습니다."}, status_code=400)
    if not isinstance(payload, dict) or not set(payload) <= {"question", "resolved"}:
        return JSONResponse(
            {"error": "question 과 resolved 필드만 전송할 수 있습니다."}, status_code=400
        )
    # 되묻기에서 사용자가 고른 값. 서버는 대화 상태를 들지 않으므로 매 요청에 함께
    # 온다. 값이 실제로 제시된 선택지였는지는 계획 계층이 다시 만들어 대조한다.
    resolved = payload.get("resolved") or {}
    if not isinstance(resolved, dict) or len(resolved) > MAX_RESOLVED_ENTRIES:
        return JSONResponse(
            {"error": "resolved 는 항목 수가 제한된 객체여야 합니다."}, status_code=400
        )
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return JSONResponse({"error": "question 필드가 필요합니다."}, status_code=400)
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        return JSONResponse(
            {"error": f"질문은 {MAX_QUESTION_LENGTH}자를 넘을 수 없습니다."},
            status_code=422,
        )
    state = _state(request)
    if not state.ready or state.service is None or state.limiter is None:
        return JSONResponse(
            {"error": state.error or "질의 서비스를 사용할 수 없습니다."},
            status_code=503,
        )

    service = state.service
    limiter = state.limiter
    adapter = ChatResponseAdapter(debug=state.debug)

    async def stream() -> AsyncIterator[bytes]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        collector = InspectionCollector()

        async def run_request() -> None:
            finished: asyncio.Future[None] = loop.create_future()

            def on_progress(event: ProgressEvent) -> None:
                inspection_update = collector.record(event)
                loop.call_soon_threadsafe(queue.put_nowait, event.public_payload())
                clarification_update = _clarification_options_event(event)
                if clarification_update is not None:
                    loop.call_soon_threadsafe(queue.put_nowait, clarification_update)
                if state.show_query_details and inspection_update is not None:
                    loop.call_soon_threadsafe(queue.put_nowait, inspection_update)

            def worker() -> None:
                try:
                    response = service.ask(
                        question,
                        resolved=resolved or None,
                        progress_callback=on_progress,
                    )
                    result = adapter.adapt(response)
                    loop.call_soon_threadsafe(queue.put_nowait, result)
                except Exception:
                    error: dict[str, Any] = {
                        "type": "error",
                        "message": "요청을 안전하게 처리하지 못했습니다.",
                    }
                    if state.debug:
                        error["error_code"] = "CHAT_REQUEST_FAILED"
                    loop.call_soon_threadsafe(queue.put_nowait, error)
                finally:
                    loop.call_soon_threadsafe(finished.set_result, None)

            async with limiter:
                # Keep the blocking local model call off the event loop.  The
                # independently-created request task is deliberately not cancelled
                # on a browser disconnect, so the single-GPU semaphore remains held
                # until the worker really exits.
                Thread(
                    target=worker,
                    name="evidence-chat-request",
                    daemon=True,
                ).start()
                await finished

        task = asyncio.create_task(run_request())
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.1)
                except TimeoutError:
                    continue
                yield _sse(item)
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        finally:
            # A disconnected browser cannot cancel an already-running local model
            # call.  Keep the task alive so it holds the single-GPU semaphore until
            # the worker finishes; its queue is intentionally unbounded and local.
            pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def pdf_page(request: Request) -> Response:
    page_number = request.path_params["page"]
    try:
        image = pdf_evidence.render_page_png(page_number)
    except pdf_evidence.PdfEvidenceError:
        return JSONResponse(
            {"error": "요청한 PDF 페이지를 표시할 수 없습니다."}, status_code=404
        )
    return Response(
        image,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def create_app(state_factory: Callable[[], ChatState] = ChatState) -> Starlette:
    """Create an app whose state factory can be replaced by isolated tests."""

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        state = state_factory()
        app.state.chat = state
        state.open()
        state.limiter = anyio.Semaphore(state.max_concurrent)
        print(
            "[evidence-chat] 서비스 준비됨"
            if state.ready
            else "[evidence-chat] 서비스 준비 실패"
        )
        source = pdf_evidence.inspect_pdf()
        print(f"[evidence-chat] PDF: {'탑재됨' if source.available else '없음'}")
        try:
            yield
        finally:
            state.close()

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/health", health),
            Route("/api/ask", ask, methods=["POST"]),
            Route("/api/pdf/page/{page:int}.png", pdf_page),
            Mount("/static", app=_FreshStaticFiles(directory=STATIC_DIR), name="static"),
        ],
        lifespan=lifespan,
    )


app = create_app()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified KG 근거 챗봇 화면 실행")
    parser.add_argument("--host", default=os.getenv("CHATBOT_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("CHATBOT_PORT", str(DEFAULT_PORT)))
    )
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
