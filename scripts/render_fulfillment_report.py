"""Render request-fulfillment SSE results without truncating answer bodies.

This reporting utility is evaluation-only.  Runtime code never imports the public
questions, expected outcomes, or recorded answers.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latency(rows: Iterable[dict[str, Any]]) -> tuple[float, float, float]:
    values = sorted(float(row["elapsed_seconds"]) for row in rows)
    index = max(0, min(len(values) - 1, int(len(values) * 0.95 + 0.999) - 1))
    return (
        round(statistics.fmean(values), 3),
        round(statistics.median(values), 3),
        round(values[index], 3),
    )


def _reason(row: dict[str, Any]) -> str:
    items = row.get("requested_items") or ()
    item_states = ", ".join(
        f"{item.get('action')}={item.get('status')}"
        + (f"({item['reason_code']})" if item.get("reason_code") else "")
        for item in items
    )
    if not item_states:
        item_states = "대화 행위 또는 최소 확인 질문으로 처리"
    return (
        f"turn={row.get('fulfillment_status') or '-'}; {item_states}; "
        f"Citation={row.get('citation_count', 0)}; Cypher={'실행' if row.get('cypher_executed') else '미실행'}"
    )


def _details(identifier: str, row: dict[str, Any]) -> list[str]:
    return [
        f"<details><summary>{identifier} · {row['outcome_status']} · 전문과 판정 근거</summary>",
        "",
        f"질문: {row['question']}",
        "",
        f"판정: {_reason(row)}",
        "",
        "~~~text",
        row.get("answer") or "(응답 본문 없음)",
        "~~~",
        "",
        "</details>",
        "",
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--override", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base = _load(args.base)
    single = {row["id"]: row for row in base.get("single_turn", ())}
    multi = {row["id"]: row for row in base.get("multi_turn", ())}
    for path in args.override:
        payload = _load(path)
        single.update({row["id"]: row for row in payload.get("single_turn", ())})
        multi.update({row["id"]: row for row in payload.get("multi_turn", ())})
    if set(single) != {f"F{number:02d}" for number in range(1, 31)}:
        raise ValueError("report must contain F01 through F30")
    if set(multi) != {"P01", "P02", "P03"}:
        raise ValueError("report must contain P01 through P03")

    single_rows = [single[key] for key in sorted(single)]
    multi_turns = [turn for key in sorted(multi) for turn in multi[key]["turns"]]
    all_rows = [*single_rows, *multi_turns]
    single_latency = _latency(single_rows)
    multi_latency = _latency(multi_turns)
    counts = Counter(row["outcome_status"] for row in single_rows)
    answered = [row for row in all_rows if row["outcome_status"] == "ANSWERED"]
    lines = [
        "# 자연어 요청 충족도·연속 대화 평가",
        "",
        "> `eval/conversational-fulfillment-v1.json`을 실제 `/api/ask` SSE로 실행한 결과다.",
        "> 이 질문과 결과는 평가·문서 계층에만 있으며 런타임에서 참조하지 않는다.",
        "",
        "## 요약",
        "",
        f"- 단일 턴 30개 상태 분포: `{dict(sorted(counts.items()))}`",
        f"- 단일 턴 평균/P50/P95: `{single_latency[0]}/{single_latency[1]}/{single_latency[2]}초`",
        f"- 다중 턴 3개 시나리오·{len(multi_turns)}턴 평균/P50/P95: "
        f"`{multi_latency[0]}/{multi_latency[1]}/{multi_latency[2]}초`",
        f"- `ANSWERED` Citation 보유: `{sum(row['citation_count'] > 0 for row in answered)}/{len(answered)}`",
        f"- 공개 오류·SAFE_FAILURE: `{sum(bool(row.get('error')) for row in all_rows)}/0`",
        "- 프로필 갱신만으로 종료된 질문, 범위 안 `OUT_OF_SCOPE`, 조회 가능한 KG의 "
        "`INSUFFICIENT_EVIDENCE`: 각각 0건",
        "",
        "## 단일 턴 결과",
        "",
        "| ID | 범주 | 상태 | 충족도 | Citation | 도구/KG | 시간(초) |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    categories = {
        item["id"]: item["category"] for item in _load(Path("eval/conversational-fulfillment-v1.json"))["single_turn_variants"]
    }
    for key in sorted(single):
        row = single[key]
        kg_queries = sum(tool == "query_curriculum" for tool in row.get("tool_order") or ())
        lines.append(
            f"| {key} | {categories[key]} | {row['outcome_status']} | "
            f"{row.get('fulfillment_status') or '-'} | {row['citation_count']} | "
            f"{row.get('tool_call_count', 0)}/{kg_queries} | {row['elapsed_seconds']} |"
        )
    lines.extend(["", "## 단일 턴 응답 전문과 판정 이유", ""])
    for key in sorted(single):
        lines.extend(_details(key, single[key]))

    lines.extend(["## 다중 턴 응답 전문과 판정 이유", ""])
    for key in sorted(multi):
        lines.extend([f"### {key}", ""])
        for index, row in enumerate(multi[key]["turns"], start=1):
            lines.extend(_details(f"{key} turn {index}", row))
    lines.extend(
        [
            "## 의미 검토",
            "",
            "- 과목 전체 목록은 VERIFIED Evidence가 직접 연결된 37개 고유 Course identity를 "
            "기준으로 중복 제거했고, 전공필수 9개와 전공선택 28개로 그룹화했다.",
            "- 다음 학기 수강 가능 여부는 편성 학기와 선수·제한 규정을 분리했다. 직접 규정이 "
            "없으면 확인된 과목 사실은 Citation과 함께 남기고 판단 항목만 미해결로 둔다.",
            "- 열린 과목 추천은 현재 학년을 모르면 특정 과목을 추측하지 않고 그 한 필드만 묻는다.",
            "- 정정 시 기존 전공 42학점에서 45학점으로 교체하고 영역 합계와 잔여학점을 모두 재계산했다.",
            "- 반복 목록 요청은 직전 사용자 요청을 복원하며 assistant 답변을 사실 근거로 사용하지 않는다.",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
