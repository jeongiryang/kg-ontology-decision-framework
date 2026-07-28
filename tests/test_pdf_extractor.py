from __future__ import annotations

import unittest
from pathlib import Path

from src.pdf_parser.extractor import extract_pdf_pages, parse_page_range


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = PROJECT_ROOT / "data/raw/2022 교육과정 -일부 발췌본.pdf"


class PageRangeTest(unittest.TestCase):
    def test_parses_inclusive_one_based_range(self) -> None:
        self.assertEqual(parse_page_range("128-130"), [128, 129, 130])

    def test_rejects_reversed_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_page_range("130-128")

    def test_rejects_ambiguous_range_format(self) -> None:
        with self.assertRaises(ValueError):
            parse_page_range("128,129,130")


@unittest.skipUnless(SOURCE_PDF.exists(), "로컬 원본 PDF가 필요합니다.")
class PdfExtractionIntegrationTest(unittest.TestCase):
    def test_extracts_only_pages_128_to_130(self) -> None:
        result = extract_pdf_pages(SOURCE_PDF, [128, 129, 130])

        self.assertEqual(result["source"]["total_pdf_pages"], 130)
        self.assertEqual(
            result["source"]["selected_pdf_pages"], [128, 129, 130]
        )
        self.assertEqual(
            [page["pdf_page"] for page in result["pages"]], [128, 129, 130]
        )
        self.assertTrue(all(page["block_count"] > 0 for page in result["pages"]))
        self.assertTrue(all(page["character_count"] > 0 for page in result["pages"]))

    def test_rejects_page_beyond_document(self) -> None:
        with self.assertRaises(ValueError):
            extract_pdf_pages(SOURCE_PDF, [131])


if __name__ == "__main__":
    unittest.main()
