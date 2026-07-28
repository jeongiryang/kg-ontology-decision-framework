from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from src.normalizer import normalize_curriculum_pdf, write_review_markdown
from src.normalizer.curriculum import _apply_corrections
from src.normalizer.models import Correction, CorrectionSet


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = PROJECT_ROOT / "data/raw/2022 교육과정 -일부 발췌본.pdf"
CORRECTIONS = (
    PROJECT_ROOT
    / "data/corrections/2022_curriculum_p128_130_corrections.json"
)


@unittest.skipUnless(SOURCE_PDF.exists(), "로컬 원본 PDF가 필요합니다.")
class CurriculumNormalizerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = normalize_curriculum_pdf(SOURCE_PDF)

    def test_normalizes_expected_course_totals(self) -> None:
        curriculum = self.result.curriculum

        self.assertEqual(len(curriculum.designated_courses), 5)
        self.assertEqual(len(curriculum.major_courses), 43)
        self.assertEqual(sum(course.credits for course in curriculum.major_courses), 144)

    def test_course_codes_are_unique(self) -> None:
        curriculum = self.result.curriculum
        courses = curriculum.designated_courses + curriculum.major_courses
        codes = [course.code for course in courses]

        self.assertEqual(len(codes), len(set(codes)))

    def test_two_pdf_engines_agree_on_codes_and_credits(self) -> None:
        check = next(
            check
            for check in self.result.validation_checks
            if check.check_id == "cross_parser_course_credits"
        )

        self.assertEqual(check.status, "pass")

    def test_multiline_and_spanning_names_are_joined(self) -> None:
        courses = {course.code: course for course in self.result.curriculum.major_courses}

        self.assertEqual(courses["CDA0148"].name_ko, "분산컴퓨팅시스템")
        self.assertEqual(
            courses["CDA0148"].name_en,
            "Fundamentals of Distributed Computing Systems",
        )
        self.assertEqual(courses["CDA0160"].name_ko, "표준형현장실습1")
        self.assertEqual(courses["CDA0160"].name_en, "Co-op 1")

    def test_every_normalized_fact_keeps_page_evidence(self) -> None:
        curriculum = self.result.curriculum
        evidence_items = (
            curriculum.profile_statements
            + curriculum.program_requirements
            + curriculum.credit_allocations
            + curriculum.designated_courses
            + curriculum.major_courses
        )

        self.assertTrue(
            all(item.evidence.pdf_page in {128, 129, 130} for item in evidence_items)
        )
        self.assertTrue(all(item.evidence.raw_text for item in evidence_items))

    def test_source_inconsistencies_are_warnings_not_silent_corrections(self) -> None:
        warnings = {
            check.check_id
            for check in self.result.validation_checks
            if check.status == "warning"
        }

        self.assertIn(
            "allocation_total:allocation:2022:전공:전공선택", warnings
        )
        self.assertIn("required_credit_cross_section", warnings)
        self.assertIn("source_spelling_candidates", warnings)
        self.assertEqual(self.result.validation_status, "warning")
        self.assertFalse(
            any(check.status == "fail" for check in self.result.validation_checks)
        )

    def test_warnings_include_actionable_review_information(self) -> None:
        warnings = [
            check
            for check in self.result.validation_checks
            if check.status == "warning"
        ]

        self.assertTrue(warnings)
        self.assertTrue(all(check.reason for check in warnings))
        self.assertTrue(all(check.evidence for check in warnings))
        self.assertTrue(all(check.review_steps for check in warnings))
        self.assertTrue(all(check.correction_targets for check in warnings))

    def test_review_markdown_contains_exact_pages_and_steps(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "review.md"
            write_review_markdown(self.result, output)
            report = output.read_text(encoding="utf-8")

        self.assertIn("PDF 파일 129쪽(인쇄 282쪽)", report)
        self.assertIn("### 확인 방법", report)
        self.assertIn("### 보정 가능한 대상", report)

    def test_correction_changes_copy_without_changing_source_evidence(self) -> None:
        curriculum = self.result.curriculum.model_copy(deep=True)
        correction = Correction(
            check_id="source_spelling_candidates",
            target_uid="course:CDA0016",
            field="name_en",
            value="Computer Architecture",
            reason="발행기관 정정 자료 확인",
            reviewer="test-reviewer",
        )
        original_evidence = next(
            course.evidence.raw_text
            for course in curriculum.major_courses
            if course.code == "CDA0016"
        )

        _apply_corrections(curriculum, [correction])
        corrected = next(
            course for course in curriculum.major_courses if course.code == "CDA0016"
        )

        self.assertEqual(corrected.name_en, "Computer Architecture")
        self.assertEqual(corrected.evidence.raw_text, original_evidence)

    def test_correction_template_matches_source_pdf(self) -> None:
        correction_set = CorrectionSet.model_validate_json(
            CORRECTIONS.read_text(encoding="utf-8")
        )

        self.assertEqual(correction_set.source_sha256, self.result.source.sha256)


if __name__ == "__main__":
    unittest.main()
