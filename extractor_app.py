"""규정집 PDF 자동 추출기 — 웹 화면.

실행:  streamlit run extractor_app.py

흐름:  PDF 업로드 → 자동 추출·검증 → 검토 큐(문제 항목만)
       → 사람이 [원문 유지]/[제안값 채택]/[직접 입력] 또는 자연어 지시로 보정
       → 보정 적용 시 전체 재검증 (사람 수정도 게이트를 다시 통과해야 함)
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw

from src.extractor import pipeline
from src.extractor.models import Check, ExtractionResult

UPLOAD_DIR = Path("data/uploads")

st.set_page_config(page_title="규정집 PDF 자동 추출기", layout="wide")

# ── 유틸 ─────────────────────────────────────────────────────────────────────

NL_PATTERNS = [
    re.compile(r"^(?P<old>.+?)\s*(?:을|를)\s*(?P<new>.+?)\s*(?:으로|로)\s*(?:수정|변경|바꿔|고쳐)"),
    re.compile(r"^(?P<old>.+?)\s*(?:->|→)\s*(?P<new>.+?)$"),
]


def parse_nl_instruction(text: str) -> tuple[str, str] | None:
    """자연어 수정 지시에서 (기존값, 새값)을 뽑는다. 규칙 기반 — LLM 없음."""
    t = text.strip()
    for pat in NL_PATTERNS:
        m = pat.match(t)
        if m:
            old, new = m.group("old").strip(), m.group("new").strip()
            if old and new and old != new:
                return old, new
    return None


@st.cache_data(show_spinner=False)
def render_page_image(pdf_path: str, page: int, bbox: tuple | None, zoom: float = 2.0) -> bytes:
    """페이지를 렌더링하고 bbox를 빨간 박스로 표시한 PNG 바이트를 돌려준다."""
    with fitz.open(pdf_path) as doc:
        pg = doc[page - 1]
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if bbox:
        d = ImageDraw.Draw(img)
        x0, y0, x1, y1 = (v * zoom for v in bbox)
        d.rectangle([x0, y0, x1, y1], outline=(220, 20, 20), width=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def cell_refs(check: Check) -> list[str]:
    return [r for r in check.refs if ":" in r]


def apply_one(result: ExtractionResult, pdf_path: str, corr) -> ExtractionResult:
    with st.spinner("보정 적용 후 전체 재검증 중…"):
        return pipeline.apply_corrections(result, [corr], pdf_path)


# ── 사이드바 ─────────────────────────────────────────────────────────────────

with st.sidebar:
    st.header("설정")
    reviewer = st.text_input("확인자 이름 *", value=st.session_state.get("reviewer", ""))
    st.session_state["reviewer"] = reviewer
    pages_str = st.text_input("페이지 범위 (비우면 전체)", value="", placeholder="예: 287-289")
    use_ocr = st.checkbox("OCR 사용 (텍스트층 없는 페이지)", value=False)
    st.caption(
        "확인자 이름은 모든 보정 기록에 남습니다. "
        "전체 문서(366쪽)는 GPU 기준 수 분이 걸립니다."
    )

st.title("📄 규정집 PDF 자동 추출기")
st.caption(
    "Docling 구조 복원 → pdfplumber 교차검증 → 결정론적 게이트(원문 대조·커버리지·정합성) "
    "→ 사람 검토 → 재검증. 추출 단계에 LLM을 쓰지 않습니다."
)

uploaded = st.file_uploader("규정집 PDF를 올려주세요", type=["pdf"])

if uploaded is not None:
    data = uploaded.getvalue()
    sha = hashlib.sha256(data).hexdigest()
    updir = UPLOAD_DIR / sha[:16]
    updir.mkdir(parents=True, exist_ok=True)
    pdf_path = updir / "source.pdf"
    if not pdf_path.exists():
        pdf_path.write_bytes(data)
    st.session_state["pdf_path"] = str(pdf_path)

    page_range = None
    if pages_str.strip():
        try:
            a, b = (pages_str.split("-", 1) + [pages_str])[:2]
            page_range = (int(a), int(b))
        except ValueError:
            st.error("페이지 범위 형식이 잘못됐습니다. 예: 287-289")

    run_key = f"{sha}:{page_range}:{use_ocr}"
    if st.button("🚀 추출 및 자동 검증 시작", disabled=not reviewer.strip(), type="primary"):
        if not reviewer.strip():
            st.warning("확인자 이름을 먼저 입력해주세요.")
        else:
            with st.status("추출 중… (Docling 레이아웃 분석 → 교차검증 → 게이트)", expanded=True) as status:
                result = pipeline.extract(pdf_path, page_range=page_range, ocr=use_ocr)
                st.session_state["result"] = result
                st.session_state["run_key"] = run_key
                status.update(label="추출 완료", state="complete")

    if not reviewer.strip():
        st.info("👈 사이드바에 **확인자 이름**을 입력해야 추출을 시작할 수 있습니다.")

# ── 결과 표시 ─────────────────────────────────────────────────────────────────

result: ExtractionResult | None = st.session_state.get("result")
if result is not None:
    pdf_path = st.session_state["pdf_path"]
    s = result.summary

    st.divider()
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("표", s.n_tables)
    c2.metric("셀", s.n_cells)
    c3.metric("자동확정", s.n_auto)
    c4.metric("검토필요", s.n_review, delta_color="inverse")
    c5.metric("사람확정", s.n_confirmed)
    c6.metric("검사 P/W/F", f"{s.checks_pass}/{s.checks_warning}/{s.checks_fail}")
    if result.revision > 0:
        st.caption(f"재검증 {result.revision}회 수행됨 · 보정 {len(result.corrections)}건 기록")

    tab_review, tab_tables, tab_texts, tab_dl = st.tabs(
        ["🔎 검토 큐", "📊 표 보기", "📃 본문 텍스트", "⬇️ 내려받기"]
    )

    # ── 검토 큐 ──────────────────────────────────────────────────────────
    with tab_review:
        problems = [k for k in result.checks if k.level in ("WARNING", "FAIL")]
        if not problems:
            st.success("검토할 항목이 없습니다 — 모든 검사 통과 ✅")
        else:
            st.write(f"사람 확인이 필요한 검사 **{len(problems)}건**")

            # 자연어 일괄 지시 (규칙 기반)
            with st.expander("💬 자연어로 수정 지시 (예: `운리의식을 윤리의식으로 수정` 또는 `78 → 54`)"):
                nl = st.text_input("지시 입력", key="nl_input")
                if st.button("지시 해석", key="nl_go") and nl.strip():
                    parsed = parse_nl_instruction(nl)
                    if parsed is None:
                        st.error(
                            "지시를 해석하지 못했습니다. `기존값을 새값으로 수정` 또는 "
                            "`기존값 → 새값` 형식으로 입력해주세요. "
                            "(모호한 지시는 임의로 해석하지 않습니다)"
                        )
                    else:
                        old, new = parsed
                        targets = [
                            (t, c)
                            for t in result.tables
                            for c in t.cells
                            if c.status == "review" and c.value == old
                        ]
                        if len(targets) == 0:
                            st.error(f"검토필요 셀 중 값이 '{old}'인 셀이 없습니다.")
                        elif len(targets) > 1:
                            st.error(
                                f"값이 '{old}'인 검토필요 셀이 {len(targets)}개라 대상을 "
                                f"특정할 수 없습니다. 아래 개별 항목에서 수정해주세요."
                            )
                        else:
                            t, c = targets[0]
                            corr = pipeline.make_correction(
                                target=f"{t.table_id}:r{c.row}c{c.col}",
                                action="manual",
                                old_value=old,
                                new_value=new,
                                reviewer=st.session_state["reviewer"],
                                instruction=nl,
                            )
                            st.session_state["result"] = apply_one(result, pdf_path, corr)
                            st.rerun()

            for i, k in enumerate(problems):
                refs = cell_refs(k)
                with st.expander(f"[{k.level}] {k.message}", expanded=(i == 0)):
                    col_img, col_act = st.columns([3, 2])

                    found = result.find_cell(refs[0]) if refs else None
                    with col_img:
                        if k.page:
                            bbox = None
                            if found is not None:
                                bbox = found[1].provenance.bbox
                            st.image(
                                render_page_image(pdf_path, k.page, bbox),
                                caption=f"p{k.page}"
                                + (" — 문제 위치 빨간 박스" if bbox else ""),
                                width="stretch",
                            )
                        else:
                            st.caption("페이지 정보 없는 요약 검사입니다.")

                    with col_act:
                        if found is None:
                            st.caption("특정 셀에 연결되지 않은 검사 — 표 보기에서 원인을 확인하세요.")
                            continue
                        table, cell = found
                        if len(refs) > 1:
                            st.info(f"이 판정은 **{len(refs)}개 셀에 일괄 적용**됩니다.")
                        st.markdown(
                            f"**현재값** `{cell.value or '(빈칸)'}`  \n"
                            f"위치 `{table.table_id}` r{cell.row}c{cell.col} (p{table.page})"
                            + (f" 외 {len(refs) - 1}개" if len(refs) > 1 else "")
                            + f"  \n이슈: {', '.join(cell.issues) or '—'}"
                        )
                        options = ["원문 값 유지 (원문이 맞음)"]
                        if k.suggestion:
                            options.append(f"제안값 채택: {k.suggestion}")
                        options.append("직접 입력")
                        choice = st.radio("판정", options, key=f"radio_{i}", index=None)
                        manual_val = ""
                        if choice == "직접 입력":
                            manual_val = st.text_input("새 값", key=f"manual_{i}")
                        if st.button("적용 → 재검증", key=f"apply_{i}", disabled=choice is None):
                            if choice == "직접 입력" and not manual_val.strip():
                                st.warning("새 값을 입력해주세요.")
                                st.stop()
                            corrs = []
                            for r in refs:
                                fc = result.find_cell(r)
                                if fc is None:
                                    continue
                                _t, c_ = fc
                                if choice.startswith("원문"):
                                    action, new_val = "keep_original", c_.value
                                elif choice.startswith("제안값"):
                                    action, new_val = "use_suggested", (k.suggestion or "")
                                else:
                                    action, new_val = "manual", manual_val.strip()
                                corrs.append(
                                    pipeline.make_correction(
                                        target=r, action=action,
                                        old_value=c_.value, new_value=new_val,
                                        reviewer=st.session_state["reviewer"],
                                    )
                                )
                            with st.spinner(f"보정 {len(corrs)}건 적용 후 전체 재검증 중…"):
                                st.session_state["result"] = pipeline.apply_corrections(
                                    result, corrs, pdf_path
                                )
                            st.rerun()

    # ── 표 보기 ──────────────────────────────────────────────────────────
    with tab_tables:
        if not result.tables:
            st.info("추출된 표가 없습니다.")
        else:
            pages_avail = sorted({t.page for t in result.tables})
            sel_page = st.selectbox("페이지", pages_avail)
            for t in [x for x in result.tables if x.page == sel_page]:
                st.markdown(f"**{t.table_id}** ({t.n_rows}×{t.n_cols})" + (f" — {t.caption}" if t.caption else ""))
                df = pd.DataFrame(t.grid())
                st.dataframe(df, width="stretch")
                flagged = [c for c in t.cells if c.status == "review"]
                if flagged:
                    st.caption(
                        "검토필요 셀: "
                        + ", ".join(f"r{c.row}c{c.col}({','.join(c.issues)})" for c in flagged[:10])
                        + (" …" if len(flagged) > 10 else "")
                    )

    # ── 본문 텍스트 ──────────────────────────────────────────────────────
    with tab_texts:
        if not result.texts:
            st.info("본문 블록이 없습니다.")
        else:
            show_notes = st.checkbox("각주(※)·조건절만 보기", value=False)
            for tb in result.texts:
                if show_notes and ("※" not in tb.text and "단," not in tb.text):
                    continue
                st.markdown(f"`p{tb.page}` **[{tb.label}]** {tb.text}")

    # ── 내려받기 ─────────────────────────────────────────────────────────
    with tab_dl:
        st.download_button(
            "결과 JSON (provenance·검사·보정 포함)",
            data=json.dumps(result.model_dump(), ensure_ascii=False, indent=1),
            file_name=f"{Path(result.source_file).stem}.extraction.json",
            mime="application/json",
        )
        if result.corrections:
            st.download_button(
                "보정 기록 JSON",
                data=json.dumps(
                    [c.model_dump() for c in result.corrections], ensure_ascii=False, indent=1
                ),
                file_name="corrections.json",
                mime="application/json",
            )
