"""Presentation-only adapter for approved ``ChatResponse`` values.

The adapter never creates a response or changes its answer/citations.  It only derives
page groups and safe display metadata for the Starlette UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kg_builder.answer.contracts import ChatResponse, ChatStatus

from . import pdf_evidence


CHAT_RESPONSE_FIELDS = frozenset(
    {
        "request_id",
        "status",
        "answer_text",
        "citations",
        "used_fact_ids",
        "used_evidence_ids",
        "clarification",
        "error_code",
    }
)

STATUS_LABELS = {
    ChatStatus.ANSWERABLE.value: "검증된 답변",
    ChatStatus.CLARIFICATION_REQUIRED.value: "추가 정보 필요",
    ChatStatus.OUT_OF_SCOPE.value: "지원 범위 밖",
    ChatStatus.UNSUPPORTED.value: "지원하지 않는 질문",
    ChatStatus.UNRESOLVED.value: "정책 확인 필요",
    ChatStatus.NOT_FOUND.value: "검증 결과 없음",
    ChatStatus.SAFE_FAILURE.value: "안전 처리 실패",
}

SCOPE_NOTICE = (
    "현재 지원 범위는 2026학년도 공통 교양과 컴퓨터공학과 교육과정입니다."
)


class ChatAdapterError(ValueError):
    """Raised when the backend/UI wire contract has drifted."""


@dataclass(frozen=True, slots=True)
class ChatResponseAdapter:
    """Convert an approved response into the existing SSE presentation envelope."""

    debug: bool = False

    def adapt(self, response: ChatResponse) -> dict[str, Any]:
        if not isinstance(response, ChatResponse):
            raise ChatAdapterError("the UI accepts only an approved ChatResponse")
        wire = response.to_dict()
        if frozenset(wire) != CHAT_RESPONSE_FIELDS:
            raise ChatAdapterError("ChatResponse wire contract has changed")

        citations = _deduplicate_citations(wire["citations"])
        pages = pdf_evidence.build_evidence_pages(citations)
        # IDs are necessary in ChatResponse's wire/audit contract, but page cards do
        # not need them.  The general-user presentation receives only source content.
        for page in pages:
            for evidence in page["evidence"]:
                evidence.pop("evidence_id", None)
                evidence.pop("fact_ids", None)

        status = wire["status"]
        presentation: dict[str, Any] = {
            "status_label": STATUS_LABELS.get(status, "안전 처리 실패"),
            "scope_notice": (
                SCOPE_NOTICE
                if status in {
                    ChatStatus.OUT_OF_SCOPE.value,
                    ChatStatus.UNSUPPORTED.value,
                }
                else None
            ),
            "evidence_pages": pages,
            "pdf": pdf_evidence.inspect_pdf().to_public_dict(),
            "debug": None,
        }
        if self.debug:
            presentation["debug"] = {
                "request_id": wire["request_id"],
                "error_code": wire["error_code"],
            }
        return {"type": "result", "response": wire, "presentation": presentation}


def _deduplicate_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for citation in citations:
        evidence_id = citation.get("evidence_id")
        if isinstance(evidence_id, str) and evidence_id not in unique:
            unique[evidence_id] = citation
    return list(unique.values())
