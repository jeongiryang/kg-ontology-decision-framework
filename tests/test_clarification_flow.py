"""되묻기가 커버리지를 넓히면서 정확성을 깎지 않는지 고정한다.

이 파일이 지키는 명제는 하나다. **되묻기를 거쳐도 적재되지 않은 값은 계획에 들어갈 수
없다.** 선택지는 서버가 데이터에서 만들고, 사용자가 되돌려 보낸 값은 같은 선택지를 다시
만들어 대조한 뒤에만 받아들인다. 그래서 대화가 이어져도 근거 계약이 느슨해지지 않는다.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from kg_builder.answer.contracts import (
    ChatResponse,
    ChatStatus,
    ClarificationOption,
    clarification_message,
)
from kg_builder.query.clarification import (
    MAX_OPTIONS,
    MISSING_CODES,
    ClarificationChoices,
)
from kg_builder.query.fact_index import FactIndex, vocabulary_labels

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = json.loads(
    (ROOT / "data/verified/2026/2026_curriculum_kg_data.json").read_text(encoding="utf-8")
)
SPEC = json.loads((ROOT / "ontology/ontology_spec.json").read_text(encoding="utf-8"))


def _choices() -> ClarificationChoices:
    index = FactIndex.from_bundle(BUNDLE, vocabulary_labels(SPEC))
    return ClarificationChoices(BUNDLE, index, SPEC)


def _bundle_values(name: str, label: str) -> set[Any]:
    return {
        node["properties"][name]
        for node in BUNDLE["nodes"]
        if label in node["labels"] and node["properties"].get(name) is not None
    }


class ChoiceSourceTests(unittest.TestCase):
    """선택지의 값과 표기는 모두 적재 데이터에서 나와야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.choices = _choices()

    def test_department_choices_come_from_the_bundle(self) -> None:
        offered = self.choices.for_missing("전공필수 과목은?", ["DEPARTMENT"])
        self.assertTrue(offered)
        loaded = _bundle_values("department_id", "Department")
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertEqual(choice.filter_name, "department_id")
                self.assertIn(choice.value, loaded)
                self.assertTrue(choice.label.strip())

    def test_academic_year_choices_come_from_the_bundle(self) -> None:
        offered = self.choices.for_missing("교양 학점은?", ["ACADEMIC_YEAR"])
        self.assertTrue(offered)
        loaded = _bundle_values("academic_year", "CurriculumVersion")
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertIn(choice.value, loaded)

    def test_course_choices_are_real_course_codes(self) -> None:
        offered = self.choices.for_missing("자료구조 알려줘", ["COURSE_IDENTITY"])
        self.assertTrue(offered)
        loaded = _bundle_values("course_code", "Course")
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertEqual(choice.filter_name, "course_code")
                self.assertIn(choice.value, loaded)

    def test_rule_choices_show_verified_wording(self) -> None:
        """규칙 선택지는 검증된 원문을 보여 준다. 새로 요약하지 않는다."""

        offered = self.choices.for_missing("교양 이수요건은?", ["RULE_TOPIC"])
        self.assertTrue(offered)
        wording = {
            node["properties"]["description_ko"]
            for node in BUNDLE["nodes"]
            if "Rule" in node["labels"]
            and isinstance(node["properties"].get("description_ko"), str)
        }
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertEqual(choice.filter_name, "rule_ids")
                stem = choice.label.rstrip("…")
                self.assertTrue(
                    any(text.startswith(stem) for text in wording),
                    "선택지 문구가 검증된 원문에서 오지 않았다",
                )

    def test_intent_choices_are_declared_selection_modes(self) -> None:
        from kg_builder.query.fact_families import SelectionMode

        offered = self.choices.for_missing("인재상이 뭐야?", ["QUESTION_INTENT"])
        self.assertTrue(offered)
        declared = {mode.value for mode in SelectionMode}
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertEqual(choice.filter_name, "selection_mode")
                self.assertIn(choice.value, declared)

    def test_choice_count_is_capped(self) -> None:
        for code in MISSING_CODES:
            with self.subTest(code=code):
                offered = self.choices.for_missing("교양 이수요건은?", [code])
                self.assertLessEqual(len(offered), MAX_OPTIONS)

    def test_only_one_scope_is_asked_at_a_time(self) -> None:
        """여러 개가 부족해도 한 번에 하나만 묻는다."""

        offered = self.choices.for_missing(
            "전공필수 과목은?", ["DEPARTMENT", "ACADEMIC_YEAR"]
        )
        self.assertTrue(offered)
        self.assertEqual({choice.filter_name for choice in offered}, {"department_id"})


