"""실패·되묻기 상황에서 사용자 문구와 진단 정보를 어떻게 내보내는지 고정한다.

두 가지를 지킨다.

- 계획 모델이 쓴 자연어는 화면에 나가지 않는다. 사용자 문구는 통제된 코드에서 만든다.
- 어느 관문에서 멈췄는지는 오류 코드로 남아 운영자가 원인을 좁힐 수 있다.
"""

from __future__ import annotations

import unittest

from kg_builder.answer.contracts import (
    CLARIFICATION_FALLBACK,
    MISSING_SCOPE_LABELS,
    QUERY_STAGE_ERROR_CODES,
    ChatErrorCode,
    ChatStatus,
    clarification_message,
    safe_failure_message,
)
from kg_builder.answer.service import CurriculumChatService
from kg_builder.llm.models import MissingScope
from kg_builder.query.natural_language_service import NaturalLanguageResult


class StubQueryService:
    def __init__(self, result: NaturalLanguageResult):
        self.result = result

    def ask(
        self,
        question: str,
        *,
        resolved=None,
        progress_callback=None,
    ) -> NaturalLanguageResult:
        del question, resolved, progress_callback
        return self.result


def ask(result: NaturalLanguageResult):
    return CurriculumChatService(StubQueryService(result)).ask("무엇이든")


class ClarificationMessageTests(unittest.TestCase):
    def test_every_missing_scope_code_has_a_korean_label(self) -> None:
        for code in MissingScope:
            with self.subTest(code=code.value):
                self.assertIn(code.value, MISSING_SCOPE_LABELS)

    def test_codes_are_rendered_as_korean_guidance(self) -> None:
        text = clarification_message(["ACADEMIC_YEAR", "DEPARTMENT"])
        self.assertIn("학년도", text)
        self.assertIn("학과", text)

    def test_unknown_or_empty_codes_fall_back(self) -> None:
        self.assertEqual(clarification_message([]), CLARIFICATION_FALLBACK)
        self.assertEqual(clarification_message(None), CLARIFICATION_FALLBACK)
        self.assertEqual(clarification_message(["NOT_A_CODE"]), CLARIFICATION_FALLBACK)

    def test_model_prose_never_reaches_the_user(self) -> None:
        """계획 모델이 영어 문장을 보내도 화면 문구로 쓰이지 않아야 한다."""

        prose = "The question is unclear. Please provide more details."
        response = ask(
            NaturalLanguageResult(
                request_id="request-1",
                status=ChatStatus.CLARIFICATION_REQUIRED.value,
                model="stub",
                elapsed_seconds=0.0,
                message=prose,
                missing=("ACADEMIC_YEAR",),
            )
        )
        self.assertEqual(response.status, ChatStatus.CLARIFICATION_REQUIRED)
        self.assertIsNotNone(response.clarification)
        self.assertNotIn(prose, response.clarification)
        self.assertNotIn("The question", response.clarification)
        self.assertIn("학년도", response.clarification)

    def test_clarification_without_codes_still_speaks_korean(self) -> None:
        response = ask(
            NaturalLanguageResult(
                request_id="request-2",
                status=ChatStatus.CLARIFICATION_REQUIRED.value,
                model="stub",
                elapsed_seconds=0.0,
                message="Please clarify your question.",
            )
        )
        self.assertEqual(response.clarification, CLARIFICATION_FALLBACK)


class FailureStageReportingTests(unittest.TestCase):
    def test_each_stage_maps_to_its_own_error_code(self) -> None:
        for stage, expected in QUERY_STAGE_ERROR_CODES.items():
            with self.subTest(stage=stage):
                response = ask(
                    NaturalLanguageResult(
                        request_id="request-3",
                        status=ChatStatus.SAFE_FAILURE.value,
                        model="stub",
                        elapsed_seconds=0.0,
                        error_stage=stage,
                        error_code="SOME_INTERNAL_CODE",
                    )
                )
                self.assertEqual(response.status, ChatStatus.SAFE_FAILURE)
                self.assertEqual(response.error_code, expected)

    def test_unknown_stage_falls_back_to_the_generic_code(self) -> None:
        response = ask(
            NaturalLanguageResult(
                request_id="request-4",
                status=ChatStatus.SAFE_FAILURE.value,
                model="stub",
                elapsed_seconds=0.0,
                error_stage="SOMETHING_NEW",
            )
        )
        self.assertEqual(response.error_code, ChatErrorCode.QUERY_SAFE_FAILURE)

    def test_every_error_code_has_a_korean_message(self) -> None:
        for code in ChatErrorCode:
            with self.subTest(code=code.value):
                message = safe_failure_message(code)
                self.assertTrue(message.strip())
                self.assertFalse(message.isascii(), "사용자 문구는 한국어여야 한다")

    def test_internal_codes_are_not_shown_to_the_user(self) -> None:
        """내부 오류 코드 문자열이 사용자 문구에 섞이지 않아야 한다."""

        response = ask(
            NaturalLanguageResult(
                request_id="request-5",
                status=ChatStatus.SAFE_FAILURE.value,
                model="stub",
                elapsed_seconds=0.0,
                error_stage="RESULT_VALIDATION",
                error_code="RESULT_FIELD_NULL",
            )
        )
        self.assertNotIn("RESULT_FIELD_NULL", response.answer_text)
        self.assertIn("근거", response.answer_text)


if __name__ == "__main__":
    unittest.main()
