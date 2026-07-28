from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src.normalizer.models import NormalizationResult
from src.reviewer.session import ReviewSessionPaths


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = PROJECT_ROOT / "data/raw/2022 교육과정 -일부 발췌본.pdf"
NORMALIZED_JSON = (
    PROJECT_ROOT / "data/processed/2022_curriculum_p128_130_normalized.json"
)


class ReviewAppSmokeTest(unittest.TestCase):
    def test_initial_upload_screen_renders_without_exception(self) -> None:
        app = AppTest.from_file(
            str(PROJECT_ROOT / "review_app.py"), default_timeout=20
        ).run()

        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "교육과정 PDF 추출·검토")
        self.assertEqual(len(app.get("file_uploader")), 1)
        self.assertIn(
            "추출 및 자동 검증 시작",
            [button.label for button in app.button],
        )

    @unittest.skipUnless(
        SOURCE_PDF.exists() and NORMALIZED_JSON.exists(),
        "원본 PDF와 정규화 JSON이 필요합니다.",
    )
    def test_reviewer_name_enables_instruction_input(self) -> None:
        processed = PROJECT_ROOT / "data/processed"
        paths = ReviewSessionPaths(
            directory=PROJECT_ROOT,
            source_pdf=SOURCE_PDF,
            metadata_json=PROJECT_ROOT / "README.md",
            raw_json=processed / "2022_curriculum_p128_130_raw.json",
            normalized_json=NORMALIZED_JSON,
            review_markdown=(
                processed / "2022_curriculum_p128_130_normalized_review.md"
            ),
            corrections_json=(
                PROJECT_ROOT
                / "data/corrections/2022_curriculum_p128_130_corrections.json"
            ),
            events_jsonl=PROJECT_ROOT / "unused-events.jsonl",
            history_directory=PROJECT_ROOT / "unused-history",
        )
        result = NormalizationResult.model_validate_json(
            NORMALIZED_JSON.read_text(encoding="utf-8")
        )
        app = AppTest.from_file(
            str(PROJECT_ROOT / "review_app.py"), default_timeout=20
        )
        app.session_state["paths"] = paths
        app.session_state["result"] = result
        app.session_state["messages"] = []
        app.session_state["pending_proposal"] = None
        app.run()

        self.assertTrue(app.text_input[1].disabled)
        app.text_input[0].set_value("테스트 확인자").run()

        self.assertFalse(app.text_input[1].disabled)
        send = next(button for button in app.button if button.label == "지시 보내기")
        self.assertFalse(send.disabled)


if __name__ == "__main__":
    unittest.main()
