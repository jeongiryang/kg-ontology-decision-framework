"""근거가 붙은 사실은 모두 질의로 도달할 수 있어야 한다는 계약을 검사한다.

이 파일이 지키는 명제는 하나다. **VERIFIED 사실에 VERIFIED Evidence 가 직접 붙어 있으면
그 사실을 주어로 하는 답변 경로가 있어야 한다.** 반대로 상태가 검증되지 않은 사실은
경로가 있어도 답변에 쓰이지 않아야 한다.

경로가 없는 라벨이 새로 적재되면 이 테스트가 실패한다. 그때 할 일은 테스트를 고치는
것이 아니라 family 를 선언하거나, 왜 답변 대상이 아닌지를 여기 예외로 남기는 것이다.
"""

from __future__ import annotations

import json
import unittest
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from kg_builder.answer.korean_renderer import _has_final_consonant, _particle
from kg_builder.answer.plan_cli import default_bundle_path
from kg_builder.query.fact_families import (
    EXTENDED_FAMILIES,
    SelectionMode,
    family_for_mode,
    family_for_result,
)
from kg_builder.query.fact_index import FactIndex, tokenize, vocabulary_labels

BUNDLE_PATH = default_bundle_path()
BUNDLE = json.loads(BUNDLE_PATH.read_text(encoding="utf-8"))
NODES_BY_ID: dict[str, Mapping[str, Any]] = {node["id"]: node for node in BUNDLE["nodes"]}

# 기존 두 family 가 담당하는 라벨. 선언형 family 목록에는 없지만 답변 경로는 있다.
BASE_FAMILY_LABELS = frozenset({"Rule", "CourseOffering"})
# 답변의 주어가 되지 않는 라벨. 근거가 붙어 있어도 그 자체를 묻는 질문이 성립하지
# 않거나, 다른 사실의 부속 정보로만 쓰인다.
NON_SUBJECT_LABELS = frozenset(
    {
        # 원문 정정 기록은 정정 대상 사실을 통해 드러나야 하며, 그 자체가 학사 질문의
        # 답이 되지 않는다.
        "CorrectionRecord",
        # Course 는 CourseOffering 을 통해서만 답변에 들어간다. 개설 정보 없이
        # 과목만 답하면 학년·학기·이수구분이 빠진 답이 된다.
        "Course",
    }
)


def _verified_evidence_ids() -> set[str]:
    return {
        node["id"]
        for node in BUNDLE["nodes"]
        if "Evidence" in node["labels"]
        and node["properties"].get("verification_status") == "VERIFIED"
    }


def _grounded_nodes() -> set[str]:
    """VERIFIED 사실 중 VERIFIED Evidence 가 직접 붙은 노드 ID 를 모은다."""

    evidence = _verified_evidence_ids()
    return {
        relationship["from_id"]
        for relationship in BUNDLE["relationships"]
        if relationship["type"] == "SUPPORTED_BY"
        and relationship["to_id"] in evidence
        and NODES_BY_ID.get(relationship["from_id"], {})
        .get("properties", {})
        .get("status")
        == "VERIFIED"
    }


def _grounded_facts() -> dict[str, set[str]]:
    """근거가 붙은 VERIFIED 노드를 라벨별로 모은다. 한 노드가 여러 라벨에 들어간다."""

    grounded: dict[str, set[str]] = defaultdict(set)
    for node_id in _grounded_nodes():
        for label in NODES_BY_ID[node_id]["labels"]:
            if label != "Evidence":
                grounded[label].add(node_id)
    return grounded


