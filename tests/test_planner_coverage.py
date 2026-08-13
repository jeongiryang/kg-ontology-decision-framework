"""계획 단계에서 답을 넓히는 경로가 근거 계약을 깨지 않는지 고정한다.

이 파일이 지키는 것은 네 가지다.

- 좁히지 못한 질문을 거절 대신 넓혀 답하되, 근거 요구는 낮추지 않는다.
- 적재된 데이터가 이미 정해 둔 범위는 되묻지 않는다.
- 질문이 지목한 과목을 담지 않은 계획으로는 답하지 않는다.
- 계획 단계에서 멈춘 요청도 무엇이 왜 막혔는지 기록을 남긴다.
"""

from __future__ import annotations

import unittest

from kg_builder.llm.client import LLMResponseError
from kg_builder.llm.models import AttemptOutcome, LLMGeneration, PlanningStatus
from kg_builder.llm.planner import LocalQueryPlanner
from kg_builder.query.query_plan import SelectionMode


class SequenceClient:
    """정해진 순서로 계획 응답을 돌려주는 대역."""

    model = "stub"

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.prompts: list[str] = []

    def generate_json(self, *, system_prompt, user_prompt, response_schema):
        del system_prompt, response_schema
        self.prompts.append(user_prompt)
        return LLMGeneration(self.payloads.pop(0), 0.01, self.model)


def payload(**overrides):
    base = {
        "status": "READY",
        "intent": "테스트",
        "filters": {},
        "requested_fields": ["description_ko"],
        "evidence_required": True,
        "message": None,
        "selection_mode": "SINGLE_COURSE",
        "missing_scope": [],
    }
    base.update(overrides)
    return base


class ModeCorrectionTests(unittest.TestCase):
    def test_fields_owned_by_one_family_correct_the_mode(self) -> None:
        """요청 필드를 담을 수 있는 모드가 하나뿐이면 그 모드로 고친다."""

        corrected = LocalQueryPlanner._mode_for_fields(
            SelectionMode.SINGLE_COURSE.value, ["description_ko", "profile_order"]
        )
        self.assertEqual(corrected, SelectionMode.TALENT_PROFILE_LIST.value)

    def test_a_mode_that_already_fits_is_left_alone(self) -> None:
        corrected = LocalQueryPlanner._mode_for_fields(
            SelectionMode.SINGLE_COURSE.value, ["grade_year", "semester"]
        )
        self.assertEqual(corrected, SelectionMode.SINGLE_COURSE.value)

    def test_ambiguous_fields_do_not_move_the_mode(self) -> None:
        """여러 모드가 담을 수 있는 필드만으로는 모드를 바꾸지 않는다."""

        corrected = LocalQueryPlanner._mode_for_fields(
            SelectionMode.SINGLE_RULE.value, ["description_ko"]
        )
        self.assertEqual(corrected, SelectionMode.SINGLE_RULE.value)


