"""Render the 50-question before/after SSE evaluation as a Markdown table."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


CATEGORIES = (
    (range(1, 9), "현재 이수 내역 분석"),
    (range(9, 15), "학번·교육과정 적용"),
    (range(15, 26), "교양 이수요건"),
    (range(26, 32), "영어 면제·졸업인증"),
    (range(32, 41), "전공필수·전공선택"),
    (range(41, 47), "학년·학기·수강신청"),
    (range(47, 51), "진로·교과목 추천"),
)

EXPECTED: dict[int, str] = {
    1: "NEEDS_USER_INFO",
    2: "NEEDS_USER_INFO",
    3: "ANSWERED",
    4: "ANSWERED",
    5: "ANSWERED",
    6: "INSUFFICIENT_EVIDENCE",
    7: "INSUFFICIENT_EVIDENCE",
    8: "NEEDS_USER_INFO",
    9: "INSUFFICIENT_EVIDENCE",
    10: "OUT_OF_SCOPE",
    11: "ANSWERED",
    12: "INSUFFICIENT_EVIDENCE",
    13: "INSUFFICIENT_EVIDENCE",
    14: "NEEDS_USER_INFO",
    15: "NEEDS_USER_INFO",
    16: "ANSWERED",
    17: "ANSWERED",
    18: "INSUFFICIENT_EVIDENCE",
    19: "INSUFFICIENT_EVIDENCE",
    20: "ANSWERED",
    21: "ANSWERED",
    22: "ANSWERED",
    23: "ANSWERED",
    24: "ANSWERED",
    25: "INSUFFICIENT_EVIDENCE",
    26: "ANSWERED",
    27: "INSUFFICIENT_EVIDENCE",
    28: "ANSWERED",
    29: "ANSWERED",
    30: "ANSWERED",
    31: "ANSWERED",
    32: "INSUFFICIENT_EVIDENCE",
    33: "INSUFFICIENT_EVIDENCE",
    34: "ANSWERED",
    35: "ANSWERED",
    36: "ANSWERED",
    37: "ANSWERED",
    38: "ANSWERED",
    39: "INSUFFICIENT_EVIDENCE",
    40: "ANSWERED",
    41: "INSUFFICIENT_EVIDENCE",
    42: "INSUFFICIENT_EVIDENCE",
    43: "INSUFFICIENT_EVIDENCE",
    44: "ADVISORY",
    45: "ADVISORY",
    46: "INSUFFICIENT_EVIDENCE",
    47: "ADVISORY",
    48: "ADVISORY",
    49: "ADVISORY",
    50: "ADVISORY",
}


def _load(path: Path) -> dict[int, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {item["number"]: item for item in payload["results"]}


def _category(number: int) -> str:
    return next(label for numbers, label in CATEGORIES if number in numbers)


def _cell(value: Any, limit: int = 180) -> str:
    text = str(value or "-").replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _executed(item: dict[str, Any]) -> str:
    return (
        "예"
        if any(
            event.get("phase") == "GRAPH_EXECUTION"
            and event.get("state") == "COMPLETED"
            for event in item.get("progress", ())
        )
        else "아니요"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True)
    parser.add_argument("--override", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline = _load(args.baseline)
    final = _load(args.final)
    for override in args.override:
        final.update(_load(override))
    if set(baseline) != set(range(1, 51)) or set(final) != set(range(1, 51)):
        raise ValueError("baseline and final inputs must cover all 50 questions")

    baseline_counts = Counter(item["status"] for item in baseline.values())
    final_counts = Counter(item["status"] for item in final.values())
    passed = sum(final[number]["status"] == EXPECTED[number] for number in range(1, 51))
    baseline_seconds = sum(item.get("elapsed_seconds") or 0 for item in baseline.values())
    final_seconds = sum(item.get("elapsed_seconds") or 0 for item in final.values())

    lines = [
        "# 챗봇 평가 질문셋 V1 기준선·최종 결과",
        "",
        "> 원문은 PR #10 `eval/question-set-v1.md`를 실행 시점에 직접 읽었다. 이 문서는 "
        "평가 결과이며 런타임 질문 분기나 정답 테이블로 사용되지 않는다.",
        "",
        "## 요약",
        "",
        f"- 기준선: `{dict(sorted(baseline_counts.items()))}`, 총 {baseline_seconds:.3f}초",
        f"- 최종: `{dict(sorted(final_counts.items()))}`, 총 {final_seconds:.3f}초",
        f"- 기대 상태 일치: **{passed}/50**",
        "- 각 문항은 빈 프로필·빈 clarification 상태의 독립 SSE 요청으로 실행했다.",
        "- `ANSWERED`만 확정 답변이며, 나머지 상태도 근거와 지원 범위에 맞으면 통과다.",
        "",
        "## 50문항 결과",
        "",
        "| 번호 | 대분류 | 질문 | 기대 | 기준선 | 최종 | 통과 | 응답 요약 | 사용자 정보 | Citation | Cypher 실행 | 제한 이유 |",
        "|---:|---|---|---|---|---|:---:|---|---|---:|:---:|---|",
    ]
    for number in range(1, 51):
        old = baseline[number]
        new = final[number]
        expected = EXPECTED[number]
        used = new.get("used_profile_fields") or new.get("changed_profile_fields") or []
        limitation = new.get("limitations") or new.get("required_user_fields") or []
        lines.append(
            "| "
            + " | ".join(
                [
                    str(number),
                    _category(number),
                    _cell(new["question"], 120),
                    expected,
                    _cell(old["status"], 40),
                    _cell(new["status"], 40),
                    "PASS" if new["status"] == expected else "FAIL",
                    _cell(new.get("answer_text")),
                    _cell(", ".join(used) if used else "없음", 100),
                    str(new.get("citation_count", 0)),
                    _executed(new),
                    _cell(", ".join(limitation) if limitation else "없음", 140),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## 판정 원칙",
            "",
            "- 확정 사실은 `VERIFIED` Fact와 직접 연결된 `VERIFIED Evidence`를 요구했다.",
            "- 사용자 학점·과목은 `USER_ASSERTION`으로만 사용하고 KG 사실과 합치지 않았다.",
            "- 실시간 수강신청, 성적·재수강, 전과·휴복학 적용처럼 근거가 없는 판단은 "
            "`INSUFFICIENT_EVIDENCE`로 남겼다.",
            "- 추천은 조회된 과목 사실과 조건부 판단을 분리해 `ADVISORY`로 표시했다.",
        ]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
