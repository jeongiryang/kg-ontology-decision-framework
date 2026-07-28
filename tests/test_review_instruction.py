from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from main import main
from src.normalizer.models import NormalizationResult
from src.reviewer import propose_corrections_from_instruction


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NORMALIZED_JSON = (
    PROJECT_ROOT / "data/processed/2022_curriculum_p128_130_normalized.json"
)


@unittest.skipUnless(NORMALIZED_JSON.exists(), "정규화 JSON이 필요합니다.")
class NaturalLanguageCorrectionProposalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = NormalizationResult.model_validate_json(
            NORMALIZED_JSON.read_text(encoding="utf-8")
        )

    def test_proposes_one_spelling_correction(self) -> None:
        proposal = propose_corrections_from_instruction(
            self.result,
            "운리의식을 윤리의식으로 수정해줘",
            "tester",
        )

        self.assertEqual(len(proposal.corrections), 1)
        correction = proposal.corrections[0]
        self.assertEqual(
            correction.target_uid,
            "profile:2022:computer-engineering:talent_profile:3",
        )
        self.assertEqual(correction.field, "text")
        self.assertIn("윤리의식", correction.value)
        self.assertNotIn("운리의식", correction.value)

    def test_proposes_all_known_spelling_candidates(self) -> None:
        proposal = propose_corrections_from_instruction(
            self.result,
            "오탈자 후보를 추천값으로 모두 수정해줘",
            "tester",
        )

        self.assertEqual(len(proposal.corrections), 2)
        self.assertEqual(
            {correction.target_uid for correction in proposal.corrections},
            {
                "profile:2022:computer-engineering:talent_profile:3",
                "course:CDA0016",
            },
        )

    def test_proposes_unambiguous_numeric_correction(self) -> None:
        proposal = propose_corrections_from_instruction(
            self.result,
            "전공선택 총계 78을 54로 수정해줘",
            "tester",
        )

        self.assertEqual(len(proposal.corrections), 1)
        correction = proposal.corrections[0]
        self.assertEqual(
            correction.target_uid,
            "allocation:2022:전공:전공선택",
        )
        self.assertEqual(correction.field, "declared_total")
        self.assertEqual(correction.value, 54)

    def test_rejects_instruction_without_change_intent(self) -> None:
        with self.assertRaisesRegex(ValueError, "수정 의도"):
            propose_corrections_from_instruction(
                self.result,
                "운리의식이 맞는지 확인해줘",
                "tester",
            )

    def test_rejects_keep_original_until_review_decision_is_supported(self) -> None:
        with self.assertRaisesRegex(ValueError, "원문 유지"):
            propose_corrections_from_instruction(
                self.result,
                "Architechture는 원문 유지해줘",
                "tester",
            )

    def test_rejects_value_outside_report_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "찾지 못했습니다"):
            propose_corrections_from_instruction(
                self.result,
                "컴퓨터공학과를 소프트웨어학과로 수정해줘",
                "tester",
            )

    def test_revise_command_records_and_applies_natural_language_instruction(
        self,
    ) -> None:
        with TemporaryDirectory() as directory:
            temp_dir = Path(directory)
            corrections = temp_dir / "corrections.json"
            output = temp_dir / "normalized.json"
            review = temp_dir / "review.md"
            exit_code = main(
                [
                    "revise",
                    "--pdf",
                    str(PROJECT_ROOT / "data/raw/2022 교육과정 -일부 발췌본.pdf"),
                    "--normalized",
                    str(NORMALIZED_JSON),
                    "--instruction",
                    "오탈자 후보를 추천값으로 모두 수정해줘",
                    "--reviewer",
                    "tester",
                    "--corrections-output",
                    str(corrections),
                    "--output",
                    str(output),
                    "--review-output",
                    str(review),
                ]
            )
            revised = NormalizationResult.model_validate_json(
                output.read_text(encoding="utf-8")
            )

        self.assertEqual(exit_code, 2)
        self.assertEqual(len(revised.corrections_applied), 2)
        statement = next(
            item
            for item in revised.curriculum.profile_statements
            if item.uid == "profile:2022:computer-engineering:talent_profile:3"
        )
        course = next(
            item
            for item in revised.curriculum.major_courses
            if item.code == "CDA0016"
        )
        self.assertIn("윤리의식", statement.text)
        self.assertIn("운리의식", statement.evidence.raw_text)
        self.assertEqual(course.name_en, "Computer Architecture")
        self.assertIn("Architechture", course.evidence.raw_text)
        checks = {check.check_id: check for check in revised.validation_checks}
        self.assertEqual(checks["source_spelling_candidates"].status, "pass")
        self.assertEqual(checks["profile_text_cross_parser"].status, "pass")
        self.assertEqual(revised.validation_status, "warning")


if __name__ == "__main__":
    unittest.main()
