from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from src.normalizer.models import Correction, CorrectionSet, NormalizationResult
from src.reviewer import (
    ReviewSessionPaths,
    apply_correction_proposal,
    create_review_session,
    initialize_review_session,
    build_review_presentation,
    correction_target_views,
    propose_corrections_from_instruction,
    render_evidence_image,
)


st.set_page_config(
    page_title="교육과정 PDF 추출·검토",
    page_icon="📄",
    layout="wide",
)


def _state_default(key: str, value: Any) -> None:
    if key not in st.session_state:
        st.session_state[key] = value


def _correctable_items(result: NormalizationResult) -> dict[str, Any]:
    curriculum = result.curriculum
    items = (
        curriculum.profile_statements
        + curriculum.program_requirements
        + curriculum.credit_allocations
        + curriculum.designated_courses
        + curriculum.major_courses
    )
    return {item.uid: item for item in items}


def _current_value(result: NormalizationResult, correction: Correction) -> Any:
    item = _correctable_items(result).get(correction.target_uid)
    if item is None:
        return "(대상을 찾을 수 없음)"
    return getattr(item, correction.field, "(필드를 찾을 수 없음)")


def _new_corrections(
    result: NormalizationResult, proposal: CorrectionSet
) -> list[Correction]:
    previous = {
        (item.target_uid, item.field, repr(item.value))
        for item in result.corrections_applied
    }
    return [
        item
        for item in proposal.corrections
        if (item.target_uid, item.field, repr(item.value)) not in previous
    ]


def _status_summary(result: NormalizationResult) -> None:
    passed = sum(check.status == "pass" for check in result.validation_checks)
    warnings = sum(check.status == "warning" for check in result.validation_checks)
    failed = sum(check.status == "fail" for check in result.validation_checks)
    first, second, third, fourth = st.columns(4)
    first.metric("전체 상태", result.validation_status.upper())
    second.metric("통과", passed)
    third.metric("경고", warnings)
    fourth.metric("실패", failed)
    if result.validation_status == "pass":
        st.success("모든 자동 검사를 통과했습니다.")
    elif result.validation_status == "warning":
        st.warning("사람이 확인해야 할 항목이 있어 다음 단계 진행을 중단했습니다.")
    else:
        st.error("자동 사용이 불가능한 실패 항목이 있습니다.")


def _show_downloads(paths: ReviewSessionPaths) -> None:
    with st.expander("생성 파일 내려받기"):
        first, second, third = st.columns(3)
        first.download_button(
            "정규화 JSON",
            paths.normalized_json.read_bytes(),
            file_name="normalized.json",
            mime="application/json",
            width="stretch",
        )
        second.download_button(
            "검토 보고서",
            paths.review_markdown.read_bytes(),
            file_name="review.md",
            mime="text/markdown",
            width="stretch",
        )
        third.download_button(
            "보정 기록",
            paths.corrections_json.read_bytes(),
            file_name="corrections.json",
            mime="application/json",
            width="stretch",
        )


def _show_evidence(paths: ReviewSessionPaths, check: Any) -> None:
    st.subheader("규정집 원문")
    if not check.evidence:
        st.info("이 검사는 특정 문장 좌표가 아니라 문서 전체 구조를 검사합니다.")
        return

    show_page_key = f"show_full_page:{check.check_id}"
    _state_default(show_page_key, False)
    st.checkbox("전체 페이지도 같이 보기", key=show_page_key)
    for index, evidence in enumerate(check.evidence, start=1):
        st.markdown(
            f"**근거 {index} · PDF {evidence.pdf_page}쪽 · "
            f"인쇄 {evidence.printed_page}쪽 · {evidence.section}**"
        )
        st.image(
            render_evidence_image(paths.source_pdf, evidence),
            caption="빨간 테두리가 자동 검증에서 참조한 원문 위치입니다.",
            width="stretch",
        )
        st.code(evidence.raw_text, language=None, wrap_lines=True)
        if st.session_state[show_page_key]:
            st.image(
                render_evidence_image(
                    paths.source_pdf,
                    evidence,
                    full_page=True,
                    zoom=1.35,
                ),
                caption=f"PDF {evidence.pdf_page}쪽 전체",
                width="stretch",
            )


