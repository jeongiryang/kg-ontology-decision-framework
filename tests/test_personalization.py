"""User-assertion and five-state decision contracts without external services."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from kg_builder.answer.contracts import ChatResponse
from kg_builder.answer.personalized_service import PersonalizedCurriculumChatService
from kg_builder.personalization import (
    OutcomeStatus,
    ProfileExtractor,
    ProfileValidationError,
    UserProfile,
)
from kg_builder.query.course_names import CourseNameResolver


ROOT = Path(__file__).parents[1]
BUNDLE = ROOT / "data/verified/2026/2026_curriculum_kg_data.json"


class _ResponseService:
    def __init__(self, response: ChatResponse):
        self.response = response
        self.calls = 0
        self.questions: list[str] = []

    def ask(
        self,
        question: str,
        *,
        resolved: dict[str, Any] | None = None,
        progress_callback=None,
    ) -> ChatResponse:
        del resolved, progress_callback
        self.calls += 1
        self.questions.append(question)
        return self.response


class UserProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.resolver = CourseNameResolver.from_bundle(
            json.loads(BUNDLE.read_text(encoding="utf-8"))
        )
        cls.extractor = ProfileExtractor(cls.resolver)

    def test_profile_round_trip_is_versioned_and_user_assertions_are_separate(self):
        extracted = self.extractor.extract(
            "2026학번 컴퓨터공학과야. 자료구조와 이산수학을 들었어. "
            "교양 30학점, 전공 42학점이고 토익은 700점이야.",
            UserProfile(),
        )
        restored = UserProfile.from_payload(extracted.profile.to_dict())
        self.assertEqual(restored, extracted.profile)
        self.assertEqual(restored.admission_year, 2026)
        self.assertEqual(restored.department_id, "CSE")
        self.assertEqual(restored.credits_by_category["general"], 30)
        self.assertEqual(restored.credits_by_category["major"], 42)
        self.assertEqual(len(restored.completed_courses), 2)
        self.assertTrue(
            all(item.provenance == "USER_ASSERTION" for item in restored.completed_courses)
        )
        self.assertEqual(restored.english_credentials[0].provenance, "USER_ASSERTION")

    def test_latest_explicit_credit_correction_wins(self):
        current = self.extractor.extract("전공 42학점이야.", UserProfile()).profile
        corrected = self.extractor.extract(
            "방금 말한 전공학점은 42가 아니라 45학점이야.", current
        )
        self.assertEqual(corrected.profile.credits_by_category["major"], 45)
        self.assertEqual(corrected.conflicts, ())

    def test_credit_correction_allows_category_particle_without_repeated_unit(self):
        corrected = self.extractor.extract(
            "전공은 42가 아니라 45학점이라고 정정할게. 얼마나 부족하지?",
            UserProfile(),
        )
        self.assertEqual(corrected.profile.credits_by_category["major"], 45)
        self.assertEqual(corrected.conflicts, ())

    def test_toeic_speaking_is_not_also_extracted_as_toeic(self):
        extracted = self.extractor.extract(
            "TOEIC Speaking 130점과 일반 TOEIC 700점이 있어.", UserProfile()
        )
        values = {item.test: item.value for item in extracted.profile.english_credentials}
        self.assertEqual(values, {"TOEIC": 700, "TOEIC_SPEAKING": 130})

    def test_conflicting_values_in_one_message_require_resolution(self):
        extracted = self.extractor.extract(
            "교양 30학점이고 교양 32학점이야.", UserProfile()
        )
        self.assertEqual(extracted.conflicts, ("credits.general",))

    def test_general_total_is_not_misclassified_as_all_credits(self):
        extracted = self.extractor.extract("교양 총학점이 31학점이야.", UserProfile())
        self.assertEqual(extracted.profile.credits_by_category, {"general": 31.0})

    def test_area_credits_do_not_conflict_with_explicit_general_total(self):
        extracted = self.extractor.extract(
            "기초교양 9학점과 균형교양 12학점을 채웠고 교양 총학점이 31학점이야.",
            UserProfile(),
        )
        self.assertEqual(extracted.conflicts, ())
        self.assertEqual(extracted.profile.credits_by_category, {"general": 31.0})

    def test_compact_category_credit_list_uses_surrounding_unit_context(self):
        extracted = self.extractor.extract(
            "전공 51, 교양 29, 일선 14인데 영역별로 얼마나 부족해?",
            UserProfile(),
        )
        self.assertEqual(
            extracted.profile.credits_by_category,
            {"major": 51.0, "general": 29.0, "free_elective": 14.0},
        )

    def test_negated_course_is_not_recorded_as_completed(self):
        extracted = self.extractor.extract(
            "운영체제는 듣지 않고 데이터통신을 이수했어.", UserProfile()
        )
        self.assertEqual(
            tuple(item.name_ko for item in extracted.profile.completed_courses),
            ("데이타통신",),
        )

    def test_generic_programming_word_is_not_a_course_alias(self):
        matches = self.resolver.find_mentions("수학과 프로그래밍 실력이 부족해")
        self.assertEqual(matches, ())

    def test_unknown_schema_and_invalid_ranges_fail_closed(self):
        with self.assertRaises(ProfileValidationError):
            UserProfile.from_payload({"version": 99})
        with self.assertRaises(ProfileValidationError):
            UserProfile.from_payload({"version": 1, "credits": {"major": -1}})
        with self.assertRaises(ProfileValidationError):
            UserProfile.from_payload({"version": 1, "unexpected": "value"})


class PersonalizedOutcomeTests(unittest.TestCase):
    def test_personal_calculation_asks_only_for_missing_user_information(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("내가 지금까지 들은 과목과 학점으로 졸업할 수 있어?")
        self.assertEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertIn("completed_courses", result.outcome.required_user_fields)
        self.assertIn("credits", result.outcome.required_user_fields)
        self.assertEqual(base.calls, 0)

    def test_explicit_other_curriculum_is_out_of_scope_without_query(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("2025학년도 컴퓨터공학과 기준을 알려줘")
        self.assertEqual(result.outcome.status, OutcomeStatus.OUT_OF_SCOPE)
        self.assertEqual(base.calls, 0)

    def test_missing_verified_fact_is_not_relabelled_as_missing_user_data(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("휴학 후 교육과정 적용 규정을 알려줘")
        self.assertEqual(result.outcome.status, OutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.outcome.required_user_fields, ())
        self.assertEqual(base.calls, 1)

    def test_career_question_uses_advisory_status_without_inventing_facts(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("AI 엔지니어가 되고 싶은데 어떤 과목을 추천해줘?")
        self.assertEqual(result.outcome.status, OutcomeStatus.ADVISORY)
        self.assertIn("단정해 추천하지 않습니다", result.outcome.message)
        self.assertEqual(base.calls, 1)

    def test_live_registration_question_stops_before_the_kg_query(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("다음 학기 알고리즘 잔여석이 몇 개인지 알려줘")
        self.assertEqual(result.outcome.status, OutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertIn("실시간", result.outcome.message)
        self.assertEqual(base.calls, 0)

    def test_nonacademic_recommendation_is_out_of_scope(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("오늘 날씨에 맞는 식당을 추천해줘")
        self.assertEqual(result.outcome.status, OutcomeStatus.OUT_OF_SCOPE)
        self.assertEqual(base.calls, 0)

    def test_curriculum_applicability_requests_only_scope_information(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("내 학번에 적용되는 교육과정을 알려면 뭘 말해야 해?")
        self.assertEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(
            result.outcome.required_user_fields,
            ("admission_year", "department_id"),
        )
        self.assertEqual(base.calls, 0)

    def test_named_course_recommendation_is_advisory_without_generic_domain_word(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask(
            "컴퓨터구조, 운영체제, 데이터통신을 어떤 순서로 추천할 수 있어?"
        )
        self.assertEqual(result.outcome.status, OutcomeStatus.ADVISORY)
        self.assertEqual(base.calls, 1)

    def test_existing_profile_value_is_not_requested_again(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        profile = UserProfile.from_payload(
            {
                "version": 1,
                "completed_courses": [
                    {"course_code": "CDA0008", "name_ko": "자료구조"}
                ],
                "credits": {"total": 60, "general": 30, "major": 30},
            }
        )
        result = service.ask("내가 지금까지 들은 과목과 학점으로 졸업할 수 있어?", profile=profile)
        self.assertNotEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(base.calls, 1)

    def test_follow_up_reuses_only_validated_public_course_codes(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        profile = UserProfile.from_payload(
            {
                "version": 1,
                "completed_courses": [
                    {"course_code": "CDA0008", "name_ko": "자료구조"},
                    {"course_code": "CDA0157", "name_ko": "이산수학"},
                ],
                "note": "server-model에 전달되면 안 되는 참고사항",
            }
        )
        service.ask("내가 들은 과목 중 전공선택은 뭐야?", profile=profile)
        self.assertEqual(base.calls, 1)
        self.assertIn("CDA0008", base.questions[0])
        self.assertIn("CDA0157", base.questions[0])
        self.assertNotIn("참고사항", base.questions[0])

    def test_reset_profile_does_not_reuse_previous_courses(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask(
            "내가 들은 과목 중 전공선택은 뭐야?", profile=UserProfile()
        )
        self.assertEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(result.outcome.required_user_fields, ("completed_courses",))
        self.assertEqual(base.calls, 0)


if __name__ == "__main__":
    unittest.main()