class GroundedCoverageTests(unittest.TestCase):
    def test_every_grounded_fact_has_an_answer_path(self) -> None:
        """근거가 붙은 VERIFIED 사실은 하나도 빠짐없이 답변 경로를 가져야 한다.

        판정은 노드 단위다. ``CreditRequirement`` 처럼 ``Rule`` 을 함께 달고 있는
        하위 유형은 Rule family 로 도달하므로 별도 family 가 필요하지 않다. 라벨
        단위로 세면 이런 노드가 미도달로 잘못 잡힌다.
        """

        reachable = {family.fact_label for family in EXTENDED_FAMILIES.values()}
        reachable |= BASE_FAMILY_LABELS | NON_SUBJECT_LABELS
        unreachable = defaultdict(int)
        for node_id in _grounded_nodes():
            labels = set(NODES_BY_ID[node_id]["labels"])
            if not labels & reachable:
                unreachable[" + ".join(sorted(labels))] += 1
        self.assertEqual(
            dict(unreachable),
            {},
            f"근거가 있는데 조회할 family 가 없는 사실: {dict(unreachable)}",
        )

    def test_non_subject_labels_are_not_silently_reachable(self) -> None:
        """답변 주어가 아니라고 선언한 라벨에 family 를 붙이면 선언을 고치게 한다."""

        declared = {family.fact_label for family in EXTENDED_FAMILIES.values()}
        self.assertEqual(
            declared & NON_SUBJECT_LABELS,
            set(),
            "답변 주어가 아니라고 적어 둔 라벨에 family 가 생겼다",
        )

    def test_review_required_facts_stay_out_of_answers(self) -> None:
        """검증되지 않은 사실은 근거가 붙어 있어도 조회 대상이 아니어야 한다.

        Cypher 스캐폴드가 ``status = 'VERIFIED'`` 를 강제하므로 실제 차단은 질의에서
        일어난다. 여기서는 그런 사실이 기준 데이터에 실제로 존재하는지 확인해, 이
        방어가 가정이 아니라 현재 데이터에서 의미를 갖는다는 점을 고정한다.
        """

        evidence = _verified_evidence_ids()
        unverified = [
            relationship["from_id"]
            for relationship in BUNDLE["relationships"]
            if relationship["type"] == "SUPPORTED_BY"
            and relationship["to_id"] in evidence
            and NODES_BY_ID.get(relationship["from_id"], {})
            .get("properties", {})
            .get("status")
            not in (None, "VERIFIED")
        ]
        self.assertTrue(
            unverified,
            "검증되지 않은 사실이 없다면 이 방어가 실제로 검사되지 않는다",
        )
        for family in EXTENDED_FAMILIES.values():
            with self.subTest(mode=family.selection_mode.value):
                self.assertIn(
                    "MATCH (f)-[:SUPPORTED_BY]->(e:Evidence)",
                    family.base_matches,
                    "모든 family 는 직접 근거 경로를 base MATCH 에 둬야 한다",
                )

    def test_every_declared_family_matches_real_data(self) -> None:
        """선언한 family 마다 기준 데이터에 실제 대상이 있어야 한다."""

        grounded = _grounded_facts()
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                self.assertTrue(
                    grounded.get(family.fact_label),
                    f"{family.fact_label} 에 근거가 붙은 VERIFIED 노드가 없다",
                )


class FamilyLookupTests(unittest.TestCase):
    """한 라벨이 여러 모드에 걸릴 때 모드가 조회의 유일한 키여야 한다."""

    def test_labels_are_shared_across_modes(self) -> None:
        by_label: dict[str, list[SelectionMode]] = defaultdict(list)
        for mode, family in EXTENDED_FAMILIES.items():
            by_label[family.fact_label].append(mode)
        shared = {label: modes for label, modes in by_label.items() if len(modes) > 1}
        self.assertTrue(
            shared,
            "라벨을 공유하는 모드가 없다면 모드 키 조회를 검사할 대상이 없다",
        )

    def test_family_for_result_rejects_mode_label_mismatch(self) -> None:
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                self.assertIs(family_for_result(mode, family.fact_label), family)
                self.assertIsNone(family_for_result(mode, "Evidence"))

    def test_family_for_mode_accepts_the_mode_string(self) -> None:
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                self.assertIs(family_for_mode(mode.value), family)


class DepartmentScopeTests(unittest.TestCase):
    """대학 소유 사실을 열어도 학과 범위 고정이 풀리지 않아야 한다."""

    def test_every_family_anchors_under_one_department(self) -> None:
        for mode, family in EXTENDED_FAMILIES.items():
            with self.subTest(mode=mode.value):
                self.assertEqual(
                    family.base_matches[0],
                    "MATCH (cv:CurriculumVersion)-[:FOR_DEPARTMENT]->(d:Department)",
                )
                self.assertIn("academic_year", family.required_filters)
                self.assertIn("department_id", family.required_filters)

    def test_institution_facts_are_reached_through_the_department(self) -> None:
        institution_modes = [
            mode
            for mode, family in EXTENDED_FAMILIES.items()
            if any(":Institution)" in match for match in family.base_matches)
        ]
        self.assertTrue(institution_modes, "대학 소유 family 가 하나도 없다")
        for mode in institution_modes:
            with self.subTest(mode=mode.value):
                matches = EXTENDED_FAMILIES[mode].base_matches
                self.assertIn(
                    "MATCH (d)-[:PART_OF]->(i:Institution)",
                    matches,
                    "대학 소유 사실은 학과를 거쳐서만 도달해야 한다",
                )


