from __future__ import annotations

import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from src.reviewer import (
    apply_correction_proposal,
    create_review_session,
    initialize_review_session,
    propose_corrections_from_instruction,
    render_evidence_image,
)
from src.reviewer.session import _normalize_with_blank_cell_retry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = PROJECT_ROOT / "data/raw/2022 교육과정 -일부 발췌본.pdf"


@unittest.skipUnless(SOURCE_PDF.exists(), "로컬 원본 PDF가 필요합니다.")
class ReviewSessionIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf_bytes = SOURCE_PDF.read_bytes()

    def test_upload_extract_render_approve_and_revalidate(self) -> None:
        with TemporaryDirectory() as directory:
            paths = create_review_session(
                self.pdf_bytes,
                SOURCE_PDF.name,
                sessions_root=Path(directory),
            )
            initial = initialize_review_session(paths)
            spelling_check = next(
                check
                for check in initial.validation_checks
                if check.check_id == "source_spelling_candidates"
            )
            crop = render_evidence_image(paths.source_pdf, spelling_check.evidence[0])
            with Image.open(BytesIO(crop)) as image:
                crop_size = image.size

            proposal = propose_corrections_from_instruction(
                initial,
                instruction="운리의식을 윤리의식으로 수정해줘",
                reviewer="integration-tester",
                allowed_check_id=spelling_check.check_id,
            )
            revised = apply_correction_proposal(
                paths,
                proposal,
                instruction="운리의식을 윤리의식으로 수정해줘",
                check_id=spelling_check.check_id,
            )
            events = [
                json.loads(line)
                for line in paths.events_jsonl.read_text(encoding="utf-8").splitlines()
            ]
            history = list(paths.history_directory.iterdir())

        self.assertGreater(crop_size[0], 0)
        self.assertGreater(crop_size[1], 0)
        statement = next(
            item
            for item in revised.curriculum.profile_statements
            if item.uid == "profile:2022:computer-engineering:talent_profile:3"
        )
        self.assertIn("윤리의식", statement.text)
        self.assertIn("운리의식", statement.evidence.raw_text)
        self.assertEqual([event["event"] for event in events], [
            "extracted",
            "correction_approved",
        ])
        self.assertEqual(len(history), 1)

    def test_rejects_pdf_shorter_than_current_profile_scope(self) -> None:
        import pymupdf

        document = pymupdf.open()
        document.new_page()
        short_pdf = document.tobytes()
        document.close()

        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "128–130쪽"):
                create_review_session(
                    short_pdf,
                    "short.pdf",
                    sessions_root=Path(directory),
                )

    def test_retries_one_transient_blank_numeric_cell(self) -> None:
        expected = object()
        transient = ValueError("invalid literal for int() with base 10: ''")
        with patch(
            "src.reviewer.session.normalize_curriculum_pdf",
            side_effect=[transient, expected],
        ) as normalize:
            result = _normalize_with_blank_cell_retry(
                Path("source.pdf"), Path("corrections.json")
            )

        self.assertIs(result, expected)
        self.assertEqual(normalize.call_count, 2)

    def test_reports_clear_error_after_second_blank_numeric_cell(self) -> None:
        transient = ValueError("invalid literal for int() with base 10: ''")
        with patch(
            "src.reviewer.session.normalize_curriculum_pdf",
            side_effect=[transient, transient],
        ):
            with self.assertRaisesRegex(ValueError, "두 번 연속"):
                _normalize_with_blank_cell_retry(
                    Path("source.pdf"), Path("corrections.json")
                )


if __name__ == "__main__":
    unittest.main()
