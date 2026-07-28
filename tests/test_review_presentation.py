from __future__ import annotations

import unittest
from pathlib import Path

from src.normalizer.models import NormalizationResult
from src.reviewer.presentation import (
    build_review_presentation,
    correction_target_views,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_JSON = (
    PROJECT_ROOT / "data/processed/2022_curriculum_p128_130_normalized.json"
)


@unittest.skipUnless(NORMALIZED_JSON.exists(), "정규화 JSON이 필요합니다.")
class ReviewPresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = NormalizationResult.model_validate_json(
            NORMALIZED_JSON.read_text(encoding="utf-8")
        )
        cls.checks = {
            check.check_id: check for check in cls.result.validation_checks
        }

    def test_allocation_warning_uses_document_and_calculated_labels(self) -> None:
        presentation = build_review_presentation(
            self.checks["allocation_total:allocation:2022:전공:전공선택"]
        )

        self.assertEqual(
            [item.label for item in presentation.values],
            ["PDF에 적힌 총계", "학기별 학점 자동 합계", "두 값의 차이"],
        )
        self.assertEqual([item.value for item in presentation.values], [78, 54, 24])
        self.assertIn("원문 표", presentation.decision_question)

    def test_cross_section_warning_names_all_three_tables(self) -> None:
        presentation = build_review_presentation(
            self.checks["required_credit_cross_section"]
        )

        self.assertEqual(
            [item.label for item in presentation.values],
            ["전공교육과정표", "기본이수 학점구조표", "학기별 학점배분표"],
        )
        self.assertEqual([item.value for item in presentation.values], [21, 21, 24])

    def test_spelling_warning_shows_source_and_suggestion(self) -> None:
        check = self.checks["source_spelling_candidates"]
        presentation = build_review_presentation(check)
        targets = correction_target_views(self.result, check)

        self.assertEqual(
            [item.label for item in presentation.values],
            ["PDF 원문 표기", "수정 후보"],
        )
        self.assertEqual(
            presentation.values[1].value,
            ["윤리의식", "Architecture"],
        )
        self.assertEqual(
            [target.label for target in targets],
            ["인재상 문장", "영문 과목명"],
        )
        self.assertTrue(all(target.current_value for target in targets))


if __name__ == "__main__":
    unittest.main()