def _show_issue_details(result: NormalizationResult, check: Any) -> None:
    presentation = build_review_presentation(check)
    st.subheader(f"[{check.status.upper()}] {check.message}")
    st.markdown(f"**검사 ID:** `{check.check_id}`")
    if check.status == "fail":
        st.error(presentation.summary)
    else:
        st.warning(presentation.summary)

    st.markdown("#### 바로 비교할 값")
    value_columns = st.columns(len(presentation.values))
    for column, item in zip(value_columns, presentation.values):
        column.markdown(f"**{item.label}**")
        if isinstance(item.value, (dict, list)):
            column.json(item.value)
        else:
            column.code(str(item.value), language=None, wrap_lines=True)

    st.markdown("#### 사람이 결정할 내용")
    st.info(presentation.decision_question)

    targets = correction_target_views(result, check)
    if targets:
        st.markdown("#### 수정 가능한 현재 값")
        for target in targets:
            st.markdown(f"**{target.label}**")
            st.code(str(target.current_value), language=None, wrap_lines=True)
            st.caption(f"수정 위치: {target.uid}.{target.field}")
    else:
        st.error(
            "안전하게 수정할 대상이 지정되지 않은 검사입니다. 자연어로 값을 만들지 않고 추출기 또는 검증 코드 수정 대상으로 남깁니다."
        )

    st.markdown("#### 자연어 지시 예시")
    for example in presentation.instruction_examples:
        st.code(example, language=None)

    if check.review_steps:
        with st.expander("상세 확인 순서"):
            for index, step in enumerate(check.review_steps, start=1):
                st.markdown(f"{index}. {step}")


def _show_chat(
    result: NormalizationResult,
    paths: ReviewSessionPaths,
    check: Any,
    reviewer: str,
) -> None:
    presentation = build_review_presentation(check)
    st.subheader("검토 채팅")
    st.caption(
        "현재 화면의 검사 안에서만 수정 대상을 찾습니다. 입력만으로 적용되지 않으며 변경 미리보기 후 승인이 필요합니다."
    )
    messages = st.session_state.messages
    for message in messages:
        if message["check_id"] != check.check_id:
            continue
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    pending = st.session_state.pending_proposal
    if pending is not None:
        proposal: CorrectionSet = pending["proposal"]
        st.markdown("#### 변경 미리보기")
        for correction in _new_corrections(result, proposal):
            st.markdown(f"`{correction.target_uid}.{correction.field}`")
            before, after = st.columns(2)
            before.markdown("**변경 전**")
            before.code(str(_current_value(result, correction)), wrap_lines=True)
            after.markdown("**변경 후**")
            after.code(str(correction.value), wrap_lines=True)
        approve, cancel = st.columns(2)
        if approve.button(
            "승인하고 JSON 다시 만들기",
            type="primary",
            width="stretch",
        ):
            try:
                with st.spinner("보정 기록 저장 후 전체 검사를 다시 실행하고 있습니다..."):
                    revised = apply_correction_proposal(
                        paths,
                        proposal,
                        instruction=pending["instruction"],
                        check_id=pending["check_id"],
                    )
                st.session_state.result = revised
                st.session_state.pending_proposal = None
                st.session_state.messages.append(
                    {
                        "role": "assistant",
                        "check_id": check.check_id,
                        "content": (
                            "승인된 변경을 적용하고 JSON을 다시 생성했습니다. "
                            f"재검증 결과는 **{revised.validation_status.upper()}**입니다."
                        ),
                    }
                )
                st.rerun()
            except (FileNotFoundError, ValueError) as error:
                st.error(str(error))
        if cancel.button("제안 취소", width="stretch"):
            st.session_state.pending_proposal = None
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "check_id": check.check_id,
                    "content": "변경 제안을 취소했습니다. JSON은 수정하지 않았습니다.",
                }
            )
            st.rerun()

    disabled_reason: str | None = None
    if pending is not None:
        disabled_reason = "위 변경 제안을 먼저 승인하거나 취소해 주세요."
    elif not reviewer.strip():
        disabled_reason = "상단의 확인자 이름을 입력하면 채팅이 활성화됩니다."
    elif not check.correction_targets:
        disabled_reason = "이 검사는 자연어로 안전하게 수정할 대상이 없습니다."

    if disabled_reason:
        st.warning(f"채팅 입력 대기: {disabled_reason}")
    else:
        st.success("채팅 입력 가능")

    with st.form(
        key=f"review_instruction:{check.check_id}",
        clear_on_submit=True,
    ):
        instruction = st.text_input(
            "수정 지시",
            placeholder=f"예: {presentation.instruction_examples[0]}",
            disabled=disabled_reason is not None,
        )
        send_instruction = st.form_submit_button(
            "지시 보내기",
            type="primary",
            disabled=disabled_reason is not None,
            width="stretch",
        )
    if send_instruction and instruction.strip():
        st.session_state.messages.append(
            {"role": "user", "check_id": check.check_id, "content": instruction}
        )
        show_page_key = f"show_full_page:{check.check_id}"
        if "전체 페이지" in instruction or "페이지 전체" in instruction:
            st.session_state[show_page_key] = True
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "check_id": check.check_id,
                    "content": "원문 영역 아래에 전체 페이지를 표시했습니다.",
                }
            )
            st.rerun()
        try:
            proposal = propose_corrections_from_instruction(
                result,
                instruction=instruction,
                reviewer=reviewer,
                allowed_check_id=check.check_id,
            )
            proposed_count = len(_new_corrections(result, proposal))
            st.session_state.pending_proposal = {
                "proposal": proposal,
                "instruction": instruction,
                "check_id": check.check_id,
            }
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "check_id": check.check_id,
                    "content": (
                        f"현재 검사에서 변경 {proposed_count}건을 찾았습니다. "
                        "아래 미리보기를 확인하고 승인하거나 취소해 주세요."
                    ),
                }
            )
        except ValueError as error:
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "check_id": check.check_id,
                    "content": f"변경 제안을 만들지 않았습니다: {error}",
                }
            )
        st.rerun()


