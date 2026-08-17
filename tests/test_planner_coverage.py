"""계획 단계가 확신 없이 답하지 않는지 고정한다.

이 파일이 지키는 것은 네 가지다.

- 좁히지 못한 질문은 넓혀서 답하지 않고 **고를 수 있는 선택지로 되묻는다.**
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
    """모드는 분류가 아니라 역산으로 고친다.

    1순위는 요청 필드다. 어느 필드가 어느 family 소유인지는 온톨로지가 이미 정해
    두었으므로, 담을 수 있는 모드가 하나뿐이면 질문을 보지 않고 고친다. family 가
    늘면서 필드만으로 좁혀지지 않는 경우가 생겼고, 그때만 질문 표기와 적재 사실을
    대조해 동점을 깬다.
    """

    def setUp(self) -> None:
        self.planner = LocalQueryPlanner(SequenceClient([]))

    def test_a_named_course_outranks_a_stray_requested_field(self) -> None:
        """모델이 종류는 맞고 필드를 틀렸을 때, 필드가 종류를 뒤집으면 안 된다.

        `자료구조는 몇 학년 몇 학기에 개설되나?` 에 계획 모델이 `SINGLE_COURSE` 와
        권장 교양 과목 전용 필드(`recommended_*`)를 함께 냈다. 필드를 믿는 보정이
        계획을 권장 과목 조회로 바꿔 놓아, 묻지 않은 사실을 답하게 됐다. 3회 시도가
        모두 같아 재시도로도 벗어나지 못했다(2026-08-15 실측).

        질문이 적재된 과목을 이름으로 지목했고 모델도 과목 조회라고 했다면, 신호 둘이
        일치하므로 필드 하나에 지지 않는다.
        """

        kept = self.planner._mode_for_fields(
            "자료구조는 몇 학년 몇 학기에 개설되나?",
            SelectionMode.SINGLE_COURSE.value,
            ["recommended_grade_year", "recommended_semester"],
        )
        self.assertEqual(kept, SelectionMode.SINGLE_COURSE.value)

    def test_the_guard_needs_both_signals(self) -> None:
        """과목을 지목하지 않은 질문은 종전대로 필드로 고친다."""

        corrected = self.planner._mode_for_fields(
            "1학년 1학기 권장 교과목은?",
            SelectionMode.SINGLE_COURSE.value,
            ["recommended_grade_year", "recommended_semester"],
        )
        self.assertNotEqual(corrected, SelectionMode.SINGLE_COURSE.value)

    def test_fields_owned_by_one_family_correct_the_mode(self) -> None:
        """요청 필드를 담을 수 있는 모드가 하나뿐이면 그 모드로 고친다."""

        corrected = self.planner._mode_for_fields(
            "", SelectionMode.SINGLE_COURSE.value, ["description_ko", "profile_order"]
        )
        self.assertEqual(corrected, SelectionMode.TALENT_PROFILE_LIST.value)

    def test_a_mode_that_already_fits_is_left_alone(self) -> None:
        corrected = self.planner._mode_for_fields(
            "", SelectionMode.SINGLE_COURSE.value, ["grade_year", "semester"]
        )
        self.assertEqual(corrected, SelectionMode.SINGLE_COURSE.value)

    def test_ambiguous_fields_stay_put_without_a_matching_question(self) -> None:
        """질문이 아무 사실도 가리키지 않으면 동점을 깨지 않는다."""

        corrected = self.planner._mode_for_fields(
            "", SelectionMode.SINGLE_RULE.value, ["description_ko"]
        )
        self.assertEqual(corrected, SelectionMode.SINGLE_RULE.value)

    def test_question_wording_breaks_the_tie_between_families(self) -> None:
        """여러 family 가 같은 필드를 쓰면 질문과 적재 사실의 대조로 고른다.

        ``description_ko`` 는 인재상·교육목표·역량이 함께 쓰는 필드라 필드만으로는
        모드가 정해지지 않는다. 종전에는 이때 손대지 않아 잘못된 모드가 그대로
        내려갔다.
        """

        corrected = self.planner._mode_for_fields(
            "학과 인재상이 뭐야?", SelectionMode.SINGLE_COURSE.value, ["description_ko"]
        )
        self.assertEqual(corrected, SelectionMode.TALENT_PROFILE_LIST.value)


class UnresolvedRuleTopicTests(unittest.TestCase):
    """어느 이수요건인지 못 고르면 넓혀서 답하지 않고 선택지로 되묻는다.

    종전에는 관련 요건을 모아 한꺼번에 보여 줬다(넓히기). 근거는 붙어 있었지만 묻지
    않은 요건이 섞이고, 뜻 없는 입력에는 규칙집 전체가 나왔다. 되묻기가 생긴 뒤로는
    같은 후보를 **고를 수 있게** 주는 편이 낫다. 넓히기 경로는 제거했다.
    """

    def test_unresolved_rule_topic_asks_back_with_choices(self) -> None:
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
        outcome = LocalQueryPlanner(client).plan("교양 이수요건은?")
        self.assertIs(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertIsNone(outcome.plan)
        self.assertTrue(outcome.options, "고를 수 있는 이수요건을 제시해야 한다")
        self.assertEqual(
            {choice.filter_name for choice in outcome.options}, {"rule_ids"}
        )

    def test_a_meaningless_question_is_out_of_scope_not_a_menu(self) -> None:
        """뜻 없는 입력에는 규칙집도, 되묻기 메뉴도 주지 않는다.

        종전에는 이런 입력도 되묻기로 넘어갔고, 되묻기가 선언된 fact family 전체를
        목록으로 내주는 바람에 하나만 고르면 근거가 붙은 답변까지 갔다. 답에 쓰인
        사실은 진짜였지만 **아무도 그것을 묻지 않았다.** 되물을 자격은 "계획이 서지
        않았다"가 아니라 "질문이 적재된 사실을 가리킨다"에서 나온다.
        """

        for question in ("ㅇㅇㄹㅇㄹㅇㄹ", "asdfasdf", "ㅋㅋㅋㅋㅋ"):
            with self.subTest(question=question):
                client = SequenceClient(
                    [
                        payload(
                            status="CLARIFICATION_REQUIRED",
                            selection_mode="SINGLE_RULE",
                            missing_scope=["RULE_TOPIC"],
                        )
                    ]
                )
                outcome = LocalQueryPlanner(client).plan(question)
                self.assertIs(outcome.status, PlanningStatus.OUT_OF_SCOPE)
                self.assertIsNone(outcome.plan)
                self.assertFalse(outcome.options, "고를 것을 주면 답까지 가 버린다")

    def test_clarification_always_offers_something_to_pick(self) -> None:
        """가리키는 사실이 있는 질문이면, 좁히지 못해도 고를 것을 준다.

        위 검사와 짝이다. 되묻기를 없앤 것이 아니라 **자격을 둔 것**이다.
        """

        client = SequenceClient(
            [
                payload(
                    status="CLARIFICATION_REQUIRED",
                    selection_mode="SINGLE_RULE",
                    missing_scope=["RULE_TOPIC"],
                )
            ]
        )
        outcome = LocalQueryPlanner(client).plan("알려줘 과목")
        self.assertIs(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertTrue(outcome.options)


class NamedCourseGuardTests(unittest.TestCase):
    def test_a_named_course_question_is_never_widened_to_rules(self) -> None:
        """질문이 과목을 지목했으면 이수요건 조회로 답하지 않는다."""

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
        """되묻기 선택지가 묻지 않은 요건까지 쏟아내지 않아야 한다."""

        planner = LocalQueryPlanner(SequenceClient([]))
        related = planner._rules_related_to("균형교양 이수요건은?")
        texts = planner.context["rule_match_text"]
        self.assertTrue(related)
        self.assertLess(len(related), len(texts))
        for rule_id in related:
            self.assertIn("균형교양", texts[rule_id])

    def test_a_question_sharing_no_wording_selects_nothing(self) -> None:
        """겹치는 낱말이 없으면 후보를 만들지 않는다. 전부 보여 주지 않는다."""

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
        # 가리키는 사실이 있으면서 무엇을 묻는지는 정해지지 않은 질문이어야 한다.
        outcome = LocalQueryPlanner(client).plan("교양 그거 알려줘")
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
        outcome = LocalQueryPlanner(client).plan("교양 학점 알려줘")
        # 시도를 다 써도 계획이 서지 않으면 되묻기로 끝난다. 진단 기록은 그대로다.
        self.assertIs(outcome.status, PlanningStatus.CLARIFICATION_REQUIRED)
        self.assertTrue(outcome.options)
        rejected = [
            record
            for record in outcome.attempts
            if record.outcome is AttemptOutcome.CONTRACT_REJECTED
        ]
        self.assertEqual(len(rejected), 3)
        for record in rejected:
            self.assertEqual(record.selection_mode, "SINGLE_COURSE")
            self.assertEqual(record.filter_names, ("credits",))
            # 어떤 계약에 걸렸는지는 남되, 문구 자체는 계획기 내부 사정이라 고정하지
            # 않는다. 여기서 지키는 것은 "이유가 기록된다"이지 특정 문장이 아니다.
            self.assertTrue(record.contract_error)
        # 골격만 남기므로 값은 어디에도 들어 있지 않다.
        self.assertNotIn("3", str(rejected[0].filter_names))

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