class ResolvedValueTests(unittest.TestCase):
    """사용자가 되돌려 보낸 값은 제시했던 선택지일 때만 받아들인다."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.choices = _choices()

    def test_offered_values_are_accepted(self) -> None:
        offered = self.choices.for_missing("전공필수 과목은?", ["DEPARTMENT"])
        for choice in offered:
            with self.subTest(value=choice.value):
                self.assertTrue(
                    self.choices.is_offered(
                        "전공필수 과목은?", choice.filter_name, choice.value
                    )
                )

    def test_invented_values_are_rejected(self) -> None:
        """적재되지 않은 값은 요청에 실려 와도 거부돼야 한다."""

        for filter_name, value in (
            ("department_id", "department:cwnu:eee"),
            ("course_code", "ZZZ9999"),
            ("selection_mode", "SINGLE_RULE_FORGED"),
            ("academic_year", 2099),
        ):
            with self.subTest(filter=filter_name):
                self.assertFalse(
                    self.choices.is_offered("전공필수 과목은?", filter_name, value)
                )

    def test_values_offered_for_another_question_are_rejected(self) -> None:
        """질문이 달라지면 그 질문에서 제시했을 값만 인정한다."""

        offered = self.choices.for_missing("자료구조 알려줘", ["COURSE_IDENTITY"])
        self.assertTrue(offered)
        stale = offered[0]
        self.assertFalse(
            self.choices.is_offered("교양 최소 이수학점은?", stale.filter_name, stale.value)
        )


class ClarificationResponseTests(unittest.TestCase):
    """되묻기 응답은 답변 응답과 섞이지 않아야 한다."""

    def test_options_ride_only_on_clarification(self) -> None:
        response = ChatResponse.clarification_required(
            "req-1",
            "어느 학과를 말씀하시나요?",
            ["DEPARTMENT"],
            [ClarificationOption("department_id", "department:cwnu:cse", "컴퓨터공학과")],
        )
        self.assertIs(response.status, ChatStatus.CLARIFICATION_REQUIRED)
        wire = response.to_dict()
        self.assertEqual(wire["missing"], ["DEPARTMENT"])
        self.assertEqual(wire["options"][0]["value"], "department:cwnu:cse")

    def test_other_statuses_carry_no_options(self) -> None:
        for factory in (
            ChatResponse.out_of_scope,
            ChatResponse.not_found,
            ChatResponse.unsupported,
        ):
            with self.subTest(factory=factory.__name__):
                wire = factory("req-2").to_dict()
                self.assertEqual(wire["options"], [])
                self.assertEqual(wire["missing"], [])

    def test_question_wording_follows_the_offered_choices(self) -> None:
        """문구는 부족 코드가 아니라 실제로 제시하는 선택지를 따라간다.

        고를 것이 없어 "무엇을 알고 싶은지"로 되돌아간 경우, 문구까지 원래 코드를
        따라가면 화면과 선택지가 어긋난다.
        """

        department = ClarificationOption(
            "department_id", "department:cwnu:cse", "컴퓨터공학과"
        )
        intent = ClarificationOption(
            "selection_mode", "TALENT_PROFILE_LIST", "전공 인재상"
        )
        self.assertEqual(
            clarification_message(["DEPARTMENT"], [department]),
            "어느 학과를 말씀하시나요?",
        )
        # 부족 코드는 이수요건인데 실제 선택지는 "무엇을 알고 싶은지"인 경우
        self.assertEqual(
            clarification_message(["RULE_TOPIC"], [intent]),
            "무엇을 알고 싶으신가요?",
        )
        # 선택지가 없으면 종전대로 무엇이 부족한지 알린다
        self.assertIn("학과", clarification_message(["DEPARTMENT"], []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
