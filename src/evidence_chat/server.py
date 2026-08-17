"""Local-only Starlette UI for the approved curriculum chat service.

The app has one backend process and one official query path::

    browser -> /api/ask -> CurriculumChatService -> ChatResponse

It never accepts Cypher, exposes a query plan, or constructs a ChatResponse itself.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from time import perf_counter
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
from kg_builder.query.query_executor import DynamicQueryExecutor
from kg_builder.query.query_explainer import QueryExplainer
from kg_builder.query.query_plan import MAX_QUESTION_LENGTH
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
    def ask(self, question: str) -> ChatResponse: ...


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
    source = await anyio.to_thread.run_sync(pdf_evidence.inspect_pdf)
    payload: dict[str, Any] = {
        "service_ready": state.ready,
        "error": state.error,
        "pdf": source.to_public_dict(),
        "examples": list(EXAMPLE_QUESTIONS),
        "max_question_length": MAX_QUESTION_LENGTH,
        "client_timeout_seconds": state.client_timeout_seconds,
        "debug": state.debug,
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
        started = perf_counter()
        yield _sse(
            {
                "type": "progress",
                "phase": "SUBMITTED",
                "message": "질문 전송됨",
                "elapsed_ms": 0,
            }
        )
        yield _sse(
            {
                "type": "progress",
                "phase": "CHECKING",
                "message": "답변을 확인하고 있습니다",
                "elapsed_ms": 0,
            }
        )
        try:
            async with limiter:
                response = await anyio.to_thread.run_sync(
                    service.ask, question, resolved
                )
                result = await anyio.to_thread.run_sync(
                    adapter.adapt, response
                )
            yield _sse(result)
            yield _sse(
                {
                    "type": "progress",
                    "phase": "COMPLETED",
                    "message": "답변 완료",
                    "elapsed_ms": round((perf_counter() - started) * 1000),
                }
            )
        except anyio.get_cancelled_exc_class():
            return
        except Exception:
            error: dict[str, Any] = {
                "type": "error",
                "message": "요청을 안전하게 처리하지 못했습니다.",
            }
            if state.debug:
                error["error_code"] = "CHAT_REQUEST_FAILED"
            yield _sse(error)
        yield _sse({"type": "end"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def pdf_page(request: Request) -> Response:
    page_number = request.path_params["page"]
    try:
        image = await anyio.to_thread.run_sync(pdf_evidence.render_page_png, page_number)
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
        await anyio.to_thread.run_sync(state.open)
        state.limiter = anyio.Semaphore(state.max_concurrent)
        print(
            "[evidence-chat] 서비스 준비됨"
            if state.ready
            else "[evidence-chat] 서비스 준비 실패"
        )
        source = await anyio.to_thread.run_sync(pdf_evidence.inspect_pdf)
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