class BroadeningTests(unittest.TestCase):
    def test_unresolved_rule_topic_answers_widely_instead_of_refusing(self) -> None:
        """어느 이수요건인지 못 고르면 되묻는 대신 관련 요건을 조회한다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="SINGLE_RULE",
                    requested_fields=["value"],
                    missing_scope=["RULE_TOPIC"],
                    message="어떤 이수요건인지 모르겠습니다",
                )
            ]
        )
        outcome = LocalQueryPlanner(client).plan("몇 학점이야?")
        self.assertIs(outcome.status, PlanningStatus.READY)
        self.assertIsNotNone(outcome.plan)
        self.assertIn(
            outcome.broadened, {"RULE_TOPIC_NARROWED", "RULE_TOPIC_UNRESOLVED"}
        )
        self.assertIs(outcome.plan.selection_mode, SelectionMode.MULTIPLE_RULES)
        self.assertGreater(len(outcome.plan.filters["rule_ids"]), 1)

    def test_broadening_never_lowers_the_evidence_requirement(self) -> None:
        """모델이 근거 요구를 비워 보내도 넓힌 조회는 근거를 요구한다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="SINGLE_RULE",
                    evidence_required=False,
                    missing_scope=["RULE_TOPIC"],
                )
            ]
        )
        outcome = LocalQueryPlanner(client).plan("몇 학점이야?")
        self.assertIs(outcome.status, PlanningStatus.READY)
        self.assertTrue(outcome.plan.evidence_required)

    def test_broadening_only_requests_fields_every_rule_carries(self) -> None:
        """고른 규칙이 모두 갖고 있는 필드만 넓힌 조회에 넣는다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="MULTIPLE_RULES",
                    requested_fields=["value", "unit"],
                    missing_scope=["RULE_TOPIC"],
                )
            ]
        )
        planner = LocalQueryPlanner(client)
        outcome = planner.plan("몇 학점이야?")
        requested = set(outcome.plan.requested_fields)
        self.assertIn("description_ko", requested)
        presence = planner.context["rule_field_presence"]
        for rule_id in outcome.plan.filters["rule_ids"]:
            self.assertTrue(requested.issubset(set(presence[rule_id])))

    def test_a_named_course_question_is_never_widened_to_rules(self) -> None:
        """질문이 과목을 지목했으면 이수요건 전체로 넓히지 않는다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="SINGLE_RULE",
                    requested_fields=["grade_year", "semester"],
                    missing_scope=["RULE_TOPIC"],
                )
            ]
            * 2
        )
        outcome = LocalQueryPlanner(client).plan("자료구조는 몇 학기에 개설되나?")
        self.assertIs(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertIsNone(outcome.plan)


class RelatedRuleTests(unittest.TestCase):
    def test_only_rules_worded_like_the_question_are_selected(self) -> None:
        """넓힌 조회가 묻지 않은 요건까지 쏟아내지 않아야 한다."""

        planner = LocalQueryPlanner(SequenceClient([]))
        related = planner._rules_related_to("균형교양 이수요건은?")
        texts = planner.context["rule_match_text"]
        self.assertTrue(related)
        self.assertLess(len(related), len(texts))
        for rule_id in related:
            self.assertIn("균형교양", texts[rule_id])

    def test_a_question_sharing_no_wording_keeps_every_rule(self) -> None:
        """겹치는 낱말이 없으면 추리지 않고 호출자가 전부 보여 주도록 둔다."""

        planner = LocalQueryPlanner(SequenceClient([]))
        self.assertEqual(planner._rules_related_to("zzz"), [])


class SettledScopeTests(unittest.TestCase):
    def test_a_scope_the_data_already_fixes_is_not_asked_again(self) -> None:
        """적재 후보가 하나뿐인 학과를 되묻지 않고 그 값으로 조회한다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="COURSE_LIST",
                    requested_fields=["name_ko", "credits"],
                    filters={"grade_year": 1},
                    missing_scope=["DEPARTMENT"],
                )
            ]
        )
        outcome = LocalQueryPlanner(client).plan("1학년 과목 알려줘")
        self.assertIs(outcome.status, PlanningStatus.READY)
        self.assertIn("department_id", outcome.plan.filters)

    def test_a_course_named_in_the_question_is_adopted_as_a_filter(self) -> None:
        """질문에 그대로 나온 과목명은 추정이 아니라 사용자가 말한 조건이다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="SINGLE_COURSE",
                    requested_fields=["grade_year", "semester"],
                    missing_scope=["COURSE_IDENTITY"],
                )
            ]
        )
        outcome = LocalQueryPlanner(client).plan("자료구조는 몇 학기에 개설되나?")
        self.assertIs(outcome.status, PlanningStatus.READY)
        self.assertEqual(outcome.plan.filters["name_ko"], "자료구조")

    def test_an_intent_the_data_cannot_settle_still_asks_back(self) -> None:
        """필터로 메울 수 없는 부족 코드는 되묻기로 남는다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="COURSE_LIST",
                    missing_scope=["QUESTION_INTENT"],
                )
            ]
            * 2
        )
        outcome = LocalQueryPlanner(client).plan("그거 알려줘")
        self.assertIs(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)

    def test_a_plan_that_ignores_the_named_course_is_not_accepted(self) -> None:
        """지목된 과목을 담지 않은 계획으로는 다른 것을 답하지 않는다."""

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="ROADMAP_LIST",
                    requested_fields=["course_name_ko"],
                    missing_scope=["DEPARTMENT"],
                )
            ]
            * 2
        )
        outcome = LocalQueryPlanner(client).plan("자료구조는 몇 학기에 개설되나?")
        self.assertIs(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertIsNone(outcome.plan)


class PlanningDiagnosticsTests(unittest.TestCase):
    def test_every_attempt_is_recorded_without_question_values(self) -> None:
        """계획 실패도 골격은 남기되 질문에서 온 값은 담지 않는다."""

        bad = payload(selection_mode="SINGLE_COURSE", filters={"credits": 3})
        client = SequenceClient([bad] * 3)
        with self.assertRaises(LLMResponseError) as caught:
            LocalQueryPlanner(client).plan("무엇이든 알려줘")
        attempts = caught.exception.attempts
        self.assertEqual(len(attempts), 3)
        for record in attempts:
            self.assertIs(record.outcome, AttemptOutcome.CONTRACT_REJECTED)
            self.assertEqual(record.selection_mode, "SINGLE_COURSE")
            self.assertEqual(record.filter_names, ("credits",))
            self.assertIn("SINGLE_COURSE", record.contract_error)
        # 골격만 남기므로 값은 어디에도 들어 있지 않다.
        self.assertNotIn("3", str(attempts[0].filter_names))

    def test_an_accepted_plan_records_its_attempt_too(self) -> None:
        client = SequenceClient(
            [
                payload(
                    selection_mode="EDUCATION_GOAL_LIST",
                    requested_fields=["description_ko", "goal_order"],
                )
            ]
        )
        outcome = LocalQueryPlanner(client).plan("교육목표 알려줘")
        self.assertIs(outcome.status, PlanningStatus.READY)
        self.assertEqual(len(outcome.attempts), 1)
        self.assertIs(outcome.attempts[0].outcome, AttemptOutcome.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
