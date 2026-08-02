"""추출기의 단일 중간 산출물 스키마.

모든 값은 provenance(페이지·bbox·원문 인용·추출기)를 갖는다.
검증 게이트를 통과하면 status="auto", 걸리면 "review",
사람이 승인·수정하면 "confirmed". 이 JSON 하나를 검증·사람검토·이후 단계가 공유한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    page: int  # 1-base PDF 페이지 번호
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1), top-left 원점
    source_quote: str  # 추출 당시 원문 문자열 (quote grounding 대상)
    extractor: str  # "docling" | "docling-ocr" | "pdfplumber" | "human:<이름>"
    quote_verified: bool | None = None  # PDF 텍스트층 대조 결과. 사람 입력/텍스트층 없음이면 None


class NumericValue(BaseModel):
    """숫자 셀의 구조 분해 결과.

    조용한 오염 방지 규칙:
      - 빈칸은 0이 아니라 value=None (kind="empty")
      - "4주"처럼 단위가 붙으면 unit으로 분리 (kind="unit")
      - "3(3)" 이중값은 value=3, secondary=3 (kind="dual")
      - 숫자가 2개 이상인데 이중값 형식이 아니면 value=None (kind="ambiguous")
    """

    raw: str
    kind: Literal["number", "dual", "unit", "empty", "ambiguous", "text"]
    value: float | None = None
    secondary: float | None = None  # N(M)의 괄호 안 값
    unit: str | None = None  # 주, 시간 등


class Cell(BaseModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    is_header: bool = False
    value: str
    numeric: NumericValue | None = None  # 숫자로 해석 가능한 셀만
    provenance: Provenance
    status: Literal["auto", "review", "confirmed"] = "auto"
    issues: list[str] = Field(default_factory=list)  # 있으면 review로 강등
    notes: list[str] = Field(default_factory=list)  # 정보성 기록 (강등 없음)


class Table(BaseModel):
    table_id: str  # 예: "t287_0" (페이지_순번)
    page: int
    n_rows: int
    n_cols: int
    caption: str | None = None
    cells: list[Cell]

    def grid(self) -> list[list[str]]:
        """셀 목록을 2차원 문자열 격자로 펼친다 (span은 좌상단 위치에만 기록)."""
        g = [["" for _ in range(self.n_cols)] for _ in range(self.n_rows)]
        for c in self.cells:
            if 0 <= c.row < self.n_rows and 0 <= c.col < self.n_cols:
                g[c.row][c.col] = c.value
        return g

    def cell_at(self, row: int, col: int) -> Cell | None:
        for c in self.cells:
            if c.row == row and c.col == col:
                return c
        return None


class TextBlock(BaseModel):
    """표 밖의 본문 — 제목·문단·각주(※)·조건절. 삭제하지 않고 전량 보존한다."""

    page: int
    label: str  # docling 라벨: section_header / text / footnote / list_item ...
    text: str
    provenance: Provenance


class Check(BaseModel):
    check_id: str
    level: Literal["PASS", "WARNING", "FAIL"]
    kind: str  # quote_grounding | coverage | crosscheck | consistency | schema
    message: str
    page: int | None = None
    refs: list[str] = Field(default_factory=list)  # "t287_0" 또는 "t287_0:r5c2"
    suggestion: str | None = None  # 사람 검토 화면에 제시할 제안값


class Correction(BaseModel):
    """사람의 수정 기록. 적용 후에도 반드시 게이트를 재통과해야 한다."""

    target: str  # "t287_0:r5c2"
    action: Literal["keep_original", "use_suggested", "manual"]
    old_value: str
    new_value: str
    reviewer: str
    instruction: str | None = None  # 자연어 지시 원문 (있었다면)
    timestamp: str  # ISO 8601


class Summary(BaseModel):
    n_pages: int
    n_tables: int
    n_cells: int
    n_text_blocks: int
    n_auto: int
    n_review: int
    n_confirmed: int
    checks_pass: int
    checks_warning: int
    checks_fail: int


class ExtractionResult(BaseModel):
    source_file: str
    sha256: str
    page_range: tuple[int, int] | None = None
    pipeline: str = "pdf-auto-extractor v0.1"
    revision: int = 0  # 사람 보정·재검증을 거칠 때마다 +1
    tables: list[Table]
    texts: list[TextBlock]
    checks: list[Check]
    corrections: list[Correction] = Field(default_factory=list)
    summary: Summary

    def find_cell(self, ref: str) -> tuple[Table, Cell] | None:
        """"t287_0:r5c2" 형식의 참조로 셀을 찾는다."""
        if ":" not in ref:
            return None
        table_id, pos = ref.split(":", 1)
        try:
            r, c = pos.lstrip("r").split("c")
            row, col = int(r), int(c)
        except ValueError:
            return None
        for t in self.tables:
            if t.table_id == table_id:
                cell = t.cell_at(row, col)
                return (t, cell) if cell else None
        return None
