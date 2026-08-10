"""Official deterministic chat composition root after verified query validation."""

from __future__ import annotations

from typing import Protocol

from kg_builder.query.natural_language_service import NaturalLanguageResult

from .claim_builder import ClaimBuilder
from .claim_validator import ClaimValidator
from .contracts import ChatErrorCode, ChatResponse, ChatStatus, GroundingError
from .korean_renderer import KoreanAnswerRenderer
from .renderer import CitationRenderer


class QueryService(Protocol):
    def ask(self, question: str) -> NaturalLanguageResult: ...


class CurriculumChatService:
    """Render only ResultValidator-approved rows; no final answer LLM is used."""

    def __init__(
        self,
        query_service: QueryService,
    ):
        self.query_service = query_service
        self._claim_builder = ClaimBuilder()
        self._claim_validator = ClaimValidator()
        self._answer_renderer = KoreanAnswerRenderer()
        self._citation_renderer = CitationRenderer()

    def ask(self, question: str) -> ChatResponse:
        query_result = self.query_service.ask(question)
        if query_result.status != ChatStatus.ANSWERABLE.value:
            return self._deterministic(query_result)
        try:
            claims = self._claim_builder.build(query_result.rows, query_result.query_plan)
            validated = self._claim_validator.validate(
                claims, query_result.rows, query_result.query_plan
            )
            answer = self._answer_renderer.render(validated)
            return self._citation_renderer.render(query_result.request_id, answer)
        except GroundingError as exc:
            code = (
                ChatErrorCode.ANSWER_RENDERING_UNSUPPORTED
                if exc.code in {"ANSWER_RENDERING_UNSUPPORTED", "ANSWER_CLAIM_TYPE_UNSUPPORTED"}
                else ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED
            )
        except Exception:
            code = ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED
        return ChatResponse.safe_failure(query_result.request_id, code)

    @staticmethod
    def _deterministic(result: NaturalLanguageResult) -> ChatResponse:
        try:
            status = ChatStatus(result.status)
        except ValueError:
            status = ChatStatus.SAFE_FAILURE
        if status is ChatStatus.ANSWERABLE:
            raise AssertionError("ANSWERABLE must use the deterministic Claim path")
        if status is ChatStatus.SAFE_FAILURE:
            return ChatResponse.safe_failure(
                result.request_id, ChatErrorCode.QUERY_SAFE_FAILURE
            )
        if status is ChatStatus.CLARIFICATION_REQUIRED:
            clarification = result.message or "학년도, 학과 또는 학수번호를 추가로 알려 주세요."
            return ChatResponse.clarification_required(
                result.request_id, clarification
            )
        factories = {
            ChatStatus.OUT_OF_SCOPE: ChatResponse.out_of_scope,
            ChatStatus.UNSUPPORTED: ChatResponse.unsupported,
            ChatStatus.UNRESOLVED: ChatResponse.unresolved,
            ChatStatus.NOT_FOUND: ChatResponse.not_found,
        }
        return factories[status](result.request_id)
