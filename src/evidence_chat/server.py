"""챗봇 화면을 제공하는 로컬 전용 Starlette 앱.

노출하는 엔드포인트는 다음 네 개다.

- `GET  /`                     : 3단계 화면 정적 페이지
- `GET  /api/health`           : Neo4j·PDF 탑재 상태
- `POST /api/ask`              : 질문 1건을 SSE 단계 이벤트로 스트리밍
- `GET  /api/pdf/page/{n}.png` : 발췌 PDF 페이지 렌더 이미지

임의 Cypher 입력 경로는 없다. 질문은 `evidence_chat.planner`가 지원 Intent로만 바꾼다.
서버는 로컬 개발용이며 인증을 제공하지 않으므로 127.0.0.1에만 바인딩한다.

앱 상태는 모듈 전역이 아니라 `app.state`에 둔다. 테스트가 전역을 덮어쓰면 이후
테스트에 상태가 새기 때문이다. 핸들러는 `request.app.state`로 접근한다.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, AsyncIterator

import anyio

from neo4j import GraphDatabase
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from kg_builder.config import ConfigurationError, Neo4jSettings
from kg_builder.query_service import Neo4jReadExecutor, QueryService

from . import pdf_evidence
from .pipeline import ChatPipeline
from .planner import EXAMPLE_QUESTIONS, RuleBasedPlanner


STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8501
MAX_BODY_BYTES = 8 * 1024
_SENTINEL = object()


class ChatState:
    """드라이버와 서비스를 앱 수명 동안 재사용한다."""

    def __init__(self) -> None:
        self.driver: Any | None = None
        self.pipeline: ChatPipeline | None = None
        self.settings: Neo4jSettings | None = None
        self.error: str | None = None

    def open(self) -> None:
        try:
            self.settings = Neo4jSettings.from_env()
        except ConfigurationError as exc:
            self.error = f"Neo4j 설정 오류: {exc}"
            return
        try:
            self.driver = GraphDatabase.driver(
                self.settings.uri, auth=(self.settings.user, self.settings.password)
            )
            self.driver.verify_connectivity()
        except Exception as exc:
            self.error = f"Neo4j 연결 실패: {type(exc).__name__}: {exc}"
            self.driver = None
            return
        service = QueryService(Neo4jReadExecutor(self.driver, self.settings.database))
        self.pipeline = ChatPipeline(runner=service, planner=RuleBasedPlanner())

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None


def _state(request: Request) -> ChatState:
    return request.app.state.chat


async def index(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "index.html")


async def health(request: Request) -> Response:
    state = _state(request)
    # inspect_pdf는 파일 읽기와 해시를 하므로 이벤트 루프에서 직접 돌리지 않는다.
    source = await anyio.to_thread.run_sync(pdf_evidence.inspect_pdf)
    return JSONResponse(
        {
            "neo4j_connected": state.pipeline is not None,
            "neo4j_endpoint": state.settings.endpoint if state.settings else None,
            "neo4j_database": state.settings.database if state.settings else None,
            "error": state.error,
            "pdf": source.to_dict(),
            "examples": list(EXAMPLE_QUESTIONS),
        }
    )


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
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        return JSONResponse({"error": "question 필드가 필요합니다."}, status_code=400)
    state = _state(request)
    if state.pipeline is None:
        return JSONResponse(
            {"error": state.error or "Neo4j에 연결되지 않았습니다."}, status_code=503
        )

    pipeline = state.pipeline

    async def stream() -> AsyncIterator[bytes]:
        # 파이프라인은 동기 Neo4j 드라이버를 쓰므로 워커 스레드에서 돌리고
        # 무제한 큐로 건네받는다. 큐가 막히지 않으므로 클라이언트가 먼저
        # 끊어져도 워커는 스스로 끝난다.
        bucket: queue.Queue[Any] = queue.Queue()

        def worker() -> None:
            try:
                for item in pipeline.run(question):
                    bucket.put(item)
            except Exception as exc:  # 예기치 못한 오류도 화면에 남긴다.
                bucket.put(
                    {
                        "type": "error",
                        "stage": "server",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
            finally:
                bucket.put(_SENTINEL)

        threading.Thread(target=worker, name="evidence-chat-pipeline", daemon=True).start()
        while True:
            item = await anyio.to_thread.run_sync(bucket.get)
            if item is _SENTINEL:
                break
            yield _sse(item)
        yield _sse({"type": "end"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


async def pdf_page(request: Request) -> Response:
    # 경로 변환기가 `{page:int}`라 여기 오는 값은 이미 정수다.
    page_number = request.path_params["page"]
    try:
        image = await anyio.to_thread.run_sync(pdf_evidence.render_page_png, page_number)
    except pdf_evidence.PdfEvidenceError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return Response(
        image,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    state = ChatState()
    app.state.chat = state
    await anyio.to_thread.run_sync(state.open)
    if state.error:
        print(f"[evidence-chat] {state.error}")
    else:
        endpoint = state.settings.endpoint if state.settings else "?"
        print(f"[evidence-chat] Neo4j 연결됨: {endpoint}")
    source = await anyio.to_thread.run_sync(pdf_evidence.inspect_pdf)
    print(
        f"[evidence-chat] PDF: {'탑재됨' if source.available else '없음'} · {source.path}"
        + (f" · {source.reason}" if source.reason else "")
    )
    try:
        yield
    finally:
        state.close()


def create_app() -> Starlette:
    """앱 인스턴스를 만든다. 상태는 `app.state.chat`에 붙는다."""
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
