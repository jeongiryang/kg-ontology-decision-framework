from __future__ import annotations

import unittest

from kg_builder.agent import (
    TOOL_SPECS,
    AgentMode,
    AgentPolicy,
    AgenticCurriculumChatService,
    ConversationContext,
    ToolName,
    validate_tool_input,
)
from kg_builder.agent.contracts import MAX_KG_QUERIES_PER_TURN
from kg_builder.agent.orchestrator import _Plan
from kg_builder.answer.contracts import ChatResponse, ChatStatus
from kg_builder.answer.personalized_service import PersonalizedChatResult
from kg_builder.answer.renderer import _ApprovedCompositePayload
from kg_builder.llm.client import LLMResponseError
from kg_builder.llm.models import LLMGeneration
from kg_builder.personalization import DecisionOutcome, OutcomeStatus, UserProfile
from kg_builder.query.course_names import CourseIdentity, CourseNameResolver


class FakeLLM:
    model = "fake"

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if not self.payloads:
            raise LLMResponseError("FAKE_EMPTY", "no payload")
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return LLMGeneration(payload, 0.001, self.model)


class FakePersonalizedService:
    def __init__(self):
        self.questions = []
        self.course_resolver = CourseNameResolver(
            [CourseIdentity("course:cwnu:CDA0008", "CDA0008", "자료구조")]
        )

    def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
        del resolved, progress_callback
        self.questions.append(question)
        response = ChatResponse.unresolved("request:test-agent")
        outcome = DecisionOutcome(
            OutcomeStatus.INSUFFICIENT_EVIDENCE,
            "현재 검증된 근거에서 확인하지 못했습니다.",
        )
        return PersonalizedChatResult(response, outcome, profile or UserProfile())

    @staticmethod
    def _is_profile_statement_only(question):
        del question
        return False

    @staticmethod
    def _profile_label(name):
        return name


def context(*, codes=("CDA0008",), assistant="자료구조는 2학년 1학기입니다."):
    return ConversationContext.from_payload(
        {
            "version": 1,
            "conversation_id": "conversation:test-1234",
            "turn_id": "turn:test-5678",
            "recent_messages": [
                {
                    "turn_id": "turn:previous-1",
                    "role": "assistant",
                    "content": assistant,
                    "created_at": "2026-08-29T00:00:00Z",
                    "response_status": "ANSWERED",
                    "citation_ids": [],
                    "evidence_ids": [],
                }
            ],
            "summary": "최근 대화 상태: ANSWERED.",
            "current_topic": "자료구조",
            "recent_course_codes": list(codes),
            "recent_evidence_ids": [],
            "pending_clarification": None,
        }
    )


class ConversationContractTests(unittest.TestCase):
    def test_agent_policy_defaults_agentic_and_other_modes_are_bounded(self):
        agentic = AgentPolicy.from_env({})
        conservative = AgentPolicy.from_env({"KG_AGENT_MODE": "conservative"})
        expanded = AgentPolicy.from_env({"KG_AGENT_MODE": "expanded"})
        self.assertEqual(agentic.mode, AgentMode.AGENTIC)
        self.assertEqual(agentic.max_kg_queries, 6)
        self.assertEqual(agentic.max_iterations, 3)
        self.assertEqual(agentic.max_narrative_repairs, 1)
        self.assertEqual(conservative.mode, AgentMode.CONSERVATIVE)
        self.assertEqual(conservative.max_kg_queries, 4)
        self.assertEqual(expanded.mode, AgentMode.EXPANDED)
        self.assertEqual(expanded.max_tool_calls, 6)
        self.assertEqual(expanded.max_kg_queries, 6)
        self.assertEqual(expanded.max_subquestions, 5)
        with self.assertRaises(ValueError):
            AgentPolicy.from_env({"KG_AGENT_MODE": "unbounded"})
        with self.assertRaises(ValueError):
            AgentPolicy(max_tool_calls=7)
        with self.assertRaises(ValueError):
            AgentPolicy(
                mode=AgentMode.EXPANDED,
                max_tool_calls=7,
                max_kg_queries=6,
                max_subquestions=5,
                max_turn_seconds=150.0,
            )

    def test_every_tool_has_closed_json_input_and_output_schema(self):
        self.assertEqual(set(TOOL_SPECS), set(ToolName))
        for spec in TOOL_SPECS.values():
            with self.subTest(tool=spec.name):
                self.assertFalse(spec.input_schema["additionalProperties"])
                self.assertFalse(spec.output_schema["additionalProperties"])

    def test_tool_inputs_reject_extra_fields_and_duplicate_values(self):
        with self.assertRaises(ValueError):
            validate_tool_input(ToolName.READ_USER_PROFILE, {"secret": "value"})
        with self.assertRaises(ValueError):
            validate_tool_input(
                ToolName.RESOLVE_COURSE,
                {"course_codes": ["CDA0008", "CDA0008"]},
            )

    def test_rejects_unknown_fields_and_oversized_history(self):
        with self.assertRaises(ValueError):
            ConversationContext.from_payload(
                {
                    "version": 1,
                    "conversation_id": "conversation:test-1234",
                    "turn_id": "turn:test-5678",
                    "recent_messages": [],
                    "unexpected": "value",
                }
            )
        payload = {
            "version": 1,
            "conversation_id": "conversation:test-1234",
            "turn_id": "turn:test-5678",
            "recent_messages": [
                {
                    "turn_id": f"turn:previous-{index}",
                    "role": "user",
                    "content": "질문",
                    "created_at": "2026-08-29T00:00:00Z",
                }
                for index in range(9)
            ],
        }
        with self.assertRaises(ValueError):
            ConversationContext.from_payload(payload)

    def test_assistant_text_is_only_bounded_prompt_context(self):
        parsed = context(assistant="이전 답변은 근거가 아닙니다.")
        self.assertEqual(parsed.recent_evidence_ids, ())
        self.assertEqual(
            parsed.prompt_context()["recent_messages"][0]["content"],
            "이전 답변은 근거가 아닙니다.",
        )