for key, value in (
    ("paths", None),
    ("result", None),
    ("messages", []),
    ("pending_proposal", None),
):
    _state_default(key, value)

st.title("교육과정 PDF 추출·검토")
st.write(
    "PDF 128–130쪽을 추출·검증하고, 문제가 있는 원문을 보면서 자연어로 수정한 뒤 다시 검증합니다."
)
st.caption(
    "현재 프로필은 2022 교육과정 128–130쪽 전용입니다. 업로드 원본은 수정하지 않습니다."
)

with st.container(border=True):
    uploaded = st.file_uploader("교육과정 PDF 업로드", type=["pdf"])
    reviewer = st.text_input(
        "확인자 이름 *",
        placeholder="보정 이력에 남길 이름",
        key="reviewer_name",
    )
    if not reviewer.strip():
        st.warning("추출과 자연어 검토를 시작하려면 확인자 이름을 입력해 주세요.")
    force = st.checkbox("기존 세션도 원본부터 다시 추출하기", value=False)
    start = st.button(
        "추출 및 자동 검증 시작",
        type="primary",
        disabled=uploaded is None or not reviewer.strip(),
        width="stretch",
    )

if start and uploaded is not None:
    try:
        with st.spinner("PDF 128–130쪽을 추출하고 두 엔진으로 검증하고 있습니다..."):
            paths = create_review_session(uploaded.getvalue(), uploaded.name)
            result = initialize_review_session(paths, force=force)
        st.session_state.paths = paths
        st.session_state.result = result
        st.session_state.messages = []
        st.session_state.pending_proposal = None
        st.rerun()
    except (FileNotFoundError, ValueError) as error:
        st.error(str(error))

paths = st.session_state.paths
result = st.session_state.result
if paths is not None and result is not None:
    st.divider()
    _status_summary(result)
    _show_downloads(paths)
    issues = [check for check in result.validation_checks if check.status != "pass"]
    if not issues:
        st.success("검토 대기 항목이 없습니다.")
    else:
        st.header("사람 검토 대기열")
        selected_id = st.selectbox(
            "검토할 항목",
            options=[check.check_id for check in issues],
            format_func=lambda check_id: next(
                f"[{item.status.upper()}] {item.message}"
                for item in issues
                if item.check_id == check_id
            ),
            disabled=st.session_state.pending_proposal is not None,
        )
        selected = next(check for check in issues if check.check_id == selected_id)
        left, right = st.columns([1.25, 1], gap="large")
        with left:
            _show_evidence(paths, selected)
        with right:
            _show_issue_details(result, selected)
            _show_chat(result, paths, selected, reviewer)
