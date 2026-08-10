"""질문 → 답변 파이프라인을 단계 이벤트로 흘려보낸다.

각 단계는 실제로 수행한 일만 보고한다. 수행하지 않은 단계는 `skipped`로,
실패한 단계는 `failed`로 표시하고 이유를 남긴다. 화면의 진행 표시가 실제
처리 상태와 어긋나지 않게 하는 것이 이 모듈의 계약이다.

따라서 `STEP_SEQUENCE`의 각 항목은 실제 작업 단위 하나에 대응해야 한다.
단계를 추가하면서 코드에서 그 일을 하지 않으면 계약이 깨진다.
`tests/test_evidence_chat.py`가 방출된 단계 순서와 이 튜플의 일치를 검사한다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator, Mapping, Protocol

from kg_builder.cypher_queries import QUERY_SPECS, ensure_read_only
from kg_builder.query_contracts import QueryRequest, QueryValidationError

from . import answer as answer_module
from . import pdf_evidence
from .planner import Plan, PlannerError, RuleBasedPlanner, normalize_question


class StepStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


STEP_SEQUENCE: tuple[tuple[str, str], ...] = (
    ("normalize", "질문 정규화"),
    ("plan", "질의 계획 수립"),
    ("contract", "요청 계약 검증"),
    ("cypher", "읽기 전용 Cypher 선택"),
    ("neo4j", "Neo4j 조회"),
    ("evidence", "VERIFIED 근거 수집"),
    ("pdf", "PDF 근거 위치 계산"),
    ("compose", "답변 구성"),
)
STEP_LABELS: dict[str, str] = dict(STEP_SEQUENCE)
STEP_INDEX: dict[str, int] = {
    step_id: position for position, (step_id, _) in enumerate(STEP_SEQUENCE)
}
STEP_TOTAL = len(STEP_SEQUENCE)


@dataclass(frozen=True, slots=True)
class StepEvent:
    step_id: str
    label: str
    status: StepStatus
    index: int
    total: int
    detail: tuple[str, ...] = ()
    data: Mapping[str, Any] | None = None
    elapsed_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "step",
            "step_id": self.step_id,
            "label": self.label,
            "status": self.status.value,
            "index": self.index,
            "total": self.total,
            "detail": list(self.detail),
        }
        if self.data is not None:
            payload["data"] = dict(self.data)
        if self.elapsed_ms is not None:
            payload["elapsed_ms"] = self.elapsed_ms
        return payload


def _event(
    step_id: str,
    status: StepStatus,
    detail: tuple[str, ...] = (),
    data: Mapping[str, Any] | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    return StepEvent(
        step_id=step_id,
        label=STEP_LABELS[step_id],
        status=status,
        index=STEP_INDEX[step_id] + 1,
        total=STEP_TOTAL,
        detail=detail,
        data=data,
        elapsed_ms=elapsed_ms,
    ).to_dict()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _format_parameters(parameters: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in sorted(parameters.items()))


def _fail(
    step_id: str, stage: str, message: str, started: float, skip_reason: str
) -> Iterator[dict[str, Any]]:
    """단계 실패를 보고하고 이후 단계를 건너뜀으로 표시한 뒤 오류를 낸다."""
    yield _event(step_id, StepStatus.FAILED, (message,), elapsed_ms=_ms(started))
    for later_id, _ in STEP_SEQUENCE[STEP_INDEX[step_id] + 1 :]:
        yield _event(later_id, StepStatus.SKIPPED, (skip_reason,))
    yield {"type": "error", "stage": stage, "message": message}


class QueryRunner(Protocol):
    """질의 실행기. 실제 구현은 `kg_builder.query_service.QueryService`다."""

    def execute(self, request: QueryRequest) -> Any: ...


@dataclass(slots=True)
class ChatPipeline:
    runner: QueryRunner
    planner: Any = field(default_factory=RuleBasedPlanner)

    def run(self, question: str) -> Iterator[dict[str, Any]]:
        """단계 이벤트를 순서대로 내보내고 마지막에 결과 또는 오류를 낸다."""

        # 1. 질문 정규화
        yield _event("normalize", StepStatus.RUNNING)
        started = time.perf_counter()
        try:
            normalized = normalize_question(question)
        except PlannerError as exc:
            yield from _fail(
                "normalize", "normalize", str(exc), started, "질문을 정규화하지 못해 건너뜀"
            )
            return
        yield _event(
            "normalize",
            StepStatus.DONE,
            (f"정규화된 질문: {normalized}",),
            {"normalized_question": normalized},
            _ms(started),
        )

        # 2. 질의 계획 수립
        yield _event("plan", StepStatus.RUNNING, (f"플래너: {self.planner.name}",))
        started = time.perf_counter()
        try:
            plan: Plan = self.planner.plan(normalized)
        except PlannerError as exc:
            yield from _fail(
                "plan", "plan", str(exc), started, "질의 계획을 세우지 못해 건너뜀"
            )
            return
        plan_detail = [f"플래너: {plan.planner_name}", f"Intent: {plan.intent.value}"]
        plan_detail += [f"신호: {signal}" for signal in plan.matched_signals]
        plan_detail += list(plan.notes)
        yield _event(
            "plan", StepStatus.DONE, tuple(plan_detail), plan.to_dict(), _ms(started)
        )

        # 3. 요청 계약 검증
        yield _event("contract", StepStatus.RUNNING)
        started = time.perf_counter()
        try:
            request = QueryRequest.from_dict(plan.request_payload)
        except QueryValidationError as exc:
            yield from _fail(
                "contract", "contract", str(exc), started, "요청 계약 검증 실패로 건너뜀"
            )
            return
        yield _event(
            "contract",
            StepStatus.DONE,
            (
                "Intent별 필수·허용 파라미터 검증 통과",
                f"파라미터: {_format_parameters(request.parameters)}",
            ),
            {"parameters": dict(request.parameters)},
            _ms(started),
        )

        # 4. Cypher 템플릿 선택
        yield _event("cypher", StepStatus.RUNNING)
        started = time.perf_counter()
        cypher = QUERY_SPECS[request.intent].cypher
        ensure_read_only(cypher)
        yield _event(
            "cypher",
            StepStatus.DONE,
            ("allowlist에 등록된 템플릿만 사용합니다.", "쓰기 키워드 차단 검사 통과"),
            {"cypher": cypher.strip()},
            _ms(started),
        )

        # 5. Neo4j 조회
        yield _event("neo4j", StepStatus.RUNNING)
        started = time.perf_counter()
        try:
            response = self.runner.execute(request)
        except Exception as exc:  # 드라이버 예외 종류가 다양해 메시지만 전달한다.
            yield from _fail(
                "neo4j",
                "neo4j",
                f"{type(exc).__name__}: {exc}",
                started,
                "조회 실패로 건너뜀",
            )
            return
        payload = response.to_dict()
        answerability = payload["answerability"]
        evidence = payload["evidence"]
        yield _event(
            "neo4j",
            StepStatus.DONE,
            (
                f"읽기 트랜잭션 완료 · 판정 {answerability}",
                f"근거 후보 {len(evidence)}건",
            ),
            {"answerability": answerability},
            _ms(started),
        )

        # 6. 근거 수집
        yield _event("evidence", StepStatus.RUNNING)
        started = time.perf_counter()
        if evidence:
            pages = sorted({item["excerpt_page"] for item in evidence})
            yield _event(
                "evidence",
                StepStatus.DONE,
                (
                    f"VERIFIED Evidence {len(evidence)}건",
                    "참조 발췌 페이지: " + ", ".join(f"p.{page}" for page in pages),
                ),
                {"evidence_count": len(evidence), "pages": pages},
                _ms(started),
            )
        else:
            yield _event(
                "evidence",
                StepStatus.SKIPPED,
                ("VERIFIED Evidence가 없어 근거 카드를 만들지 않습니다.",),
                elapsed_ms=_ms(started),
            )

        # 7. PDF 근거 위치 계산
        yield _event("pdf", StepStatus.RUNNING)
        started = time.perf_counter()
        source = pdf_evidence.inspect_pdf()
        evidence_pages = pdf_evidence.build_evidence_pages(evidence) if evidence else []
        if not evidence:
            yield _event(
                "pdf",
                StepStatus.SKIPPED,
                ("근거가 없어 PDF 위치를 계산하지 않습니다.",),
                elapsed_ms=_ms(started),
            )
        elif not source.available:
            yield _event(
                "pdf",
                StepStatus.SKIPPED,
                (
                    source.reason or "PDF를 사용할 수 없습니다.",
                    "페이지 번호와 원문 텍스트만 표시합니다.",
                ),
                {"pdf": source.to_dict()},
                _ms(started),
            )
        else:
            highlighted = sum(
                1
                for page in evidence_pages
                for item in page["evidence"]
                if item["highlight_found"]
            )
            detail = [
                f"참조 페이지 {len(evidence_pages)}개 렌더링 대상",
                f"강조 영역 탐색 성공 {highlighted}/{len(evidence)}건",
            ]
            if source.reason:
                detail.append(source.reason)
            yield _event(
                "pdf", StepStatus.DONE, tuple(detail), {"pdf": source.to_dict()}, _ms(started)
            )

        # 8. 답변 구성
        yield _event("compose", StepStatus.RUNNING)
        started = time.perf_counter()
        composed = answer_module.compose(payload)
        yield _event(
            "compose",
            StepStatus.DONE,
            (f"판정: {composed['answerability_label']}",),
            elapsed_ms=_ms(started),
        )

        yield {
            "type": "result",
            "question": normalized,
            "plan": plan.to_dict(),
            "answer": composed,
            "query_response": payload,
            "evidence_pages": evidence_pages,
            "pdf": source.to_dict(),
        }
