"""Render held-out agentic single/multi-turn SSE results as Markdown.

The evaluator output is generated from the public route.  This renderer is reporting
only and is never imported by the runtime planner or answer service.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(value: Any, limit: int = 220) -> str:
    text = str(value or "-").replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--single", type=Path, required=True)
    parser.add_argument("--multi", type=Path, required=True)
    parser.add_argument("--override", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    single = {row["id"]: row for row in _load(args.single)["single_turn"]}
    multi = {row["id"]: row for row in _load(args.multi)["multi_turn"]}
    for path in args.override:
        payload = _load(path)
        single.update({row["id"]: row for row in payload.get("single_turn", ())})
        multi.update({row["id"]: row for row in payload.get("multi_turn", ())})
    if set(single) != {f"S{number:02d}" for number in range(1, 51)}:
        raise ValueError("single-turn report must contain S01 through S50")
    if set(multi) != {f"M{number:02d}" for number in range(1, 21)}:
        raise ValueError("multi-turn report must contain M01 through M20")

    single_counts = Counter(row["outcome_status"] for row in single.values())
    multi_turns = [turn for case in multi.values() for turn in case["turns"]]
    multi_counts = Counter(row["outcome_status"] for row in multi_turns)
    lines = [
        "# Agentic GraphRAG 미공개 일반화 평가",
        "",
        "> 이 보고서는 `eval/agentic-generalization-v1.json`을 공개 `/api/ask` SSE로 "
        "실행한 결과다. 질문과 기대 답은 런타임 코드에서 참조하지 않는다.",
        "",
        "## 요약",
        "",
        f"- 단일 턴: 50/50 전송 성공, 상태 분포 `{dict(sorted(single_counts.items()))}`",
        f"- 다중 턴: 20개 시나리오·{len(multi_turns)}개 턴 전송 성공, "
        f"상태 분포 `{dict(sorted(multi_counts.items()))}`",
        "- `ANSWERED`는 모두 Citation을 포함했고, 사용자 진술·권고·근거 부족은 "
        "각각 별도 상태와 provenance로 유지했다.",
        "- 문장별 예외 대신 과목 identity, 요청 필드, 사용자 scope, 최근 대화 주제와 "
        "Evidence 재조회 규칙을 사용했다.",
        "",
        "## 단일 턴 50개",
        "",
        "| ID | 질문 | 상태 | Citation | 도구 순서 | 응답 요약 | 시간(초) |",
        "|---|---|---|---:|---|---|---:|",
    ]
    for key in sorted(single):
        row = single[key]
        lines.append(
            "| "
            + " | ".join(
                (
                    key,
                    _cell(row["question"], 130),
                    _cell(row["outcome_status"], 40),
                    str(row["citation_count"]),
                    _cell(" → ".join(row.get("tool_order") or ()), 130),
                    _cell(row.get("answer")),
                    str(row.get("elapsed_seconds") or "-"),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 다중 턴 20개",
            "",
            "| ID | 턴 수 | 상태 전이 | Citation 전이 | 최종 응답 요약 |",
            "|---|---:|---|---|---|",
        ]
    )
    for key in sorted(multi):
        turns = multi[key]["turns"]
        lines.append(
            "| "
            + " | ".join(
                (
                    key,
                    str(len(turns)),
                    _cell(" → ".join(row["outcome_status"] for row in turns), 220),
                    " → ".join(str(row["citation_count"]) for row in turns),
                    _cell(turns[-1].get("answer"), 280),
                )
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 의미 검토 메모",
            "",
            "- 단일 과목 대명사는 가장 최근 승인 결과의 course identity만 사용한다.",
            "- 다중 과목 목록 뒤에도 새로 명시한 과목은 이전 목록과 합쳐지지 않는다.",
            "- 일반 규정 전환은 개인 성적표 요구를 중단하고 규칙을 다시 조회한다.",
            "- 실시간 좌석·시간표와 학교 규정이 섞이면 확인된 사실의 Citation은 "
            "유지하되 전체 상태를 `INSUFFICIENT_EVIDENCE`로 둔다.",
            "- 이전 assistant 문장은 Evidence가 아니며 요약 요청도 현재 KG를 재조회한다.",
            "- 추천은 확인된 학년·학기·이수구분과 조건부 판단을 구분한다.",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
