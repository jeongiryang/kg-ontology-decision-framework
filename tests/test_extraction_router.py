from __future__ import annotations

import unittest
from pathlib import Path

from src.extraction import ExtractionRouter, PyMuPDFExtractionAdapter


class ExtractionRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ExtractionRouter([PyMuPDFExtractionAdapter()])

    def test_selects_pdf_adapter_case_insensitively(self) -> None:
        adapter = self.router.select(Path("curriculum.PDF"))

        self.assertIsInstance(adapter, PyMuPDFExtractionAdapter)

    def test_rejects_source_without_registered_adapter(self) -> None:
        with self.assertRaisesRegex(ValueError, "지원하지 않는 문서 형식"):
            self.router.select(Path("scan.png"))


if __name__ == "__main__":
    unittest.main()
