"""질문 → 답변 파이프라인을 단계 이벤트로 흘려보낸다.

각 단계는 실제로 수행한 일만 보고한다. 수행하지 않은 단계는 `skipped`로,
실패한 단계는 `failed`로 표시하고 이유를 남긴다. 화면의 진행 표시가 실제
처리 상태와 어긋나지 않게 하는 것이 이 모듈의 계약이다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterator, Mapping, Protocol

from kg_builder.cypher_queries import QUERY_SPECS, ensure_read_only
from kg_builder.query_contracts import (
    Answerability,
    QueryRequest,
    QueryValidationError,
)

from . import answer as answer_module
from . import pdf_evidence
from .planner import Plan, PlannerError, RuleBasedPlanner


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


class QueryRunner(Protocol):
    """질의 실행기. 실제 구현은 `kg_builder.query_service.QueryService`다."""

    def execute(self, request: QueryRequest) -> Any: ...


@dataclass(slots=True)
class ChatPipeline:
    runner: QueryRunner
    planner: Any = field(default_factory=RuleBasedPlanner)

    def run(self, question: str) -> Iterator[dict[str, Any]]:
        """단계 이벤트를 순서대로 내보내고 마지막에 결과 또는 오류를 낸다."""
        total = len(STEP_SEQUENCE)
        labels = dict(STEP_SEQUENCE)
        order = [step_id for step_id, _ in STEP_SEQUENCE]

        def event(
            step_id: str,
            status: StepStatus,
            detail: tuple[str, ...] = (),
            data: Mapping[str, Any] | None = None,
            elapsed_ms: int | None = None,
        ) -> dict[str, Any]:
            return StepEvent(
                step_id=step_id,
                label=labels[step_id],
                status=status,
                index=order.index(step_id) + 1,
                total=total,
                detail=detail,
                data=data,
                elapsed_ms=elapsed_ms,
            ).to_dict()

        def remaining_skipped(after: str, reason: str) -> Iterator[dict[str, Any]]:
            for step_id in order[order.index(after) + 1 :]:
                yield event(step_id, StepStatus.SKIPPED, (reason,))

        # 1. 질문 정규화 + 2. 질의 계획 수립
        yield event("normalize", StepStatus.RUNNING)
        started = time.perf_counter()
        try:
            plan: Plan = self.planner.plan(question)
        except PlannerError as exc:
            yield event("normalize", StepStatus.FAILED, (str(exc),), elapsed_ms=_ms(started))
            yield from remaining_skipped("normalize", "질의 계획을 세우지 못해 건너뜀")
            yield {"type": "error", "stage": "plan", "message": str(exc)}
            return
        yield event(
            "normalize",
            StepStatus.DONE,
            (f"정규화된 질문: {plan.normalized_question}",),
            {"normalized_question": plan.normalized_question},
            _ms(started),
        )

        yield event("plan", StepStatus.RUNNING, (f"플래너: {plan.planner_name}",))
        plan_detail = [f"플래너: {plan.planner_name}", f"Intent: {plan.intent.value}"]
        plan_detail += [f"신호: {signal}" for signal in plan.matched_signals]
        plan_detail += list(plan.notes)
        yield event("plan", StepStatus.DONE, tuple(plan_detail), plan.to_dict())

        # 3. 요청 계약 검증
        yield event("contract", StepStatus.RUNNING)
        started = time.perf_counter()
        try:
            request = QueryRequest.from_dict(plan.request_payload)
        except QueryValidationError as exc:
            yield event("contract", StepStatus.FAILED, (str(exc),), elapsed_ms=_ms(started))
            yield from remaining_skipped("contract", "요청 계약 검증 실패로 건너뜀")
            yield {"type": "error", "stage": "contract", "message": str(exc)}
            return
        yield event(
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
        yield event("cypher", StepStatus.RUNNING)
        started = time.perf_counter()
        cypher = QUERY_SPECS[request.intent].cypher
        ensure_read_only(cypher)
        yield event(
            "cypher",
            StepStatus.DONE,
            (
                "allowlist에 등록된 템플릿만 사용합니다.",
                "쓰기 키워드 차단 검사 통과",
            ),
            {"cypher": cypher.strip()},
            _ms(started),
        )

        # 5. Neo4j 조회
        yield event("neo4j", StepStatus.RUNNING)
        started = time.perf_counter()
        try:
            response = self.runner.execute(request)
        except Exception as exc:  # 드라이버 예외 종류가 다양해 메시지만 전달한다.
            message = f"{type(exc).__name__}: {exc}"
            yield event("neo4j", StepStatus.FAILED, (message,), elapsed_ms=_ms(started))
            yield from remaining_skipped("neo4j", "조회 실패로 건너뜀")
            yield {"type": "error", "stage": "neo4j", "message": message}
            return
        payload = response.to_dict()
        answerability = Answerability(payload["answerability"])
        yield event(
            "neo4j",
            StepStatus.DONE,
            (
                f"읽기 트랜잭션 완료 · 판정 {answerability.value}",
                f"근거 후보 {len(payload['evidence'])}건",
            ),
            {"answerability": answerability.value},
            _ms(started),
        )

        # 6. 근거 수집
        yield event("evidence", StepStatus.RUNNING)
        started = time.perf_counter()
        evidence = payload["evidence"]
        if not evidence:
            yield event(
                "evidence",
                StepStatus.SKIPPED,
                ("VERIFIED Evidence가 없어 근거 카드를 만들지 않습니다.",),
                elapsed_ms=_ms(started),
            )
        else:
            pages = sorted({item["excerpt_page"] for item in evidence})
            yield event(
                "evidence",
                StepStatus.DONE,
                (
                    f"VERIFIED Evidence {len(evidence)}건",
                    "참조 발췌 페이지: " + ", ".join(f"p.{page}" for page in pages),
                ),
                {"evidence_count": len(evidence), "pages": pages},
                _ms(started),
            )

        # 7. PDF 근거 위치 계산
        yield event("pdf", StepStatus.RUNNING)
        started = time.perf_counter()
        source = pdf_evidence.inspect_pdf()
        if not evidence:
            evidence_pages: list[dict[str, Any]] = []
            yield event(
                "pdf",
                StepStatus.SKIPPED,
                ("근거가 없어 PDF 위치를 계산하지 않습니다.",),
                elapsed_ms=_ms(started),
            )
        elif not source.available:
            evidence_pages = pdf_evidence.build_evidence_pages(evidence)
            yield event(
                "pdf",
                StepStatus.SKIPPED,
                (source.reason or "PDF를 사용할 수 없습니다.", "페이지 번호와 원문 텍스트만 표시합니다."),
                {"pdf": source.to_dict()},
                _ms(started),
            )
        else:
            evidence_pages = pdf_evidence.build_evidence_pages(evidence)
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
            yield event("pdf", StepStatus.DONE, tuple(detail), {"pdf": source.to_dict()}, _ms(started))

        # 8. 답변 구성
        yield event("compose", StepStatus.RUNNING)
        started = time.perf_counter()
        composed = answer_module.compose(payload)
        yield event(
            "compose",
            StepStatus.DONE,
            (f"판정: {composed['answerability_label']}",),
            elapsed_ms=_ms(started),
        )

        yield {
            "type": "result",
            "question": plan.normalized_question,
            "plan": plan.to_dict(),
            "answer": composed,
            "query_response": payload,
            "evidence_pages": evidence_pages,
            "pdf": source.to_dict(),
        }


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _format_parameters(parameters: Mapping[str, Any]) -> str:
    return ", ".join(f"{key}={value!r}" for key, value in sorted(parameters.items()))
