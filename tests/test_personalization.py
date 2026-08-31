"""User-assertion and five-state decision contracts without external services."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from kg_builder.answer.contracts import ChatResponse
from kg_builder.answer.personalized_service import (
    PersonalizedCurriculumChatService,
    _COURSE_OMISSION_NECESSITY,
)
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

    def test_credit_correction_replaces_existing_value_and_accepts_repeated_unit(self):
        current = self.extractor.extract(
            "교양 30학점, 전공 42학점, 일반선택 12학점이야.", UserProfile()
        ).profile
        corrected = self.extractor.extract(
            "전공은 42학점이 아니라 45학점이야. 다시 계산해줘.", current
        )
        self.assertEqual(
            corrected.profile.credits_by_category,
            {"general": 30.0, "major": 45.0, "free_elective": 12.0},
        )
        self.assertEqual(corrected.conflicts, ())

    def test_same_message_explicit_credit_corrections_use_the_latest_value(self):
        questions = (
            "전공 42학점이 아니라 45학점이야.",
            "전공은 42학점이라고 했는데 정정할게. 45학점이야.",
            "전공 42학점이야. 아니, 45학점으로 계산해줘.",
            "교양 30, 전공 42, 일반선택 12야. 전공은 실제로 45학점이야.",
        )
        for question in questions:
            with self.subTest(question=question):
                extracted = self.extractor.extract(question, UserProfile())
                self.assertEqual(extracted.profile.credits_by_category["major"], 45)
                self.assertEqual(extracted.conflicts, ())

    def test_same_message_duplicate_credit_without_correction_remains_a_conflict(self):
        extracted = self.extractor.extract(
            "전공 42학점이고 전공 45학점이야.", UserProfile()
        )
        self.assertEqual(extracted.conflicts, ("credits.major",))

    def test_explicit_correction_replaces_the_stored_value_without_repeating_it(self):
        current = self.extractor.extract("전공 42학점이야.", UserProfile()).profile
        extracted = self.extractor.extract(
            "아까 잘못 말했어. 전공은 45학점이야.", current
        )
        self.assertEqual(extracted.profile.credits_by_category["major"], 45)
        self.assertEqual(extracted.conflicts, ())

    def test_toeic_speaking_is_not_also_extracted_as_toeic(self):
        extracted = self.extractor.extract(
            "TOEIC Speaking 130점과 일반 TOEIC 700점이 있어.", UserProfile()
        )
        values = {item.test: item.value for item in extracted.profile.english_credentials}
        self.assertEqual(values, {"TOEIC": 700, "TOEIC_SPEAKING": 130})

    def test_unambiguous_credential_score_correction_may_omit_test_name(self):
        current = self.extractor.extract("토익은 650점이야.", UserProfile()).profile
        corrected = self.extractor.extract("아까 점수는 700점으로 정정할게.", current)
        self.assertEqual(
            {item.test: item.value for item in corrected.profile.english_credentials},
            {"TOEIC": 700},
        )

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

    def test_korean_credit_number_is_typed_before_profile_use(self):
        extracted = self.extractor.extract(
            "교양은 삼십 학점 들었다고 치고 부족분을 알려 줘.", UserProfile()
        )
        self.assertEqual(extracted.profile.credits_by_category, {"general": 30.0})

    def test_profile_mentions_inside_a_question_do_not_become_statement_only(self):
        service = PersonalizedCurriculumChatService(
            _ResponseService(ChatResponse.unresolved("request:question")),
            bundle_path=BUNDLE,
        )
        for question in (
            "컴퓨터공학과 전공필수 과목을 보여 줘.",
            "컴공과 과목의 모든 과목명을 다 출력해 줘.",
            "2026 컴공 3학년 과목을 한 번에 정리해 줘.",
            "컴공이고 자료구조를 들었어. 더 필요한 필수 과목을 알고 싶어.",
            "편입생에게도 공통 교양 이수 의무가 있는지 근거로 설명해 줘.",
            "서울대학교 컴퓨터공학과 규정을 검색해 줘.",
        ):
            with self.subTest(question=question):
                self.assertFalse(service._is_profile_statement_only(question))

    def test_open_next_term_recommendation_asks_for_current_grade_only(self):
        service = PersonalizedCurriculumChatService(
            _ResponseService(ChatResponse.unresolved("request:recommendation")),
            bundle_path=BUNDLE,
        )
        question = "컴공과인데 자료구조를 들었어. 다음 학기에 무엇을 듣는 게 좋아?"
        extraction = self.extractor.extract(question, UserProfile())

        outcome = service._preflight(question, extraction)

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(outcome.required_user_fields, ("current_grade_year",))
        self.assertIn("현재 학년", outcome.message)

    def test_verified_course_list_completeness_uses_course_identity_and_evidence(self):
        service = PersonalizedCurriculumChatService(
            _ResponseService(ChatResponse.unresolved("request:complete-count")),
            bundle_path=BUNDLE,
        )
        self.assertEqual(
            service.expected_unique_course_count(
                {
                    "academic_year": 2026,
                    "department_id": "department:cwnu:cse",
                }
            ),
            37,
        )
        self.assertEqual(
            service.expected_unique_course_count(
                {
                    "academic_year": 2026,
                    "area_ids": [
                        "area:general:balanced:digital-communication",
                        "area:general:balanced:humanities-arts",
                        "area:general:balanced:nature-science-technology",
                        "area:general:balanced:society-culture",
                    ],
                }
            ),
            189,
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

    def test_course_name_with_instrumental_particle_resolves_identity(self):
        matches = self.resolver.find_mentions("고급자료구조로 대신하면 인정돼?")
        self.assertEqual(tuple(item.name_ko for item in matches), ("고급자료구조",))

    def test_unknown_schema_and_invalid_ranges_fail_closed(self):
        with self.assertRaises(ProfileValidationError):
            UserProfile.from_payload({"version": 99})
        with self.assertRaises(ProfileValidationError):
            UserProfile.from_payload({"version": 1, "credits": {"major": -1}})
        with self.assertRaises(ProfileValidationError):
            UserProfile.from_payload({"version": 1, "unexpected": "value"})


class PersonalizedOutcomeTests(unittest.TestCase):
    def test_course_substitution_wording_requires_direct_verified_evidence(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=1)
        service = PersonalizedCurriculumChatService(
            _ResponseService(response), bundle_path=BUNDLE
        )
        service.nodes[response.used_fact_ids[0]] = {
            "id": response.used_fact_ids[0],
            "labels": ["Rule"],
            "properties": {
                "status": "VERIFIED",
                "description_ko": "일반 졸업학점 기준은 130학점이다.",
            },
        }

        limitation = service._grounding_limitation(
            "한 과목을 다른 과목으로 대신하면 인정돼?", response
        )

        self.assertIsNotNone(limitation)
        self.assertIn("직접 근거", limitation)

    def test_named_mandatory_rule_answers_generic_credit_substitution_safely(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=1)
        service = PersonalizedCurriculumChatService(
            _ResponseService(response), bundle_path=BUNDLE
        )
        service.nodes[response.used_fact_ids[0]] = {
            "id": response.used_fact_ids[0],
            "labels": ["Rule"],
            "properties": {
                "status": "VERIFIED",
                "description_ko": (
                    "기초교양 미래설계 영역에서 대학생활의설계를 필수로 이수하며 "
                    "최소 1학점이다."
                ),
            },
        }

        limitation = service._grounding_limitation(
            "지정 과목 대신 다른 3학점 교양과목으로 대체해도 가능해?", response
        )

        self.assertIsNone(limitation)

    def test_verified_credential_threshold_is_not_course_substitution_gap(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=1)
        service = PersonalizedCurriculumChatService(
            _ResponseService(response), bundle_path=BUNDLE
        )
        service.nodes[response.used_fact_ids[0]] = {
            "id": response.used_fact_ids[0],
            "labels": ["Rule"],
            "properties": {
                "status": "VERIFIED",
                "description_ko": "대학영어 이수 면제 TOEIC 기준은 700점 이상이다.",
            },
        }

        limitation = service._grounding_limitation(
            "TOEIC으로 영어를 대체하려면 기준점이 확정돼 있어?", response
        )

        self.assertIsNone(limitation)

    def test_next_semester_eligibility_requires_direct_restriction_evidence(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=2)
        service = PersonalizedCurriculumChatService(
            _ResponseService(response), bundle_path=BUNDLE
        )
        for fact_id in response.used_fact_ids:
            service.nodes[fact_id] = {
                "id": fact_id,
                "labels": ["CourseOffering"],
                "properties": {
                    "status": "VERIFIED",
                    "description_ko": "교육과정상 개설 학년과 학기를 확인했다.",
                },
            }

        limitation = service._grounding_limitation(
            "자료구조를 들은 다음 학기에 고급자료구조를 들을 수 있어?",
            response,
        )

        self.assertIsNotNone(limitation)
        self.assertIn("직접 근거", limitation)

    def test_elliptical_academic_terms_do_not_fail_scope_preflight(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        profile = UserProfile()
        for question in (
            "학수번호도 알려 줘.",
            "성적표 없이 가능한 일반 기준만 알려 줘.",
            "그 추천에 실시간 개설 정보도 포함돼?",
        ):
            with self.subTest(question=question):
                extraction = service.extractor.extract(question, profile)
                outcome = service._preflight(question, extraction)
                self.assertNotEqual(
                    getattr(outcome, "status", None), OutcomeStatus.OUT_OF_SCOPE
                )

        live = service._preflight(
            "그 추천에 실시간 개설 정보도 포함돼?",
            service.extractor.extract(
                "그 추천에 실시간 개설 정보도 포함돼?", profile
            ),
        )
        self.assertEqual(live.status, OutcomeStatus.ADVISORY)

    def test_course_omission_necessity_wording_is_general_not_question_specific(self):
        for question in (
            "이 과목 안 들으면 안 되는지 알려 줘.",
            "이 수업을 꼭 이수해야 하나요?",
            "반드시 수강해야 돼?",
        ):
            with self.subTest(question=question):
                self.assertIsNotNone(_COURSE_OMISSION_NECESSITY.search(question))

    def test_personal_calculation_asks_only_for_missing_user_information(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("내가 지금까지 들은 과목과 학점으로 졸업할 수 있어?")
        self.assertEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertIn("completed_courses", result.outcome.required_user_fields)
        self.assertIn("credits", result.outcome.required_user_fields)
        self.assertEqual(base.calls, 0)

    def test_general_rules_without_transcript_do_not_request_personal_history(self):
        base = _ResponseService(ChatResponse.not_found("request:general-rules"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("성적표 없이 가능한 일반 졸업 기준만 알려 줘.")
        self.assertNotEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(result.outcome.required_user_fields, ())
        self.assertEqual(base.calls, 1)

    def test_inline_numeric_substitution_condition_is_not_requested_again(self):
        base = _ResponseService(ChatResponse.not_found("request:inline-condition"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask(
            "한 영역에서 2학점을 들었어. 부족한 1학점은 확대교양으로 채워도 돼?"
        )
        self.assertNotEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(base.calls, 1)

    def test_live_priority_question_is_conditional_advisory(self):
        base = _ResponseService(ChatResponse.not_found("request:live-priority"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask(
            "과목이 같은 시간대라 하나만 들을 수 있어. 무엇을 우선해야 해?"
        )
        self.assertEqual(result.outcome.status, OutcomeStatus.ADVISORY)
        self.assertIn("실제 시간표", result.outcome.message)
        self.assertEqual(base.calls, 0)

    def test_explicit_exemption_scope_is_evidence_gap_not_user_information(self):
        base = _ResponseService(
            ChatResponse.clarification_required(
                "request:exemption-gap", "어떤 이수요건을 말씀하시나요?"
            )
        )
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("편입생의 전공필수도 면제인가요?")
        self.assertEqual(result.outcome.status, OutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.outcome.required_user_fields, ())
        self.assertIn("직접 근거", result.outcome.message)

    def test_explicit_course_substitution_is_evidence_gap_not_clarification(self):
        base = _ResponseService(
            ChatResponse.clarification_required(
                "request:substitution-gap", "무엇을 알고 싶으신가요?"
            )
        )
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("고급자료구조로 자료구조를 대신하면 인정돼?")

        self.assertEqual(result.outcome.status, OutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.outcome.required_user_fields, ())
        self.assertIn("대체 인정을 확정할 직접 근거", result.outcome.message)

    def test_explicit_other_curriculum_is_out_of_scope_without_query(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("2025학년도 컴퓨터공학과 기준을 알려줘")
        self.assertEqual(result.outcome.status, OutcomeStatus.OUT_OF_SCOPE)
        self.assertEqual(base.calls, 0)

    def test_data_declared_institution_scope_rejects_another_university(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("서울대학교 컴퓨터공학과 규정을 검색해 줘.")
        self.assertEqual(result.outcome.status, OutcomeStatus.OUT_OF_SCOPE)
        self.assertEqual(base.calls, 0)
        self.assertIn("2026학년도", result.outcome.message)

    def test_missing_verified_fact_is_not_relabelled_as_missing_user_data(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask("휴학 후 교육과정 적용 규정을 알려줘")
        self.assertEqual(result.outcome.status, OutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.outcome.required_user_fields, ())
        self.assertEqual(base.calls, 0)
        self.assertIn("휴학·복학·전과", result.outcome.message)

    def test_curriculum_application_asks_for_only_missing_scope_in_korean(self):
        base = _ResponseService(ChatResponse.not_found("unused"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)
        result = service.ask(
            "학번을 말하지 않았는데 적용 교육과정을 확정할 수 있어?"
        )
        self.assertEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(
            result.outcome.required_user_fields,
            ("admission_year", "department_id"),
        )
        self.assertIn("적용 교육과정을 확인하려면", result.outcome.message)
        self.assertEqual(base.calls, 0)

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

    def test_personal_completion_record_wording_is_academic_and_requests_minimum_fields(self):
        base = _ResponseService(ChatResponse.not_found("request"))
        service = PersonalizedCurriculumChatService(base, bundle_path=BUNDLE)

        result = service.ask("그러면 내 이수내역 기준으로 무엇이 부족해?")

        self.assertEqual(result.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(
            result.outcome.required_user_fields,
            ("completed_courses", "credits"),
        )
        self.assertEqual(base.calls, 0)


if __name__ == "__main__":
    unittest.main()
