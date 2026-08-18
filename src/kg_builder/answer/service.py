"""Official deterministic chat composition root after verified query validation."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Mapping, Protocol

from kg_builder.query.natural_language_service import NaturalLanguageResult
from kg_builder.query.progress import (
    ProgressCallback,
    ProgressPhase,
    ProgressState,
    emit_progress,
)

from .claim_builder import ClaimBuilder
from .claim_validator import ClaimValidator
from .contracts import (
    QUERY_STAGE_ERROR_CODES,
    ChatErrorCode,
    ChatResponse,
    ChatStatus,
    ClarificationOption,
    GroundingError,
    clarification_message,
)
from .korean_renderer import KoreanAnswerRenderer
from .renderer import CitationRenderer


class QueryService(Protocol):
    def ask(
        self,
        question: str,
        *,
        resolved: Mapping[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
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
        self,
        question: str,
        *,
        resolved: Mapping[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ChatResponse:
        total_started = perf_counter()
        query_result = self.query_service.ask(
            question,
            resolved=resolved,
            progress_callback=progress_callback,
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
                citation_count=0,
                deterministic_renderer=True,
                final_answer_llm_calls=0,
            )
            emit_progress(
                progress_callback,
                ProgressPhase.COMPLETED,
                ProgressState.COMPLETED,
                (perf_counter() - total_started) * 1000,
                final_status=response.status.value,
                citation_count=0,
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
                claim_types=sorted(
                    {claim.claim_type.value for claim in validated.claims}
                ),
                aggregate=any(
                    claim.claim_type.value in {"AGGREGATE", "AGGREGATE_LIST"}
                    for claim in validated.claims
                ),
                citation_target_count=len(validated.citation_sources),
                validated_rows=query_result.rows,
                approved_provenance=tuple(
                    (link.fact_id, link.evidence_id)
                    for link in validated.provenance
                ),
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
                    citation_count=len(response.citations),
                    deterministic_renderer=True,
                    final_answer_llm_calls=0,
                )
        emit_progress(
            progress_callback,
            ProgressPhase.COMPLETED,
            ProgressState.COMPLETED,
            (perf_counter() - total_started) * 1000,
            final_status=response.status.value,
            citation_count=len(response.citations),
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
            options = tuple(
                ClarificationOption(
                    choice.filter_name, choice.value, choice.label, choice.detail
                )
                for choice in getattr(result, "options", ())
            )
            return ChatResponse.clarification_required(
                result.request_id,
                clarification_message(result.missing, options),
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
