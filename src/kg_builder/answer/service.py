"""Official deterministic chat composition root after verified query validation."""

from __future__ import annotations

from time import perf_counter
from typing import Protocol

from kg_builder.query.natural_language_service import NaturalLanguageResult
from kg_builder.query.progress import (
    ProgressCallback,
    ProgressPhase,
    ProgressState,
    emit_progress,
)

from .claim_builder import ClaimBuilder
from .claim_validator import ClaimValidator
from .contracts import ChatErrorCode, ChatResponse, ChatStatus, GroundingError
from .korean_renderer import KoreanAnswerRenderer
from .renderer import CitationRenderer


class QueryService(Protocol):
    def ask(
        self, question: str, progress_callback: ProgressCallback | None = None
    ) -> NaturalLanguageResult: ...


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

    def ask(
        self, question: str, progress_callback: ProgressCallback | None = None
    ) -> ChatResponse:
        total_started = perf_counter()
        query_result = (
            self.query_service.ask(question)
            if progress_callback is None
            else self.query_service.ask(question, progress_callback)
        )
        if query_result.status != ChatStatus.ANSWERABLE.value:
            started = perf_counter()
            emit_progress(
                progress_callback,
                ProgressPhase.ANSWER_RENDERING,
                ProgressState.STARTED,
                0,
            )
            response = self._deterministic(query_result)
            emit_progress(
                progress_callback,
                ProgressPhase.ANSWER_RENDERING,
                ProgressState.COMPLETED,
                (perf_counter() - started) * 1000,
            )
            emit_progress(
                progress_callback,
                ProgressPhase.COMPLETED,
                ProgressState.COMPLETED,
                (perf_counter() - total_started) * 1000,
            )
            return response
        claim_started = perf_counter()
        emit_progress(
            progress_callback,
            ProgressPhase.CLAIM_BUILDING,
            ProgressState.STARTED,
            0,
        )
        try:
            claims = self._claim_builder.build(query_result.rows, query_result.query_plan)
            validated = self._claim_validator.validate(
                claims, query_result.rows, query_result.query_plan
            )
        except GroundingError as exc:
            emit_progress(
                progress_callback,
                ProgressPhase.CLAIM_BUILDING,
                ProgressState.FAILED,
                (perf_counter() - claim_started) * 1000,
                error_code=exc.code,
            )
            response = ChatResponse.safe_failure(
                query_result.request_id, self._grounding_error_code(exc)
            )
        except Exception:
            emit_progress(
                progress_callback,
                ProgressPhase.CLAIM_BUILDING,
                ProgressState.FAILED,
                (perf_counter() - claim_started) * 1000,
                error_code=ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED.value,
            )
            response = ChatResponse.safe_failure(
                query_result.request_id,
                ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED,
            )
        else:
            emit_progress(
                progress_callback,
                ProgressPhase.CLAIM_BUILDING,
                ProgressState.COMPLETED,
                (perf_counter() - claim_started) * 1000,
                claim_count=len(validated.claims),
            )
            answer_started = perf_counter()
            emit_progress(
                progress_callback,
                ProgressPhase.ANSWER_RENDERING,
                ProgressState.STARTED,
                0,
            )
            try:
                answer = self._answer_renderer.render(validated)
                response = self._citation_renderer.render(query_result.request_id, answer)
            except GroundingError as exc:
                emit_progress(
                    progress_callback,
                    ProgressPhase.ANSWER_RENDERING,
                    ProgressState.FAILED,
                    (perf_counter() - answer_started) * 1000,
                    error_code=exc.code,
                )
                response = ChatResponse.safe_failure(
                    query_result.request_id, self._grounding_error_code(exc)
                )
            except Exception:
                emit_progress(
                    progress_callback,
                    ProgressPhase.ANSWER_RENDERING,
                    ProgressState.FAILED,
                    (perf_counter() - answer_started) * 1000,
                    error_code=ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED.value,
                )
                response = ChatResponse.safe_failure(
                    query_result.request_id,
                    ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED,
                )
            else:
                emit_progress(
                    progress_callback,
                    ProgressPhase.ANSWER_RENDERING,
                    ProgressState.COMPLETED,
                    (perf_counter() - answer_started) * 1000,
                    evidence_count=len(response.citations),
                )
        emit_progress(
            progress_callback,
            ProgressPhase.COMPLETED,
            ProgressState.COMPLETED,
            (perf_counter() - total_started) * 1000,
        )
        return response

    @staticmethod
    def _grounding_error_code(exc: GroundingError) -> ChatErrorCode:
        return (
            ChatErrorCode.ANSWER_RENDERING_UNSUPPORTED
            if exc.code in {
                "ANSWER_RENDERING_UNSUPPORTED",
                "ANSWER_CLAIM_TYPE_UNSUPPORTED",
            }
            else ChatErrorCode.ANSWER_CLAIM_VALIDATION_FAILED
        )

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
        if status is ChatStatus.UNSUPPORTED:
            return ChatResponse.unsupported(
                result.request_id, result.unsupported_reason
            )
        factories = {
            ChatStatus.OUT_OF_SCOPE: ChatResponse.out_of_scope,
            ChatStatus.UNRESOLVED: ChatResponse.unresolved,
            ChatStatus.NOT_FOUND: ChatResponse.not_found,
        }
        return factories[status](result.request_id)
