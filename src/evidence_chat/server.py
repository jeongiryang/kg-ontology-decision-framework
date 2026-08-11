"""Local-only Starlette UI for the approved curriculum chat service.

The app has one backend process and one official query path::

    browser -> /api/ask -> CurriculumChatService -> ChatResponse

It never accepts Cypher, exposes a query plan, or constructs a ChatResponse itself.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
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


STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8501
DEFAULT_CLIENT_TIMEOUT_SECONDS = 180
DEFAULT_MAX_CONCURRENT = 1
MAX_BODY_BYTES = 16 * 1024

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
        self, question: str, progress_callback: ProgressCallback | None = None
    ) -> ChatResponse: ...


class InspectionCollector:
    """Build stage-scoped inspection updates from approved pipeline facts only."""

    _STATIC_DETAIL_KEYS = frozenset(
        {"validated_cypher", "parameters", "labels", "relationship_types"}
    )
    _SECRET_KEY_MARKERS = ("password", "token", "secret", "api_key", "uri")
    _LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*\Z", re.IGNORECASE)

    def __init__(self) -> None:
        self.stage_timings_ms: dict[str, int] = {}
        self._active_attempt: int | None = None
        self._pending_query: dict[str, Any] = {}
        self._pending_attempt: int | None = None
        self._approved_query: dict[str, Any] = {}

    @staticmethod
    def _candidate_attempt(event: ProgressEvent) -> int | None:
        value = event.details.get("candidate_attempt")
        return value if isinstance(value, int) and value > 0 else None

    def _discard_candidate(self) -> None:
        self._pending_query = {}
        self._pending_attempt = None
        self._approved_query = {}

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
        return {
            "type": "inspection_update",
            "stage": event.phase.value,
            "status": event.state.value,
            "summary": summary,
            "elapsed_ms": event.elapsed_ms,
        }

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
                if "validated_cypher" in candidate and "parameters" in candidate:
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
                        },
                    )
            return None

        if event.state is not ProgressState.COMPLETED:
            return None
        self.stage_timings_ms[event.phase.value] = event.elapsed_ms
        summary: dict[str, Any]
        if event.phase is ProgressPhase.QUESTION_ANALYSIS:
            summary = {
                "status": event.details.get("planning_status"),
                "query_plan": self._safe_plan(event.details.get("query_plan")),
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
                "message": "LLM이 Cypher 후보를 생성했습니다. 안전 검증을 진행합니다.",
            }
        elif event.phase is ProgressPhase.GRAPH_EXECUTION:
            summary = {"row_count": self._safe_count(event.details.get("row_count"))}
        elif event.phase is ProgressPhase.RESULT_VALIDATION:
            summary = {
                "row_count": self._safe_count(event.details.get("row_count")),
                "fact_count": self._safe_count(event.details.get("fact_count")),
                "verified_evidence_count": self._safe_count(
                    event.details.get("evidence_count")
                ),
                "fact_status_verified": event.details.get("fact_status_verified") is True,
                "evidence_status_verified": (
                    event.details.get("evidence_status_verified") is True
                ),
                "direct_provenance_verified": (
                    event.details.get("direct_provenance_verified") is True
                ),
            }
        elif event.phase is ProgressPhase.CLAIM_BUILDING:
            summary = {"claim_count": self._safe_count(event.details.get("claim_count"))}
        elif event.phase is ProgressPhase.ANSWER_RENDERING:
            summary = {
                "citation_count": self._safe_count(
                    event.details.get(
                        "citation_count", event.details.get("evidence_count", 0)
                    )
                )
            }
        elif event.phase is ProgressPhase.COMPLETED:
            summary = {
                "total_elapsed_ms": event.elapsed_ms,
                "stage_timings_ms": dict(sorted(self.stage_timings_ms.items())),
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


async def index(request: Request) -> Response:
    del request
    return FileResponse(STATIC_DIR / "index.html")


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


async def ask(request: Request) -> Response:
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        return JSONResponse({"error": "요청 본문이 너무 큽니다."}, status_code=413)
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"error": "JSON 본문을 해석할 수 없습니다."}, status_code=400)
    if not isinstance(payload, dict) or set(payload) != {"question"}:
        return JSONResponse({"error": "question 필드만 전송할 수 있습니다."}, status_code=400)
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
            finished = asyncio.Event()

            def on_progress(event: ProgressEvent) -> None:
                inspection_update = collector.record(event)
                loop.call_soon_threadsafe(queue.put_nowait, event.public_payload())
                if state.show_query_details and inspection_update is not None:
                    loop.call_soon_threadsafe(queue.put_nowait, inspection_update)

            def worker() -> None:
                try:
                    response = service.ask(question, on_progress)
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
                    loop.call_soon_threadsafe(queue.put_nowait, {"type": "end"})
                    loop.call_soon_threadsafe(finished.set)

            async with limiter:
                Thread(target=worker, name="evidence-chat-request", daemon=True).start()
                await finished.wait()

        task = asyncio.create_task(run_request())
        try:
            while True:
                item = await queue.get()
                yield _sse(item)
                if item.get("type") == "end":
                    break
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
            Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
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
