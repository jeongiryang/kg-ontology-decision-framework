"""Official chat composition root after the validated natural-language query layer."""

from __future__ import annotations

from typing import Protocol

from kg_builder.llm.client import LLMResponseError
from kg_builder.query.natural_language_service import NaturalLanguageResult

from .contracts import AnswerContractError, ChatResponse, ChatStatus
from .generator import EvidenceAnswerGenerator
from .renderer import CitationRenderer
from .validator import AnswerValidationError, AnswerValidator


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
RETRYABLE_GENERATION_CODES = frozenset(
    {
        "ANSWER_DRAFT_INVALID",
        "LLM_INVALID_JSON",
        "LLM_JSON_OBJECT_REQUIRED",
        "LLM_RESPONSE_MISSING",
    }
)


class CurriculumChatService:
    """Compose only ResultValidator-approved rows into a grounded Korean answer."""

    def __init__(
        self,
        query_service: QueryService,
        answer_generator: EvidenceAnswerGenerator,
        *,
        validator: AnswerValidator | None = None,
        renderer: CitationRenderer | None = None,
        answer_retries: int = 1,
    ):
        if answer_retries not in {0, 1}:
            raise ValueError("answer_retries must be 0 or 1")
        self.query_service = query_service
        self.answer_generator = answer_generator
        self.validator = validator or AnswerValidator()
        self.renderer = renderer or CitationRenderer()
        self.answer_retries = answer_retries

    def ask(self, question: str) -> ChatResponse:
        query_result = self.query_service.ask(question)
        if query_result.status != ChatStatus.ANSWERABLE.value:
            return self._deterministic(query_result)

        previous_error: str | None = None
        for attempt in range(self.answer_retries + 1):
            try:
                draft = self.answer_generator.generate(
                    question,
                    query_result.rows,
                    previous_error_code=previous_error,
                )
                validated = self.validator.validate(
                    draft, query_result.rows, question=question
                )
                return self.renderer.render(
                    query_result.request_id, validated, query_result.rows
                )
            except (AnswerContractError, AnswerValidationError) as exc:
                previous_error = exc.code
                if attempt < self.answer_retries:
                    continue
                break
            except LLMResponseError as exc:
                previous_error = exc.code
                if exc.code in RETRYABLE_GENERATION_CODES and attempt < self.answer_retries:
                    continue
                break
            except Exception:
                break
        return ChatResponse(
            request_id=query_result.request_id,
            status=ChatStatus.SAFE_FAILURE,
            answer_text=NON_ANSWERABLE_MESSAGES[ChatStatus.SAFE_FAILURE],
            error_code="ANSWER_VALIDATION_FAILED",
        )

    @staticmethod
    def _deterministic(result: NaturalLanguageResult) -> ChatResponse:
        try:
            status = ChatStatus(result.status)
        except ValueError:
            status = ChatStatus.SAFE_FAILURE
        if status is ChatStatus.ANSWERABLE:
            raise AssertionError("ANSWERABLE must use the grounded answer path")
        clarification = (
            result.message if status is ChatStatus.CLARIFICATION_REQUIRED else None
        )
        error_code = "QUERY_SAFE_FAILURE" if status is ChatStatus.SAFE_FAILURE else None
        return ChatResponse(
            request_id=result.request_id,
            status=status,
            answer_text=NON_ANSWERABLE_MESSAGES[status],
            clarification=clarification,
            error_code=error_code,
        )
