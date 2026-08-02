"""회귀 정답(golden) 테스트 — 컴공과 구간 (전체 PDF p287-289 = 인쇄면 281-283).

정답 출처: 서로 독립된 두 파이프라인(PR #3, PR #5)이 같은 구간에서 일치한 수치.
  - 과목 48개 (교양 5 + 전공 43)
  - 전공 43과목, 개설학점 합 144
  - 전공필수 9과목, 학점 합 21 (학점구조표의 규정값과 일치)
  - 표 내부 학수번호 중복 0
  - CDA0155/0156/0157 은 전공선택이다 — 기존 docling 브랜치가 전공필수로
    오분류했던 케이스. 자동 분류되면 전공선택이어야 하고,
    분류하지 못하면 최소한 빈칸(추측 금지)이어야 한다.

원본 PDF가 없으면 건너뛴다 (PR #3 컨벤션).
숫자 파서 단위 테스트는 PDF 없이도 실행된다.
"""

from __future__ import annotations

import unittest
from pathlib import Path

PDF = Path("data/raw/★2022학년도 교육과정(업로드)(2022-3-2).pdf")
CS_RANGE = (287, 289)


class TestNumericParsing(unittest.TestCase):
    """조용한 오염 방지 규칙 — PDF 불필요."""

    def test_empty_is_not_zero(self):
        from src.extractor.numeric import parse_numeric

        v = parse_numeric("")
        self.assertEqual(v.kind, "empty")
        self.assertIsNone(v.value)

    def test_dual_value(self):
        from src.extractor.numeric import parse_numeric

        v = parse_numeric("3(3)")
        self.assertEqual(v.kind, "dual")
        self.assertEqual(v.value, 3)
        self.assertEqual(v.secondary, 3)

    def test_unit_not_coerced(self):
        from src.extractor.numeric import parse_numeric

        v = parse_numeric("4주")
        self.assertEqual(v.kind, "unit")
        self.assertEqual(v.unit, "주")

    def test_ambiguous_multi_number(self):
        from src.extractor.numeric import parse_numeric

        v = parse_numeric("12 3")
        self.assertEqual(v.kind, "ambiguous")
        self.assertIsNone(v.value)


@unittest.skipUnless(PDF.exists(), "원본 PDF 없음 — 로컬 통합 테스트")
class TestGoldenCS(unittest.TestCase):
    """컴공과 구간 통합 검증. Docling 실행 포함 — 수십 초 소요."""

    @classmethod
    def setUpClass(cls):
        from src.extractor import pipeline
        from src.extractor.profiles import COURSE_CODE

        cls.COURSE_CODE = COURSE_CODE
        cls.result = pipeline.extract(PDF, page_range=CS_RANGE)

    def _course_cells(self):
        """(학수번호, 행, 표) 목록 — 표 본문에서 학수번호가 있는 행."""
        out = []
        for t in self.result.tables:
            for c in t.cells:
                codes = self.COURSE_CODE.findall(c.value)
                if codes:
                    out.append((codes[0], c.row, t))
        return out

    def test_total_courses_48(self):
        codes = {code for code, _, _ in self._course_cells()}
        self.assertEqual(len(codes), 48, f"과목 수 {len(codes)} ≠ 48: {sorted(codes)}")

    def test_no_duplicate_codes_within_table(self):
        for t in self.result.tables:
            seen: dict[str, int] = {}
            for c in t.cells:
                for code in self.COURSE_CODE.findall(c.value):
                    if code in seen and seen[code] != c.row:
                        self.fail(f"{t.table_id}: {code} 중복")
                    seen[code] = c.row

    def _category_of(self, target_code: str) -> str | None:
        """해당 과목 행의 이수구분 셀 값 (보충 포함)."""
        from src.extractor.profiles import classify_header, normalize_category

        for t in self.result.tables:
            cat_col = None
            for c in t.cells:
                if c.row <= 2 and classify_header(c.value) == "category":
                    cat_col = c.col
                    break
            if cat_col is None:
                continue
            for c in t.cells:
                if target_code in c.value:
                    cell = t.cell_at(c.row, cat_col)
                    if cell is not None and cell.value.strip():
                        return normalize_category(cell.value)
        return None

    def test_cda0155_0157_not_misclassified(self):
        """기존 docling 브랜치의 오분류 재발 방지 — 전공필수로 확정되면 안 된다."""
        for code in ("CDA0155", "CDA0156", "CDA0157"):
            cat = self._category_of(code)
            self.assertNotEqual(
                cat, "전공필수",
                f"{code}가 전공필수로 오분류됨 (정답: 전공선택 또는 미분류+검토)",
            )

    def test_required_major_credits_21(self):
        """전공필수로 분류된 과목의 학점 합이 규정값 21이어야 한다.

        분류가 하나도 안 됐다면(보충 실패) 이 테스트는 실패한다 —
        그 경우 crosscheck 보충 로직을 점검할 것.
        """
        from src.extractor.numeric import parse_numeric
        from src.extractor.profiles import classify_header, normalize_category

        total = 0.0
        n = 0
        for t in self.result.tables:
            cat_col = credit_col = None
            for c in t.cells:
                if c.row <= 2:
                    role = classify_header(c.value)
                    if role == "category" and cat_col is None:
                        cat_col = c.col
                    elif role == "credit" and credit_col is None:
                        credit_col = c.col
            if cat_col is None or credit_col is None:
                continue
            rows = {c.row for c in t.cells if self.COURSE_CODE.search(c.value)}
            for r in rows:
                cat_cell = t.cell_at(r, cat_col)
                if cat_cell is None or normalize_category(cat_cell.value) != "전공필수":
                    continue
                cr = t.cell_at(r, credit_col)
                if cr is None:
                    continue
                v = parse_numeric(cr.value)
                if v.value is not None:
                    total += v.value
                    n += 1
        self.assertEqual(n, 9, f"전공필수 과목 수 {n} ≠ 9")
        self.assertEqual(total, 21.0, f"전공필수 학점 합 {total} ≠ 21")


if __name__ == "__main__":
    unittest.main(verbosity=2)