class FactIndexTests(unittest.TestCase):
    """검색이 조회 대상을 좁히되 근거 계약을 넓히지 않아야 한다."""

    @classmethod
    def setUpClass(cls) -> None:
        spec = json.loads(
            (Path(__file__).resolve().parents[1] / "ontology" / "ontology_spec.json")
            .read_text(encoding="utf-8")
        )
        cls.index = FactIndex.from_bundle(BUNDLE, vocabulary_labels(spec))

    def test_index_covers_exactly_the_grounded_facts(self) -> None:
        """색인 대상은 답변 대상과 같은 기준으로 잘려야 한다.

        답할 수 없는 사실을 검색이 떠올리면 계획이 그쪽으로 끌려가 결국 거절로 끝난다.
        """

        self.assertEqual(len(self.index), len(_grounded_nodes()))

    def test_bigrams_absorb_korean_particles(self) -> None:
        """조사가 붙어 표기가 달라져도 같은 토큰을 공유해야 한다."""

        for surface in ("교양은", "교양을", "교양의"):
            with self.subTest(surface=surface):
                self.assertIn("교양", tokenize(surface))

    def test_retrieval_finds_the_fact_family_the_question_names(self) -> None:
        """질문이 가리키는 사실 종류를 검색이 실제로 짚어야 한다.

        여기 적힌 질문들은 계획 모델이 반복해서 틀린 것들이다. 로그 0004 에서
        `인재상이 뭐야?` 는 세 번 모두 SINGLE_COURSE 로 분류됐다.
        """

        expected = {
            "인재상이 뭐야?": SelectionMode.TALENT_PROFILE_LIST,
            "전공능력별 과목 수와 학점은?": SelectionMode.CURRICULUM_AGGREGATE_LIST,
            "교육목표와 전공능력은 어떻게 연계되나?": (
                SelectionMode.GOAL_COMPETENCY_ALIGNMENT_LIST
            ),
            "최소전공학점제는 시행하나?": SelectionMode.CURRICULUM_AGGREGATE_LIST,
        }
        for question, mode in expected.items():
            with self.subTest(question=question):
                self.assertIn(
                    mode,
                    self.index.leading_modes(question)[:3],
                    "질문이 가리키는 모드가 상위 후보에 없다",
                )

    def test_unrelated_questions_score_far_below_real_ones(self) -> None:
        """검색은 좁히는 장치이지 답변을 승인하는 장치가 아니다.

        무관한 질문에도 낱말이 우연히 겹쳐 약한 후보가 나올 수 있다. 그것을 없애는 것이
        이 계층의 일이 아니다. 범위 밖 판정은 계획 단계와 근거 검증이 하고, 검색은
        "관련 있는 것이 훨씬 높은 점수를 받는다"만 지키면 된다. 이 간격이 무너지면
        동점을 깨는 신호로 쓸 수 없다.
        """

        related = self.index.search("컴퓨터공학과 전공필수 과목은?", limit=1)
        unrelated = self.index.search("오늘 날씨 어때?", limit=1)
        self.assertTrue(related, "관련 질문에 후보가 없다")
        best_unrelated = unrelated[0].score if unrelated else 0.0
        self.assertGreater(related[0].score, best_unrelated * 2)

    def test_ranking_is_deterministic(self) -> None:
        """같은 질문은 늘 같은 순위를 내야 측정이 의미를 갖는다."""

        question = "컴퓨터공학과 전공필수 과목은?"
        first = [candidate.fact_id for candidate in self.index.search(question)]
        second = [candidate.fact_id for candidate in self.index.search(question)]
        self.assertEqual(first, second)


class KoreanParticleTests(unittest.TestCase):
    """데이터에서 온 이름에 조사를 붙일 때 원문 표기를 훼손하지 않아야 한다."""

    def test_final_consonant_detection(self) -> None:
        for text, expected in (
            ("역량", True),
            ("전공능력", True),
            ("교육목표", False),
            ("주체적 창의력", True),
            ("피지컬AI", None),
            ("", None),
            ("3", None),
        ):
            with self.subTest(text=text):
                self.assertIs(_has_final_consonant(text), expected)

    def test_particle_selection(self) -> None:
        self.assertEqual(_particle("역량", "과", "와"), "과")
        self.assertEqual(_particle("교육목표", "은", "는"), "는")
        self.assertEqual(_particle("전공능력", "은", "는"), "은")

    def test_particle_keeps_both_forms_when_undecidable(self) -> None:
        """한글 음절이 아니면 판정하지 않는다. 틀린 조사보다 두 형태가 낫다."""

        self.assertEqual(_particle("Capstone", "과", "와"), "과(와)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
