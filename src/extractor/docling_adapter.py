"""Tier 1 — Docling 레이아웃 모델로 PDF 구조를 복원한다.

기존 feat/docling-parser 브랜치와의 결정적 차이:
  - export_to_markdown/export_to_dataframe 로 평탄화하지 않는다.
    (그 순간 페이지·bbox provenance가 전량 소실된다 — 조사에서 확인된 구현 누락)
  - 셀 단위로 순회하며 페이지 번호·bbox·원문 텍스트를 Provenance로 보존한다.
  - 본문 텍스트(각주 ※, '단,' 조건절 포함)를 TextBlock으로 전량 보존한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.extractor.models import Cell, Provenance, Table, TextBlock
from src.extractor.numeric import parse_numeric

log = logging.getLogger(__name__)

_EXTRACTOR_NAME = "docling"


def _build_converter(ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    # 셀 텍스트를 모델 출력이 아니라 PDF 원본 텍스트층에서 1:1 매칭해 가져온다
    # (지식 환각 차단의 핵심 — 이슈 #6의 원칙 그대로)
    opts.table_structure_options.do_cell_matching = True
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _bbox_to_topleft(bbox, page_height: float | None) -> tuple[float, float, float, float] | None:
    """Docling BoundingBox → top-left 원점 (x0, y0, x1, y1). 실패 시 None."""
    if bbox is None:
        return None
    try:
        origin = getattr(getattr(bbox, "coord_origin", None), "name", "")
        if origin == "BOTTOMLEFT" and page_height:
            tl = bbox.to_top_left_origin(page_height)
            return (float(tl.l), float(tl.t), float(tl.r), float(tl.b))
        return (float(bbox.l), float(bbox.t), float(bbox.r), float(bbox.b))
    except Exception:  # bbox는 보조 정보 — 좌표 실패가 추출을 막으면 안 됨
        return None


def _page_heights(doc) -> dict[int, float]:
    heights: dict[int, float] = {}
    try:
        for no, page in doc.pages.items():
            size = getattr(page, "size", None)
            if size is not None:
                heights[int(no)] = float(size.height)
    except Exception:
        pass
    return heights


def parse_pdf(
    pdf_path: str | Path,
    page_range: tuple[int, int] | None = None,
    ocr: bool = False,
) -> tuple[list[Table], list[TextBlock]]:
    """PDF를 Docling으로 변환해 (표, 본문 블록) 목록으로 돌려준다.

    표·본문이 하나도 안 나오면 예외를 던지지 않는다 — 조용한 실패 여부는
    검증 게이트(G2 커버리지)가 pdfplumber 교차 근거로 판정한다.
    """
    converter = _build_converter(ocr=ocr)
    kwargs = {}
    if page_range is not None:
        kwargs["page_range"] = page_range
    result = converter.convert(str(pdf_path), **kwargs)
    doc = result.document
    heights = _page_heights(doc)
    extractor = "docling-ocr" if ocr else _EXTRACTOR_NAME

    tables = _collect_tables(doc, heights, extractor)
    texts = _collect_texts(doc, heights, extractor)
    log.info("docling: 표 %d개, 본문 블록 %d개", len(tables), len(texts))
    return tables, texts


def _collect_tables(doc, heights: dict[int, float], extractor: str) -> list[Table]:
    tables: list[Table] = []
    per_page_idx: dict[int, int] = {}

    for item in getattr(doc, "tables", []):
        prov_list = getattr(item, "prov", None) or []
        page = int(prov_list[0].page_no) if prov_list else 0
        idx = per_page_idx.get(page, 0)
        per_page_idx[page] = idx + 1
        table_id = f"t{page}_{idx}"

        data = getattr(item, "data", None)
        if data is None:
            log.warning("%s: data 없음 — 건너뜀", table_id)
            continue
        n_rows = int(getattr(data, "num_rows", 0))
        n_cols = int(getattr(data, "num_cols", 0))
        raw_cells = getattr(data, "table_cells", None) or []

        caption = None
        try:
            caption = item.caption_text(doc) or None
        except Exception:
            pass

        cells: list[Cell] = []
        page_h = heights.get(page)
        for rc in raw_cells:
            text = (getattr(rc, "text", "") or "").strip()
            row = int(getattr(rc, "start_row_offset_idx", 0))
            col = int(getattr(rc, "start_col_offset_idx", 0))
            row_span = max(1, int(getattr(rc, "end_row_offset_idx", row + 1)) - row)
            col_span = max(1, int(getattr(rc, "end_col_offset_idx", col + 1)) - col)
            is_header = bool(getattr(rc, "column_header", False)) or bool(
                getattr(rc, "row_header", False)
            )
            bbox = _bbox_to_topleft(getattr(rc, "bbox", None), page_h)
            # 숫자 구조는 확실한 형태(number/dual/unit)만 저장한다.
            # ambiguous 판정은 열 문맥이 필요하므로 게이트(G4)가 담당.
            numeric = parse_numeric(text) if text else None
            if numeric is not None and numeric.kind not in ("number", "dual", "unit"):
                numeric = None
            cells.append(
                Cell(
                    row=row,
                    col=col,
                    row_span=row_span,
                    col_span=col_span,
                    is_header=is_header,
                    value=text,
                    numeric=numeric,
                    provenance=Provenance(
                        page=page, bbox=bbox, source_quote=text, extractor=extractor
                    ),
                )
            )

        tables.append(
            Table(
                table_id=table_id,
                page=page,
                n_rows=n_rows,
                n_cols=n_cols,
                caption=caption,
                cells=cells,
            )
        )
    return tables


def _collect_texts(doc, heights: dict[int, float], extractor: str) -> list[TextBlock]:
    """본문 블록 수집. 각주(※)·조건절도 삭제 없이 보존한다."""
    blocks: list[TextBlock] = []
    for item in getattr(doc, "texts", []):
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        label = str(getattr(item, "label", "text"))
        prov_list = getattr(item, "prov", None) or []
        page = int(prov_list[0].page_no) if prov_list else 0
        bbox = None
        if prov_list:
            bbox = _bbox_to_topleft(getattr(prov_list[0], "bbox", None), heights.get(page))
        blocks.append(
            TextBlock(
                page=page,
                label=label.replace("DocItemLabel.", "").lower(),
                text=text,
                provenance=Provenance(
                    page=page, bbox=bbox, source_quote=text, extractor=extractor
                ),
            )
        )
    return blocks