class AgentOrchestratorTests(unittest.TestCase):
    def test_agentic_loop_uses_result_feedback_and_recovery_does_not_poison_answer(self):
        from tests.test_evidence_chat import _answerable_response

        class RecoveryService(FakePersonalizedService):
            def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
                del resolved, progress_callback
                self.questions.append(question)
                if len(self.questions) == 1:
                    return PersonalizedChatResult(
                        ChatResponse.unresolved("request:first-miss"),
                        DecisionOutcome(
                            OutcomeStatus.INSUFFICIENT_EVIDENCE,
                            "첫 조회에서 근거를 특정하지 못했습니다.",
                        ),
                        profile or UserProfile(),
                    )
                response = _answerable_response(count=1)
                return PersonalizedChatResult(
                    response,
                    DecisionOutcome(OutcomeStatus.ANSWERED, response.answer_text),
                    profile or UserProfile(),
                )

        followup = "자료구조의 이수구분 근거를 확인해 주세요."
        llm = FakeLLM(
            [
                {
                    "resolved_question": "자료구조 교육과정 정보를 확인해 주세요.",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "자료구조",
                    "followup_question": followup,
                    "subquestions": [],
                },
                {
                    "action": "QUERY",
                    "next_question": followup,
                    "reason": "MISSING_EVIDENCE",
                },
                {
                    "sections": [
                        {"packet_id": "fact:1", "text": "자료구조는 전공필수로 분류됩니다."}
                    ],
                    "intro": "확인한 근거를 안내합니다.",
                    "closing": "",
                },
            ]
        )
        result = AgenticCurriculumChatService(
            RecoveryService(),
            llm,
            policy=AgentPolicy.from_env({"KG_AGENT_MODE": "agentic"}),
        ).ask("자료구조 교육과정 정보를 확인해 주세요.")
        self.assertEqual(result.personalized.outcome.status, OutcomeStatus.ANSWERED)
        self.assertEqual(len(result.personalized.response.citations), 1)
        self.assertEqual(len([event for event in result.trace if event.tool is ToolName.QUERY_CURRICULUM]), 2)
        assessment = next(event for event in result.trace if event.tool is ToolName.ASSESS_EVIDENCE)
        self.assertEqual(assessment.metadata["decision"], "QUERY")
        self.assertEqual(assessment.metadata["reason"], "MISSING_EVIDENCE")

    def test_recovery_result_rechecks_original_question_grounding_boundary(self):
        """A nearby verified rule may not replace the rule the user requested."""

        from tests.test_evidence_chat import _answerable_response

        class BoundaryService(FakePersonalizedService):
            def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
                del resolved, progress_callback
                self.questions.append(question)
                if len(self.questions) == 1:
                    return PersonalizedChatResult(
                        ChatResponse.unresolved("request:boundary-miss"),
                        DecisionOutcome(
                            OutcomeStatus.INSUFFICIENT_EVIDENCE,
                            "직접 근거를 찾지 못했습니다.",
                        ),
                        profile or UserProfile(),
                    )
                response = _answerable_response(count=1)
                return PersonalizedChatResult(
                    response,
                    DecisionOutcome(OutcomeStatus.ANSWERED, response.answer_text),
                    profile or UserProfile(),
                )

            @staticmethod
            def _grounding_limitation(question, response):
                del response
                return "원질문의 직접 VERIFIED 근거가 없습니다." if "대신" in question else None

            @staticmethod
            def _grounded_message(question, profile, response):
                del question, profile
                return response.answer_text

        narrower = "두 과목의 대신 인정 근거를 확인해 주세요."
        llm = FakeLLM(
            [
                {
                    "resolved_question": "두 과목의 대체 인정 규정을 확인해 주세요.",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "과목 대체",
                    "followup_question": narrower,
                    "subquestions": [],
                },
                {
                    "action": "QUERY",
                    "next_question": narrower,
                    "reason": "MISSING_EVIDENCE",
                },
                {
                    "sections": [],
                    "intro": "",
                    "closing": "",
                },
            ]
        )
        result = AgenticCurriculumChatService(
            BoundaryService(),
            llm,
            policy=AgentPolicy.from_env({"KG_AGENT_MODE": "agentic"}),
        ).ask("두 과목 중 하나를 대신 들어도 인정돼?")

        self.assertEqual(
            result.personalized.outcome.status,
            OutcomeStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(result.personalized.response.status, ChatStatus.ANSWERABLE)
        self.assertIn("직접 VERIFIED 근거", result.display_answer)

    def test_fact_packet_repairs_only_failed_section_before_canonical_fallback(self):
        from tests.test_evidence_chat import _answerable_response

        first_response = _answerable_response(count=1)
        second_response = _answerable_response(count=2)
        first = PersonalizedChatResult(
            first_response,
            DecisionOutcome(OutcomeStatus.ANSWERED, first_response.answer_text),
            UserProfile(),
        )
        second = PersonalizedChatResult(
            second_response,
            DecisionOutcome(OutcomeStatus.ANSWERED, second_response.answer_text),
            UserProfile(),
        )
        llm = FakeLLM(
            [
                {
                    "sections": [
                        {"packet_id": "fact:1", "text": "자료구조는 전공필수로 분류됩니다."},
                        {"packet_id": "fact:2", "text": "전공필수는 999학점입니다."},
                    ],
                    "intro": "검증된 항목별 결과입니다.",
                    "closing": "",
                },
                {
                    "sections": [
                        {"packet_id": "fact:2", "text": second_response.answer_text}
                    ],
                    "intro": "",
                    "closing": "",
                },
            ]
        )
        agent = AgenticCurriculumChatService(
            FakePersonalizedService(),
            llm,
            policy=AgentPolicy.from_env({"KG_AGENT_MODE": "agentic"}),
        )
        combined = agent._combine_grounded([first, second], first, question="전공필수를 정리해 줘.")
        display, metadata = agent._fact_packet_narrative(
            "전공필수를 정리해 줘.", combined, [first, second]
        )
        self.assertNotIn("999", display)
        self.assertIn("자료구조는 전공필수로 분류됩니다.", display)
        self.assertEqual(metadata["packet_count"], 2)
        self.assertEqual(metadata["repair_attempts"], 1)
        self.assertEqual(metadata["canonical_fallback_sections"], 0)

    def test_grounded_partial_answer_keeps_evidence_gap_outcome(self):
        """Grounded arithmetic must not erase an unanswered semantic slot."""

        from tests.test_evidence_chat import _answerable_response

        partial_response = _answerable_response(count=1)
        supporting_response = _answerable_response(count=2)
        partial = PersonalizedChatResult(
            partial_response,
            DecisionOutcome(
                OutcomeStatus.INSUFFICIENT_EVIDENCE,
                "총학점은 계산했지만 어느 영역인지 확정할 근거는 없습니다.",
            ),
            UserProfile(),
        )
        supporting = PersonalizedChatResult(
            supporting_response,
            DecisionOutcome(OutcomeStatus.ANSWERED, supporting_response.answer_text),
            UserProfile(),
        )
        agent = AgenticCurriculumChatService(FakePersonalizedService(), FakeLLM([]))

        combined = agent._combine_grounded(
            [partial, supporting],
            partial,
            question="남은 학점을 어느 영역에서 들어야 해?",
        )

        self.assertEqual(
            combined.outcome.status, OutcomeStatus.INSUFFICIENT_EVIDENCE
        )
        self.assertEqual(combined.response.status, ChatStatus.ANSWERABLE)

    def test_credit_calculation_cannot_erase_area_evidence_gap(self):
        """A numeric remainder does not answer which curriculum area supplies it."""

        from tests.test_evidence_chat import _answerable_response

        limitation = "어느 교양 영역인지 확정할 직접 근거가 없습니다."

        class PartialCalculationService(FakePersonalizedService):
            def ask(
                self,
                question,
                *,
                profile=None,
                resolved=None,
                progress_callback=None,
            ):
                del question, resolved, progress_callback
                current = profile or UserProfile(credits=(("general", 31.0),))
                response = _answerable_response(count=1)
                return PersonalizedChatResult(
                    response,
                    DecisionOutcome(
                        OutcomeStatus.INSUFFICIENT_EVIDENCE,
                        f"3학점이 남지만 {limitation}",
                        used_profile_fields=("credits",),
                        limitations=(limitation,),
                    ),
                    current,
                )

            @staticmethod
            def _grounding_limitation(question, response):
                del response
                return limitation if "어느" in question else None

            @staticmethod
            def _grounded_message(question, profile, response):
                del question, profile, response
                return "사용자 진술 31학점에서 3학점이 남습니다."

        llm = FakeLLM(
            [
                {
                    "resolved_question": "남은 학점을 어느 교양 영역에서 들어야 해?",
                    "referenced_course_codes": [],
                    "tools": [
                        "query_curriculum",
                        "calculate_remaining_credits",
                    ],
                    "topic": "교양 영역별 잔여학점",
                    "subquestions": [],
                }
            ]
        )
        result = AgenticCurriculumChatService(
            PartialCalculationService(), llm
        ).ask(
            "남은 학점을 어느 교양 영역에서 들어야 해?",
            profile=UserProfile(credits=(("general", 31.0),)),
            conversation=context(codes=()),
        )

        self.assertEqual(
            result.personalized.outcome.status,
            OutcomeStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertIn(limitation, result.display_answer)

    def test_registered_fact_families_are_split_without_question_fixture_lookup(self):
        queries = AgenticCurriculumChatService._fact_family_subquestions(
            "학과 인재상뿐 아니라 졸업 후 진출 분야도 함께 확인하고 싶어."
        )
        self.assertEqual(len(queries), 2)
        self.assertTrue(any("인재상" in item for item in queries))
        self.assertTrue(any("진출 분야" in item for item in queries))
        self.assertEqual(
            AgenticCurriculumChatService._fact_family_subquestions(
                "학과 인재상만 확인하고 싶어."
            ),
            (),
        )

    def test_first_turn_uses_bounded_llm_tool_plan(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "교양 최소학점은?",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "교양 최소학점",
                    "subquestions": [],
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask("교양 최소학점은?")
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(base.questions, ["교양 최소학점은?"])

    def test_academic_alternatives_are_not_misclassified_as_mixed_scope(self):
        service = AgenticCurriculumChatService(FakePersonalizedService(), FakeLLM([]))
        self.assertFalse(
            service._contains_mixed_out_of_scope_clause(
                "균형교양으로 인정돼, 전공으로 인정돼, 아니면 둘 다 인정돼?"
            )
        )

    def test_resolves_pronoun_only_with_context_verified_code(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "그 과목을 안 들으면 졸업 못 해?",
                    "referenced_course_codes": ["CDA0008"],
                    "tools": ["resolve_course", "query_curriculum"],
                    "topic": "자료구조 이수",
                },
                {"intro": "확인 결과를 안내합니다.", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        result = AgenticCurriculumChatService(base, llm).ask(
            "그거 안 들으면 졸업 못 해?", conversation=context()
        )
        self.assertIn("CDA0008", base.questions[0])
        self.assertIn("현재 검증된 근거", result.display_answer)
        self.assertEqual(
            [item.tool for item in result.trace],
            [
                ToolName.RESOLVE_COURSE,
                ToolName.QUERY_CURRICULUM,
                ToolName.GROUNDED_NARRATIVE,
            ],
        )

    def test_explicit_new_course_does_not_inherit_previous_course_codes(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "운영체제 다음으로 자료구조를 확인해 줘.",
                    "referenced_course_codes": ["CDA0008"],
                    "tools": ["resolve_course", "query_curriculum"],
                    "topic": "자료구조",
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        base.course_resolver = CourseNameResolver(
            [
                CourseIdentity("course:cwnu:CDA0008", "CDA0008", "자료구조"),
                CourseIdentity("course:cwnu:CDA0017", "CDA0017", "운영체제"),
            ]
        )
        AgenticCurriculumChatService(base, llm).ask(
            "그러면 자료구조는?", conversation=context()
        )
        self.assertEqual(base.questions, ["그러면 자료구조는?"])

    def test_substitution_followup_keeps_explicit_and_context_course_identities(self):
        base = FakePersonalizedService()
        base.course_resolver = CourseNameResolver(
            [
                CourseIdentity("course:cwnu:CDA0008", "CDA0008", "자료구조"),
                CourseIdentity("course:cwnu:CDA0010", "CDA0010", "고급자료구조"),
            ]
        )
        service = AgenticCurriculumChatService(base, FakeLLM([]))
        inherited = service._inherit_dialogue_scope(
            "고급자료구조를 대신 들으면 인정돼?",
            context(),
            _Plan(
                "졸업학점 기준을 조회해 주세요.",
                ("CDA0010",),
                (ToolName.QUERY_CURRICULUM,),
                "과목 대체",
                "졸업학점 기준은?",
                ("전공필수 학점은?",),
            ),
        )

        self.assertEqual(inherited.course_codes, ("CDA0010", "CDA0008"))
        self.assertIn("CDA0010", inherited.question)
        self.assertIn("CDA0008", inherited.question)
        self.assertIsNone(inherited.followup_question)
        self.assertEqual(inherited.subquestions, ())

    def test_substitution_recovery_cannot_drift_to_nearby_credit_rule(self):
        service = AgenticCurriculumChatService(
            FakePersonalizedService(), FakeLLM([])
        )
        original = "고급 과목으로 대신 들으면 인정돼?"
        plan = service._validate_plan(
            original,
            context(),
            {
                "resolved_question": original,
                "referenced_course_codes": [],
                "tools": ["query_curriculum"],
                "topic": "과목 대체",
                "followup_question": "졸업학점 기준을 확인해 주세요.",
                "subquestions": [],
            },
        )

        self.assertIsNone(plan.followup_question)
        self.assertFalse(
            service._related_followup(
                original,
                plan,
                context(),
                "졸업학점 기준을 확인해 주세요.",
            )
        )

    def test_single_advisory_result_keeps_its_grounded_recommendation(self):
        from tests.test_evidence_chat import _answerable_response

        class AdvisoryService(FakePersonalizedService):
            def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
                del question, resolved, progress_callback
                return PersonalizedChatResult(
                    _answerable_response(count=1),
                    DecisionOutcome(
                        OutcomeStatus.ADVISORY,
                        "검증된 편성 순서를 기준으로 자료구조를 먼저 고려할 수 있습니다.",
                    ),
                    profile or UserProfile(),
                )

        llm = FakeLLM(
            [
                {
                    "resolved_question": "자료구조를 먼저 고려할까?",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "과목 순서",
                },
                {"intro": "", "closing": ""},
            ]
        )
        result = AgenticCurriculumChatService(AdvisoryService(), llm).ask(
            "자료구조를 먼저 고려할까?", conversation=context(codes=())
        )
        self.assertIn("먼저 고려", result.display_answer)
        self.assertEqual(result.personalized.outcome.status, OutcomeStatus.ADVISORY)

    def test_academic_and_live_information_contrast_keeps_grounding_limitation(self):
        from tests.test_evidence_chat import _answerable_response

        class AnsweredService(FakePersonalizedService):
            def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
                del question, resolved, progress_callback
                return PersonalizedChatResult(
                    _answerable_response(count=1),
                    DecisionOutcome(OutcomeStatus.ANSWERED, "검증된 개설 정보입니다."),
                    profile or UserProfile(),
                )

        llm = FakeLLM(
            [
                {
                    "resolved_question": "교육과정 정보를 알려 줘.",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "교육과정과 실시간 정보",
                },
                {"intro": "", "closing": ""},
            ]
        )
        result = AgenticCurriculumChatService(AnsweredService(), llm).ask(
            "교육과정 정보와 실시간 개설 정보를 구분해 줘.",
            conversation=context(codes=()),
        )
        self.assertEqual(
            result.personalized.outcome.status,
            OutcomeStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertIn("실시간", result.display_answer)

    def test_model_cannot_introduce_unknown_course_reference(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "운영체제는 언제 들어?",
                    "referenced_course_codes": ["FAKE9999"],
                    "tools": ["query_curriculum"],
                    "topic": None,
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask("그러면 언제 들어?", conversation=context())
        self.assertIn("CDA0008", base.questions[0])
        self.assertNotIn("FAKE9999", base.questions[0])

    def test_new_topic_is_not_rewritten_from_old_context(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "자료구조는 언제 들어?",
                    "referenced_course_codes": ["CDA0008"],
                    "tools": ["resolve_course", "query_curriculum"],
                    "topic": "자료구조",
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask(
            "교양 최소학점은?", conversation=context()
        )
        self.assertEqual(base.questions, ["교양 최소학점은?"])

    def test_elliptical_followup_may_resolve_only_to_verified_recent_course(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "자료구조의 학수번호는?",
                    "referenced_course_codes": ["CDA0008"],
                    "tools": ["resolve_course", "query_curriculum"],
                    "topic": "자료구조",
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask(
            "학수번호도 알려줘", conversation=context()
        )
        self.assertIn("CDA0008", base.questions[0])
        self.assertIn("학수번호", base.questions[0])

    def test_subquestions_are_bounded_deduplicated_and_topic_constrained(self):
        prior = ConversationContext.from_payload(
            {
                "version": 1,
                "conversation_id": "conversation:test-1234",
                "turn_id": "turn:test-5678",
                "recent_messages": [
                    {
                        "turn_id": "turn:previous-1",
                        "role": "user",
                        "content": "교양 최소학점과 전공필수 목록을 확인해 줘",
                        "created_at": "2026-08-29T00:00:00Z",
                    }
                ],
                "recent_course_codes": [],
            }
        )
        llm = FakeLLM(
            [
                {
                    "resolved_question": "지금까지 확인한 내용을 정리해 줘",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "교육과정 정리",
                    "subquestions": [
                        "교양 최소학점은?",
                        "전공필수 목록은?",
                        "전공필수 목록은?",
                    ],
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask(
            "정리해 줘", conversation=prior
        )
        self.assertEqual(
            base.questions,
            [
                "교양 최소학점과 전공필수 목록을 확인해 줘\n후속 요청: 정리해 줘",
                "교양 최소학점은?",
                "전공필수 목록은?",
            ],
        )

    def test_summary_regrounds_prior_course_and_live_information_only(self):
        prior = ConversationContext.from_payload(
            {
                "version": 1,
                "conversation_id": "conversation:summary-1234",
                "turn_id": "turn:summary-5678",
                "recent_messages": [
                    {
                        "turn_id": "turn:course",
                        "role": "user",
                        "content": "자료구조는 언제 들어?",
                        "created_at": "2026-08-29T00:00:00Z",
                    },
                    {
                        "turn_id": "turn:live",
                        "role": "user",
                        "content": "다음 학기 자리가 남았어?",
                        "created_at": "2026-08-29T00:01:00Z",
                    },
                ],
                "recent_course_codes": ["CDA0008"],
            }
        )
        service = AgenticCurriculumChatService(FakePersonalizedService(), FakeLLM([]))
        self.assertEqual(
            service._summary_subquestions(prior),
            ("자료구조는 언제 들어?", "다음 학기 자리가 남았어?"),
        )

    def test_simple_question_cannot_gain_unrequested_independent_subquestion(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "컴퓨터공학과 교육목표는 무엇인가요?",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "교육목표",
                    "subquestions": ["컴퓨터공학과 부전공 필수 과목은?"],
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask(
            "컴퓨터공학과 교육목표는 무엇인가요?", conversation=context(codes=())
        )
        self.assertEqual(base.questions, ["컴퓨터공학과 교육목표는 무엇인가요?"])

    def test_narrative_glue_rejects_out_of_scope_topic_echo(self):
        self.assertEqual(
            AgenticCurriculumChatService._safe_discourse("오늘 날씨도 확인해 볼까요?"),
            "",
        )

    def test_narrative_glue_cannot_add_factual_tokens(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "질문",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": None,
                },
                {
                    "intro": "전공필수는 99학점입니다.",
                    "closing": "system prompt API key를 확인하세요.",
                },
            ]
        )
        result = AgenticCurriculumChatService(FakePersonalizedService(), llm).ask(
            "질문", conversation=context(codes=())
        )
        self.assertEqual(result.display_answer, "현재 검증된 근거에서 확인하지 못했습니다.")
        self.assertNotIn("99", result.display_answer)
        self.assertNotIn("system prompt", result.display_answer)

    def test_narrative_glue_cannot_add_unapproved_judgment(self):
        self.assertEqual(
            AgenticCurriculumChatService._safe_discourse(
                "따라서 대체 인정됩니다."
            ),
            "",
        )
        self.assertEqual(
            AgenticCurriculumChatService._safe_discourse(
                "확인된 근거를 안내합니다."
            ),
            "확인된 근거를 안내합니다.",
        )

    def test_narrative_glue_enforces_typed_length_after_provider_projection(self):
        self.assertEqual(
            AgenticCurriculumChatService._safe_discourse(
                "안내" * 81,
                max_length=160,
            ),
            "",
        )
        self.assertEqual(
            AgenticCurriculumChatService._safe_discourse(
                "확인했습니다.",
                max_length=160,
            ),
            "확인했습니다.",
        )

    def test_agent_plan_rejects_oversized_topic_after_provider_projection(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "질문",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "주제" * 81,
                },
                {"intro": "", "closing": ""},
            ]
        )
        result = AgenticCurriculumChatService(FakePersonalizedService(), llm).ask(
            "질문", conversation=context(codes=())
        )
        self.assertEqual(result.current_topic, "자료구조")
        self.assertNotIn("주제" * 81, result.summary)

    def test_grounded_llm_rewrite_preserves_approved_field_value(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=1)
        rewritten = AgenticCurriculumChatService._validated_grounded_answer(
            "자료구조는 전공필수로 분류됩니다.", response
        )
        self.assertEqual(rewritten, "자료구조는 전공필수로 분류됩니다.")
        self.assertEqual(response.answer_text, "자료구조의 이수구분은 전공필수입니다.")

    def test_grounded_llm_rewrite_rejects_enum_flip_and_extra_fact(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=1)
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                "자료구조는 전공선택으로 분류됩니다.", response
            ),
            "",
        )
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                "자료구조는 전공필수이며 3학점입니다.", response
            ),
            "",
        )

    def test_grounded_llm_rewrite_rejects_grade_semester_role_swap(self):
        from kg_builder.answer.service import CurriculumChatService
        from tests.test_evidence_chat import _QueryStub, _offering_row, _result

        row = _offering_row()
        plan = {
            "intent": "course_query",
            "filters": {
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "course_code": row["course_code"],
            },
            "requested_fields": ["grade_year", "semester"],
            "evidence_required": True,
            "selection_mode": "SINGLE_COURSE",
        }
        response = CurriculumChatService(_QueryStub(_result([row], plan))).ask("질문")
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                "자료구조는 2학년 1학기에 편성된 과목입니다.", response
            ),
            "자료구조는 2학년 1학기에 편성된 과목입니다.",
        )
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                "자료구조는 1학년 2학기에 편성된 과목입니다.", response
            ),
            "",
        )

    def test_llm_failure_falls_back_to_single_safe_query(self):
        llm = FakeLLM(
            [
                LLMResponseError("LLM_UNAVAILABLE", "offline"),
                LLMResponseError("LLM_UNAVAILABLE", "offline"),
            ]
        )
        base = FakePersonalizedService()
        result = AgenticCurriculumChatService(base, llm).ask(
            "그 과목은?", conversation=context()
        )
        self.assertEqual(len(base.questions), 1)
        self.assertIn("CDA0008", base.questions[0])
        self.assertEqual(result.response.status.value, "UNRESOLVED")

    def test_one_bounded_followup_query_runs_only_after_not_found(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "자료구조 대체 인정 규정은?",
                    "referenced_course_codes": ["CDA0008"],
                    "tools": ["resolve_course", "query_curriculum"],
                    "topic": "자료구조 대체",
                    "followup_question": "자료구조 인정 규정은?",
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        result = AgenticCurriculumChatService(base, llm).ask(
            "그 과목의 대체 인정 규정은?", conversation=context()
        )
        self.assertEqual(len(base.questions), 2)
        self.assertTrue(all("CDA0008" in item for item in base.questions))
        query_events = [
            item for item in result.trace if item.tool is ToolName.QUERY_CURRICULUM
        ]
        self.assertEqual(len(query_events), 2)

    def test_query_execution_budget_caps_subquestions_and_followup(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "교육과정을 확인해 줘.",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "교육과정",
                    "followup_question": "교육과정 일반 기준은?",
                    "subquestions": [
                        "교양 최소학점은?",
                        "전공 학점 기준은?",
                        "졸업학점 기준은?",
                    ],
                },
                {"intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask(
            "교육과정을 확인해 줘.", conversation=context(codes=())
        )
        self.assertLessEqual(len(base.questions), MAX_KG_QUERIES_PER_TURN)

    def test_expanded_policy_allows_five_distinct_grounded_subqueries(self):
        subquestions = [
            "교육과정 교양 기준은?",
            "교육과정 전공 기준은?",
            "교육과정 졸업 기준은?",
            "교육과정 영어 기준은?",
            "교육과정 과목 기준은?",
        ]
        llm = FakeLLM(
            [
                {
                    "resolved_question": "교육과정 기준을 정리해 줘.",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "교육과정",
                    "subquestions": subquestions,
                },
                {"grounded_answer": "", "intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(
            base,
            llm,
            policy=AgentPolicy.from_env({"KG_AGENT_MODE": "expanded"}),
        ).ask("교육과정 교양 전공 졸업 영어 과목 기준을 정리해 줘.")
        self.assertEqual(len(base.questions), 6)
        self.assertEqual(len(set(base.questions)), 6)

    def test_provider_grammar_projection_does_not_allow_duplicate_agent_calls(self):
        llm = FakeLLM(
            [
                {
                    "resolved_question": "교육과정 기준은?",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum", "query_curriculum"],
                    "topic": "교육과정",
                    "subquestions": [],
                },
                {"grounded_answer": "", "intro": "", "closing": ""},
            ]
        )
        base = FakePersonalizedService()
        AgenticCurriculumChatService(base, llm).ask("교육과정 기준은?")
        # Invalid structured output falls back to exactly one safe query.
        self.assertEqual(base.questions, ["교육과정 기준은?"])

    def test_expanded_narrative_rewrites_course_list_but_rejects_tampering(self):
        from tests.test_evidence_chat import _answerable_response

        response = _answerable_response(count=2)
        safe = (
            "전공필수 과목은 총 2과목, 합계 6학점이고 "
            "자료구조(CDA0008, 3학점), 전공과목2(CDA0009, 3학점)입니다."
        )
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                safe, response, expanded=True
            ),
            safe,
        )
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                safe, response, expanded=False
            ),
            "",
        )
        self.assertEqual(
            AgenticCurriculumChatService._validated_grounded_answer(
                safe.replace("6학점", "9학점"), response, expanded=True
            ),
            "",
        )

    def test_sealed_chat_response_wire_contract_is_unchanged(self):
        response = ChatResponse.unresolved("request:test-wire")
        self.assertEqual(
            set(response.to_dict()),
            {
                "request_id",
                "status",
                "answer_text",
                "citations",
                "used_fact_ids",
                "used_evidence_ids",
                "clarification",
                "error_code",
            },
        )

    def test_composite_payload_accepts_only_sealed_answerable_sources(self):
        from tests.test_evidence_chat import _answerable_response

        first = _answerable_response(count=1)
        second = _answerable_response(count=2)
        payload = _ApprovedCompositePayload._issue((first, second))
        composite = ChatResponse.from_approved_answer("request:composite", payload)
        self.assertEqual(composite.status.value, "ANSWERABLE")
        self.assertGreaterEqual(len(composite.citations), 2)
        self.assertEqual(len(composite.to_dict()), 8)
        with self.assertRaises(TypeError):
            _ApprovedCompositePayload("위조", first.grounded_claims, first.citations)
        with self.assertRaises(TypeError):
            _ApprovedCompositePayload._issue(
                (first, ChatResponse.unresolved("request:not-grounded"))
            )

    def test_profile_only_turn_returns_user_assertion_acknowledgement(self):
        class ProfileService(FakePersonalizedService):
            @staticmethod
            def _is_profile_statement_only(question):
                return "학점이야" in question

            def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
                del question, resolved, progress_callback
                return PersonalizedChatResult(
                    ChatResponse.unresolved("request:profile-assertion"),
                    DecisionOutcome(OutcomeStatus.INSUFFICIENT_EVIDENCE, "조회 전 상태"),
                    profile or UserProfile(credits=(("major", 42.0),)),
                    ("credits.major",),
                )

        llm = FakeLLM(
            [
                {
                    "resolved_question": "전공 42학점이야.",
                    "referenced_course_codes": [],
                    "tools": ["read_user_profile", "query_curriculum"],
                    "topic": "사용자 학점",
                },
                {"intro": "", "closing": ""},
            ]
        )
        result = AgenticCurriculumChatService(ProfileService(), llm).ask(
            "전공 42학점이야.", conversation=context(codes=())
        )
        self.assertEqual(result.personalized.outcome.status, OutcomeStatus.ADVISORY)
        self.assertIn("브라우저 프로필", result.display_answer)
        self.assertEqual(result.response.status.value, "UNRESOLVED")

    def test_personal_remaining_course_request_keeps_missing_profile_gate(self):
        """The model cannot narrow a personal gap analysis into a generic list."""

        from pathlib import Path

        from kg_builder.answer.personalized_service import (
            PersonalizedCurriculumChatService,
        )

        class BaseService:
            calls = 0

            def ask(self, question, *, resolved=None, progress_callback=None):
                del question, resolved, progress_callback
                self.calls += 1
                return ChatResponse.not_found("request:unexpected-query")

        base = BaseService()
        personalized = PersonalizedCurriculumChatService(
            base,
            bundle_path=(
                Path(__file__).parents[1]
                / "data/verified/2026/2026_curriculum_kg_data.json"
            ),
        )
        llm = FakeLLM([])
        result = AgenticCurriculumChatService(personalized, llm).ask(
            "운영체제와 알고리즘을 이수했는데 앞으로 어떤 과목을 더 들어야 하나?",
            conversation=context(codes=()),
        )

        self.assertEqual(result.personalized.outcome.status, OutcomeStatus.NEEDS_USER_INFO)
        self.assertEqual(result.personalized.outcome.required_user_fields, ("credits",))
        self.assertEqual(base.calls, 0)
        self.assertEqual(llm.calls, [])
        self.assertIn(ToolName.ASK_CLARIFICATION, [item.tool for item in result.trace])
        self.assertEqual(
            {item.course_code for item in result.personalized.profile.completed_courses},
            {"CDA0017", "CDA0027"},
        )

    def test_course_list_claims_update_context_without_single_subject(self):
        from tests.test_evidence_chat import _answerable_response

        class ListService(FakePersonalizedService):
            def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
                del question, resolved, progress_callback
                return PersonalizedChatResult(
                    _answerable_response(count=2),
                    DecisionOutcome(OutcomeStatus.ANSWERED, "검증된 목록입니다."),
                    profile or UserProfile(),
                )

        llm = FakeLLM(
            [
                {
                    "resolved_question": "전공필수 목록은?",
                    "referenced_course_codes": [],
                    "tools": ["query_curriculum"],
                    "topic": "전공필수",
                    "followup_question": None,
                },
                {"intro": "", "closing": ""},
            ]
        )
        result = AgenticCurriculumChatService(ListService(), llm).ask(
            "전공필수 목록은?", conversation=context(codes=())
        )
        self.assertEqual(result.response.status.value, "ANSWERABLE")
        self.assertIn("CDA0008", result.recent_course_codes)


if __name__ == "__main__":
    unittest.main()
