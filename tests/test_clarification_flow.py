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
from kg_builder.llm.models import LLMGeneration, PlanningStatus
from kg_builder.llm.planner import MAX_AUTO_ADOPTED_CHOICES, LocalQueryPlanner
from kg_builder.query.clarification import (
    MAX_OPTIONS,
    _CLOSED_SET_CODES,
    MAX_ROUNDS,
    MISSING_CODES,
    ClarificationChoices,
)
from kg_builder.query.fact_families import SelectionMode
from kg_builder.query.fact_index import FactIndex, vocabulary_labels


class SequenceClient:
    """계획 모델을 부르지 않는 대역. 이 파일은 조립 규칙만 본다."""

    model = "stub"

    def __init__(self, payloads):
        self.payloads = list(payloads)

    def generate_json(self, *, system_prompt, user_prompt, response_schema):
        del system_prompt, user_prompt, response_schema
        return LLMGeneration(self.payloads.pop(0), 0.01, self.model)


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

    def test_a_scope_with_one_candidate_is_not_asked(self) -> None:
        """후보가 하나뿐인 범위는 되묻지 않는다.

        계획 단계가 이미 그 값으로 채우므로(`_complete_scope`), 물어봐야 사용자에게
        의미 없는 클릭만 시킨다. 기준 데이터에는 학과와 학년도가 하나씩이다.
        """

        self.assertEqual(len(_bundle_values("department_id", "Department")), 1)
        self.assertEqual(len(_bundle_values("academic_year", "CurriculumVersion")), 1)
        self.assertEqual(self.choices._choices_for("전공필수 과목은?", "DEPARTMENT"), ())
        self.assertEqual(self.choices._choices_for("교양 학점은?", "ACADEMIC_YEAR"), ())

    def test_scope_choices_come_from_the_bundle_when_there_are_several(self) -> None:
        """후보가 여럿이면 적재된 값과 표기를 그대로 제시한다."""

        bundle = {
            "nodes": BUNDLE["nodes"]
            + [
                {
                    "id": "department:test:eee",
                    "labels": ["Department"],
                    "properties": {
                        "department_id": "department:test:eee",
                        "name_ko": "전자공학과",
                    },
                }
            ],
            "relationships": BUNDLE["relationships"],
        }
        choices = ClarificationChoices(
            bundle, FactIndex.from_bundle(BUNDLE, vocabulary_labels(SPEC)), SPEC
        )
        offered = choices._choices_for("전공필수 과목은?", "DEPARTMENT")
        self.assertEqual(len(offered), 2)
        loaded = {
            node["properties"]["department_id"]
            for node in bundle["nodes"]
            if "Department" in node["labels"]
        }
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertEqual(choice.filter_name, "department_id")
                self.assertIn(choice.value, loaded)
                self.assertTrue(choice.label.strip())

    def test_course_choices_are_real_course_codes(self) -> None:
        offered = self.choices.for_missing("자료구조 알려줘", ["COURSE_IDENTITY"])
        self.assertTrue(offered)
        loaded = _bundle_values("course_code", "Course")
        for choice in offered:
            with self.subTest(label=choice.label):
                self.assertEqual(choice.filter_name, "course_code")
                self.assertIn(choice.value, loaded)

    def test_rule_choices_never_show_the_answer(self) -> None:
        """규칙 선택지 라벨은 답변 문장이면 안 된다.

        종전에는 검증된 원문(`description_ko`)을 그대로 라벨로 썼다. 그런데 규칙 답변도
        같은 `description_ko` 를 그대로 내보내므로, 라벨과 답이 글자까지 같아졌다.
        사용자는 답을 읽고 그것을 눌러 같은 문장을 다시 보게 된다. 고르는 행위가 정보를
        주지 못한다.

        라벨은 대신 "무엇에 대한 기준인가"를 말한다. 조각은 모두 선언된 표기다.
        """

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
                self.assertFalse(
                    any(text.startswith(stem) for text in wording),
                    "선택지 라벨이 답변 문장을 그대로 드러냈다",
                )

    def test_rule_choice_labels_come_from_declared_wording(self) -> None:
        """라벨의 각 조각은 명세와 적재 데이터가 선언한 표기여야 한다."""

        declared = {
            entry["description_ko"]
            for entry in SPEC["controlled_vocabularies"]["rule_type"]["values"]
        }
        declared |= {
            node["properties"]["name_ko"]
            for node in BUNDLE["nodes"]
            if "EducationArea" in node["labels"]
            and isinstance(node["properties"].get("name_ko"), str)
        }
        for node in BUNDLE["nodes"]:
            if "ApplicabilityScope" not in node["labels"]:
                continue
            for name in ("student_type", "college_category"):
                value = node["properties"].get(name)
                if isinstance(value, str) and value:
                    declared.add(value)
        offered = self.choices.for_missing("균형교양 이수요건은?", ["RULE_TOPIC"])
        self.assertTrue(offered)
        for choice in offered:
            for group in choice.label.split(" · "):
                for piece in group.split(", "):
                    with self.subTest(piece=piece):
                        self.assertTrue(
                            any(piece.endswith(text) or text in piece for text in declared),
                            f"선언되지 않은 표기가 라벨에 들어갔다: {piece}",
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
        """검색에서 온 후보는 상한을 넘지 않는다.

        스키마가 정한 닫힌 집합(`COURSE_ASPECT` 의 속성 목록)은 상한을 적용하지 않는다.
        검색 꼬리가 없어 자를 이유가 없고, 자르면 물을 수 있는 것이 화면에서 사라진다.
        """

        for code in MISSING_CODES:
            if code in _CLOSED_SET_CODES:
                continue
            with self.subTest(code=code):
                offered = self.choices.for_missing("교양 이수요건은?", [code])
                self.assertLessEqual(len(offered), MAX_OPTIONS)

    def test_only_one_scope_is_asked_at_a_time(self) -> None:
        """여러 개가 부족해도 한 번에 하나만 묻는다."""

        offered = self.choices.for_missing(
            "교양 이수요건은?", ["RULE_TOPIC", "QUESTION_INTENT"]
        )
        self.assertTrue(offered)
        self.assertEqual({choice.filter_name for choice in offered}, {"rule_ids"})


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


class RuleChoiceScopeTests(unittest.TestCase):
    """되묻기 선택지는 질문이 가리킨 범위 밖으로 나가면 안 된다.

    `균형교양 이수요건은?` 에 기초교양·대학영어 규칙이 함께 나왔다(2026-08-15 실측).
    한국어 2-gram 검색에서 `균형교양` 과 `기초교양` 이 `교양` 을 공유해 무관한 규칙도
    얼마간 점수를 받는데, 선택지 생성기가 자리 수만큼 채웠기 때문이다. 없앤 줄 알았던
    "넓히기"가 선택지 화면으로 자리를 옮겨 살아남아 있었다.

    이 묶음이 고정하는 것은 두 가지다.

    - 점수 꼬리를 자른다. 계획기가 쓰던 기준과 같은 것을 쓴다.
    - 질문이 영역을 말했으면 그 영역의 규칙만 남긴다.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.choices = _choices()

    def _labels(self, question: str) -> list[str]:
        return [
            choice.label
            for choice in self.choices.for_missing(question, ["RULE_TOPIC"])
        ]

    def _offered_rule_ids(self, question: str) -> set[str]:
        return {
            rule_id
            for choice in self.choices.for_missing(question, ["RULE_TOPIC"])
            for rule_id in choice.value
        }

    @staticmethod
    def _rules_under(area_id: str) -> set[str]:
        """Rule ids the bundle links to this area or any area inside it."""

        targeted: set[str] = set()
        for relationship in BUNDLE["relationships"]:
            if relationship["type"] != "TARGETS":
                continue
            target = relationship["to_id"]
            if target == area_id or target.startswith(area_id + ":"):
                targeted.add(relationship["from_id"])
        return targeted

    def test_an_area_question_only_offers_that_area(self) -> None:
        """제시한 규칙은 모두 질문이 말한 영역 안에 있어야 한다.

        라벨은 가장 구체적인 영역을 쓰므로(`기초교양` 이 아니라 `글로벌의사소통`)
        문자열이 아니라 그래프의 영역 관계로 확인한다.
        """

        for question, area_id in (
            ("균형교양 이수요건은?", "area:general:balanced"),
            ("기초교양 이수요건은?", "area:general:foundational"),
        ):
            with self.subTest(question=question):
                offered = self._offered_rule_ids(question)
                self.assertTrue(offered, "고를 것은 있어야 한다")
                inside = self._rules_under(area_id)
                # `TARGETS` 가 없어 원문으로만 영역을 아는 규칙이 있다. 그 규칙은
                # 원문이 그 영역 이름을 말하고 있어야 한다.
                wording = {
                    node["properties"]["rule_id"]: node["properties"].get(
                        "description_ko", ""
                    )
                    for node in BUNDLE["nodes"]
                    if "Rule" in node["labels"]
                }
                area_name = next(
                    node["properties"]["name_ko"]
                    for node in BUNDLE["nodes"]
                    if node["properties"].get("area_id") == area_id
                )
                for rule_id in offered:
                    self.assertTrue(
                        rule_id in inside or area_name in wording.get(rule_id, ""),
                        f"질문한 영역 밖의 규칙이 제시됐다: {rule_id}",
                    )

    def test_a_major_area_is_named_without_the_department_prefix(self) -> None:
        """적재된 영역 이름은 `컴퓨터공학 전공필수` 지만 사용자는 학과를 빼고 묻는다."""

        offered = self._offered_rule_ids("전공필수 이수요건은?")
        self.assertTrue(offered)
        inside = self._rules_under("area:major:cse:required")
        self.assertTrue(offered <= inside, "전공필수 밖의 규칙이 섞였다")

    def test_a_broad_question_is_not_narrowed(self) -> None:
        """질문이 영역을 좁히지 않았으면 선택지도 좁히지 않는다."""

        broad = self._labels("교양 이수요건은?")
        narrow = self._labels("균형교양 이수요건은?")
        self.assertGreater(len(broad), len(narrow))

    def test_the_score_tail_is_cut(self) -> None:
        """최상위 점수에서 멀어진 후보로 자리를 채우지 않는다."""

        from kg_builder.query.fact_index import SCORE_RATIO

        found = self.choices._index.search(
            "균형교양 이수요건은?", limit=MAX_OPTIONS * 3, labels={"Rule"}
        )
        self.assertGreater(len(found), MAX_OPTIONS, "자를 꼬리가 있어야 검사가 성립한다")
        kept = len(self._labels("균형교양 이수요건은?"))
        threshold = found[0].score * SCORE_RATIO
        near = sum(1 for candidate in found if candidate.score >= threshold)
        self.assertLessEqual(kept, near)

    def test_offered_rules_stay_verifiable(self) -> None:
        """좁혀도 값 검증은 그대로다. 제시한 값만 되돌려 받는다."""

        offered = self.choices.for_missing("균형교양 이수요건은?", ["RULE_TOPIC"])
        for choice in offered:
            with self.subTest(value=choice.value):
                self.assertTrue(
                    self.choices.is_offered(
                        "균형교양 이수요건은?", choice.filter_name, choice.value
                    )
                )


class ChainedClarificationTests(unittest.TestCase):
    """되묻기는 답할 수 있는 상태가 될 때까지 타고 들어가야 한다.

    한 번 물어보고 끝나면, 조회 종류만 고른 상태처럼 아직 좁혀지지 않은 자리에서
    사용자가 더 갈 곳을 잃는다. 반대로 무조건 타고 들어가면 이미 충분한 질문까지
    붙잡는다. 이 묶음이 고정하는 것은 그 사이의 경계다.

    - 앞에서 고른 값은 다음 선택지를 **좁힌다**.
    - 고른 값으로 답할 수 없는 선택지는 **내놓지 않는다**.
    - 뒤의 좁은 선택이 앞의 넓은 조회 종류를 **고쳐 준다**.
    - 조회 종류는 후보가 하나여도 **대신 골라 주지 않는다**.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.choices = _choices()
        cls.planner = LocalQueryPlanner(SequenceClient([]))

    def test_a_picked_kind_narrows_the_next_choices(self) -> None:
        """조회 종류를 고르면 다음 선택지는 그 종류가 쓸 수 있는 것만 남는다."""

        first = self.choices.for_missing("과목 알려줘", ["QUESTION_INTENT"])
        self.assertTrue(first)
        self.assertEqual({choice.filter_name for choice in first}, {"selection_mode"})

        rules = self.choices.for_missing(
            "과목 알려줘", ["QUESTION_INTENT"], {"selection_mode": "MULTIPLE_RULES"}
        )
        self.assertTrue(rules, "조회 종류를 고른 뒤에도 이어서 고를 것이 있어야 한다")
        self.assertEqual({choice.filter_name for choice in rules}, {"rule_ids"})

    def test_dead_end_choices_are_not_offered(self) -> None:
        """규칙 조회를 고른 뒤 과목 선택지를 내놓지 않는다.

        골라도 답에 닿지 못하는 선택지는 사용자를 막다른 곳으로 보낸다.
        """

        offered = self.choices._choices_for(
            "자료구조 알려줘", "COURSE_IDENTITY", {"selection_mode": "MULTIPLE_RULES"}
        )
        self.assertEqual(offered, ())

    def test_a_settled_scope_is_not_asked_again(self) -> None:
        """이미 고른 자리는 다시 묻지 않는다. 같은 화면이 반복되면 끝나지 않는다."""

        picked = self.choices.for_missing("교양 이수요건은?", ["RULE_TOPIC"])
        self.assertTrue(picked)
        resolved = {picked[0].filter_name: picked[0].value}
        again = self.choices.for_missing("교양 이수요건은?", ["RULE_TOPIC"], resolved)
        self.assertNotIn("rule_ids", {choice.filter_name for choice in again})

    def test_one_picked_rule_narrows_the_kind_to_a_single_rule(self) -> None:
        """`학사규칙`을 고른 뒤 규칙 하나를 고르면 그것은 이미 단일 규칙 조회다.

        앞서 고른 MULTIPLE_RULES 를 그대로 두면 계획 계약이 "규칙 두 개 이상"을
        요구해 깨지고, 사용자는 다 골랐는데도 답을 받지 못한다.
        """

        narrowed = LocalQueryPlanner._reconciled(
            {"selection_mode": "MULTIPLE_RULES", "rule_ids": ["rule:a"]}
        )
        self.assertEqual(narrowed["selection_mode"], SelectionMode.SINGLE_RULE.value)

        widened = LocalQueryPlanner._reconciled(
            {"selection_mode": "SINGLE_RULE", "rule_ids": ["rule:a", "rule:b"]}
        )
        self.assertEqual(widened["selection_mode"], SelectionMode.MULTIPLE_RULES.value)

    def test_reconciling_leaves_other_kinds_alone(self) -> None:
        """규칙 조회가 아닌 선택은 개수로 고쳐 쓰지 않는다."""

        resolved = {"selection_mode": "COURSE_LIST", "rule_ids": ["rule:a"]}
        self.assertEqual(LocalQueryPlanner._reconciled(resolved), resolved)
        self.assertEqual(LocalQueryPlanner._reconciled({}), {})

    def test_the_query_kind_is_never_picked_for_the_user(self) -> None:
        """후보가 하나뿐이어도 무엇을 묻는지는 사용자가 정한다.

        하나로 좁혀진 것은 그 종류로만 답할 수 있다는 뜻이 아니라 검색이 약하게
        걸렸다는 뜻일 수 있다.
        """

        only = ClarificationOption("selection_mode", "TALENT_PROFILE_LIST", "인재상")
        self.assertFalse(self.planner._safe_to_adopt("인재상 알려줘", only))

    def test_a_lone_entity_choice_is_adopted_without_asking(self) -> None:
        """개체가 하나뿐이면 되묻지 않고 그 값으로 조회한다."""

        only = ClarificationOption("course_code", "GEA8617", "자료구조(GEA8617)")
        self.assertTrue(self.planner._safe_to_adopt("자료구조 알려줘", only))

    def test_the_chain_is_bounded(self) -> None:
        """되묻기는 무한히 이어지지 않는다."""

        self.assertGreaterEqual(MAX_ROUNDS, 2)
        self.assertLessEqual(MAX_ROUNDS, 4)
        self.assertLessEqual(MAX_AUTO_ADOPTED_CHOICES, MAX_ROUNDS)
        planner = LocalQueryPlanner(SequenceClient([]))
        planner._resolved = {f"filter{index}": index for index in range(MAX_ROUNDS)}
        self.assertEqual(
            planner._options_for(
                "교양 이수요건은?", (), PlanningStatus.CLARIFICATION_REQUIRED
            ),
            (),
        )


class SettledPlanTests(unittest.TestCase):
    """다 고른 뒤에는 조회할 수 있는 계획이 나와야 한다.

    되묻기 payload 는 계획 모델이 계획을 접은 껍데기라 필드가 비었거나 앞 시도의
    잔재만 남는다. 사용자가 고른 값으로 조회가 정해졌는데 그 껍데기 때문에 계약이
    깨지면, 선택지를 끝까지 눌러도 답이 나오지 않는다.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.choices = _choices()

    def _planner(self, resolved: dict[str, Any]) -> LocalQueryPlanner:
        planner = LocalQueryPlanner(SequenceClient([]))
        planner._resolved = LocalQueryPlanner._reconciled(resolved)
        return planner

    def test_a_picked_rule_survives_an_empty_clarification_payload(self) -> None:
        rule_id = next(
            node["properties"]["rule_id"]
            for node in BUNDLE["nodes"]
            if "Rule" in node["labels"]
        )
        planner = self._planner(
            {"selection_mode": "MULTIPLE_RULES", "rule_ids": [rule_id]}
        )
        settled = planner._normalise(
            "과목 알려줘",
            {"status": "CLARIFICATION_REQUIRED", "missing_scope": ["COURSE_IDENTITY"]},
        )
        self.assertEqual(settled["selection_mode"], SelectionMode.SINGLE_RULE.value)
        self.assertEqual(settled["filters"]["rule_ids"], [rule_id])
        self.assertTrue(settled["requested_fields"], "요청 필드가 비면 조회할 수 없다")

    def test_a_picked_kind_is_not_overridden_by_leftover_fields(self) -> None:
        """껍데기에 남은 앞 시도의 필드로 사용자의 선택을 뒤집지 않는다."""

        planner = self._planner({"selection_mode": "COURSE_LIST"})
        settled = planner._normalise(
            "2026학년도 전공필수 학점 얼마야?",
            {
                "status": "READY",
                "selection_mode": "TALENT_PROFILE_LIST",
                "filters": {
                    "academic_year": 2026,
                    "department_id": "department:cwnu:cse",
                    "completion_type": "MAJOR_REQUIRED",
                },
                "requested_fields": ["credits", "profile_order"],
            },
        )
        self.assertEqual(settled["selection_mode"], SelectionMode.COURSE_LIST.value)

    def test_fields_the_mode_cannot_return_are_dropped(self) -> None:
        """고른 모드가 줄 수 없는 필드는 조회문을 만들 자리가 없다."""

        planner = self._planner({"course_code": "GEA8617"})
        settled = planner._normalise(
            "자료구조 알려줘",
            {
                "status": "READY",
                "selection_mode": "SINGLE_COURSE",
                "filters": {
                    "academic_year": 2026,
                    "department_id": "department:cwnu:cse",
                    "course_code": "GEA8617",
                },
                # description_ko 는 Rule 전용 필드다.
                "requested_fields": ["credits", "description_ko"],
            },
        )
        self.assertNotIn("description_ko", settled["requested_fields"])

    def test_a_course_list_can_always_name_its_courses(self) -> None:
        """과목 목록은 과목명이 있어야 답이 된다."""

        planner = self._planner({"selection_mode": "COURSE_LIST"})
        settled = planner._normalise(
            "2026학년도 전공필수 학점 얼마야?",
            {
                "status": "READY",
                "selection_mode": "COURSE_LIST",
                "filters": {
                    "academic_year": 2026,
                    "department_id": "department:cwnu:cse",
                    "completion_type": "MAJOR_REQUIRED",
                },
                "requested_fields": ["credits"],
            },
        )
        self.assertIn("name_ko", settled["requested_fields"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
