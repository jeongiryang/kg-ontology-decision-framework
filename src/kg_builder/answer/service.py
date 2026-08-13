"""Official deterministic chat composition root after verified query validation."""

from __future__ import annotations

from typing import Protocol

from kg_builder.query.natural_language_service import NaturalLanguageResult

from .claim_builder import ClaimBuilder
from .claim_validator import ClaimValidator
from .contracts import (
    QUERY_STAGE_ERROR_CODES,
    ChatErrorCode,
    ChatResponse,
    ChatStatus,
    GroundingError,
    broadened_notice,
    clarification_message,
)
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
            answer = self._answer_renderer.render(
                validated,
                notice=broadened_notice(getattr(query_result, "broadened", None)),
            )
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
            # 어느 관문에서 멈췄는지 코드로 남긴다. 사용자 문구도 단계에 맞게 달라지지만
            # 어느 경우에도 조회하지 못한 내용을 지어내지 않는다.
            return ChatResponse.safe_failure(
                result.request_id,
                QUERY_STAGE_ERROR_CODES.get(
                    result.error_stage or "", ChatErrorCode.QUERY_SAFE_FAILURE
                ),
            )
        if status is ChatStatus.CLARIFICATION_REQUIRED:
            # 계획 모델이 쓴 문장을 그대로 내보내지 않는다. 무엇이 부족한지에 대한
            # 통제 코드만 받아 한국어 안내를 여기서 만든다.
            return ChatResponse.clarification_required(
                result.request_id, clarification_message(result.missing)
            )
        factories = {
            ChatStatus.OUT_OF_SCOPE: ChatResponse.out_of_scope,
            ChatStatus.UNSUPPORTED: ChatResponse.unsupported,
            ChatStatus.UNRESOLVED: ChatResponse.unresolved,
            ChatStatus.NOT_FOUND: ChatResponse.not_found,
        }
        return factories[status](result.request_id)
