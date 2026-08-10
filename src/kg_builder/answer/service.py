"""Official deterministic chat composition root after verified query validation."""

from __future__ import annotations

from typing import Protocol

from kg_builder.query.natural_language_service import NaturalLanguageResult

from .claim_builder import ClaimBuilder
from .claim_validator import ClaimValidator
from .contracts import ChatResponse, ChatStatus, GroundingError
from .korean_renderer import KoreanAnswerRenderer
from .renderer import CitationRenderer


class QueryService(Protocol):
    def ask(self, question: str) -> NaturalLanguageResult: ...


NON_ANSWERABLE_MESSAGES = {
    ChatStatus.CLARIFICATION_REQUIRED: "질문을 정확히 확인하려면 추가 정보가 필요합니다.",
    ChatStatus.OUT_OF_SCOPE: "현재 데이터 범위에서는 답변할 수 없습니다.",
    ChatStatus.UNSUPPORTED: "현재 지원하지 않는 질문 유형입니다.",
    ChatStatus.UNRESOLVED: "원문 확인이나 정책 결정이 필요한 항목이므로 확정해서 답변할 수 없습니다.",
    ChatStatus.NOT_FOUND: "현재 검증된 데이터에서 일치하는 결과를 찾지 못했습니다.",
    ChatStatus.SAFE_FAILURE: "안전한 답변을 생성하지 못했습니다.",
}


class CurriculumChatService:
    """Render only ResultValidator-approved rows; no final answer LLM is used."""

    def __init__(
        self,
        query_service: QueryService,
        *,
        claim_builder: ClaimBuilder | None = None,
        claim_validator: ClaimValidator | None = None,
        answer_renderer: KoreanAnswerRenderer | None = None,
        citation_renderer: CitationRenderer | None = None,
    ):
        self.query_service = query_service
        self.claim_builder = claim_builder or ClaimBuilder()
        self.claim_validator = claim_validator or ClaimValidator()
        self.answer_renderer = answer_renderer or KoreanAnswerRenderer()
        self.citation_renderer = citation_renderer or CitationRenderer()

    def ask(self, question: str) -> ChatResponse:
        query_result = self.query_service.ask(question)
        if query_result.status != ChatStatus.ANSWERABLE.value:
            return self._deterministic(query_result)
        try:
            claims = self.claim_builder.build(query_result.rows, query_result.query_plan)
            claims = self.claim_validator.validate(claims, query_result.rows)
            answer = self.answer_renderer.render(claims)
            return self.citation_renderer.render(
                query_result.request_id, answer, query_result.rows
            )
        except GroundingError as exc:
            code = (
                "ANSWER_RENDERING_UNSUPPORTED"
                if exc.code in {"ANSWER_RENDERING_UNSUPPORTED", "ANSWER_CLAIM_TYPE_UNSUPPORTED"}
                else "ANSWER_CLAIM_VALIDATION_FAILED"
            )
        except Exception:
            code = "ANSWER_CLAIM_VALIDATION_FAILED"
        return ChatResponse(
            request_id=query_result.request_id,
            status=ChatStatus.SAFE_FAILURE,
            answer_text=NON_ANSWERABLE_MESSAGES[ChatStatus.SAFE_FAILURE],
            error_code=code,
        )

    @staticmethod
    def _deterministic(result: NaturalLanguageResult) -> ChatResponse:
        try:
            status = ChatStatus(result.status)
        except ValueError:
            status = ChatStatus.SAFE_FAILURE
        if status is ChatStatus.ANSWERABLE:
            raise AssertionError("ANSWERABLE must use the deterministic Claim path")
        clarification = result.message if status is ChatStatus.CLARIFICATION_REQUIRED else None
        if status is ChatStatus.CLARIFICATION_REQUIRED and not clarification:
            clarification = "학년도, 학과 또는 학수번호를 추가로 알려 주세요."
        error_code = "QUERY_SAFE_FAILURE" if status is ChatStatus.SAFE_FAILURE else None
        return ChatResponse(
            request_id=result.request_id,
            status=status,
            answer_text=NON_ANSWERABLE_MESSAGES[status],
            clarification=clarification,
            error_code=error_code,
        )
