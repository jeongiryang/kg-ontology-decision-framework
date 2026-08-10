"""챗봇 화면을 제공하는 로컬 전용 Starlette 앱.

노출하는 엔드포인트는 다음 네 개다.

- `GET  /`                     : 3단계 화면 정적 페이지
- `GET  /api/health`           : Neo4j·PDF 탑재 상태
- `POST /api/ask`              : 질문 1건을 SSE 단계 이벤트로 스트리밍
- `GET  /api/pdf/page/{n}.png` : 발췌 PDF 페이지 렌더 이미지

임의 Cypher 입력 경로는 없다. 질문은 `evidence_chat.planner`가 지원 Intent로만 바꾼다.
서버는 로컬 개발용이며 인증을 제공하지 않으므로 127.0.0.1에만 바인딩한다.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import queue
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

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


class AppState:
    """드라이버와 서비스를 앱 수명 동안 재사용한다."""

    def __init__(self) -> None:
        self.driver: Any | None = None
        self.service: QueryService | None = None
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
        self.service = QueryService(Neo4jReadExecutor(self.driver, self.settings.database))
        self.pipeline = ChatPipeline(runner=self.service, planner=RuleBasedPlanner())

    def close(self) -> None:
        if self.driver is not None:
            self.driver.close()
            self.driver = None


state = AppState()


async def index(request: Request) -> Response:
    return FileResponse(STATIC_DIR / "index.html")


async def health(request: Request) -> Response:
    source = pdf_evidence.inspect_pdf()
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
    if state.pipeline is None:
        return JSONResponse(
            {"error": state.error or "Neo4j에 연결되지 않았습니다."}, status_code=503
        )

    pipeline = state.pipeline

    def produce() -> Iterator[dict[str, Any]]:
        try:
            yield from pipeline.run(question)
        except Exception as exc:  # 예기치 못한 오류도 화면에 남긴다.
            yield {"type": "error", "stage": "server", "message": f"{type(exc).__name__}: {exc}"}

    async def stream() -> AsyncIterator[bytes]:
        # 파이프라인은 동기 Neo4j 드라이버를 쓰므로 워커 스레드에서 돌리고
        # 무제한 큐로 건네받는다. 큐가 막히지 않으므로 클라이언트가 먼저
        # 끊어져도 워커는 스스로 끝난다.
        bucket: queue.Queue[Any] = queue.Queue()

        def worker() -> None:
            try:
                for item in produce():
                    bucket.put(item)
            finally:
                bucket.put(_SENTINEL)

        threading.Thread(target=worker, name="chatbot-pipeline", daemon=True).start()
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
    try:
        page_number = int(request.path_params["page"])
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "페이지 번호가 올바르지 않습니다."}, status_code=400)
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
async def lifespan(_: Starlette) -> AsyncIterator[None]:
    await anyio.to_thread.run_sync(state.open)
    if state.error:
        print(f"[evidence-chat] {state.error}")
    else:
        endpoint = state.settings.endpoint if state.settings else "?"
        print(f"[evidence-chat] Neo4j 연결됨: {endpoint}")
    source = pdf_evidence.inspect_pdf()
    print(
        f"[evidence-chat] PDF: {'탑재됨' if source.available else '없음'} · {source.path}"
        + (f" · {source.reason}" if source.reason else "")
    )
    try:
        yield
    finally:
        state.close()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/health", health),
        Route("/api/ask", ask, methods=["POST"]),
        Route("/api/pdf/page/{page:int}.png", pdf_page),
        Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
    ],
    lifespan=lifespan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified KG 챗봇 화면 실행")
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
