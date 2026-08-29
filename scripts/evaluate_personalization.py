"""Exercise browser-profile scenarios through the public SSE endpoint."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


def _ask(url: str, question: str, profile: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {"question": question, "profile": profile}, ensure_ascii=False
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=240) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    result = next(item for item in reversed(events) if item["type"] == "result")
    outcome = next(item for item in reversed(events) if item["type"] == "outcome")
    updated = next(item for item in reversed(events) if item["type"] == "profile_update")
    return {
        "status": outcome["status"],
        "chat_status": result["response"]["status"],
        "message": outcome["message"],
        "required_user_fields": outcome["required_user_fields"],
        "used_profile_fields": outcome["used_profile_fields"],
        "changed_fields": updated["changed_fields"],
        "profile": updated["profile"],
        "citation_count": len(result["response"]["citations"]),
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8501/api/ask")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    scenarios: list[dict[str, Any]] = []

    def run(name: str, question: str, profile: dict[str, Any]) -> dict[str, Any]:
        result = _ask(args.url, question, profile)
        scenarios.append({"name": name, "question": question, **result})
        print(f"{name}: {result['status']} citations={result['citation_count']}")
        return result

    empty = {"version": 1}
    missing = run("1-profile-missing", "졸업까지 몇 학점 남았어?", empty)
    del missing
    supplied = {
        "version": 1,
        "credits": {"total": 60},
    }
    answered = run("2-profile-supplied", "졸업까지 몇 학점 남았어?", supplied)
    restored = json.loads(json.dumps(answered["profile"], ensure_ascii=False))
    run("3-restored-after-reload", "졸업까지 몇 학점 남았어?", restored)
    stated = run(
        "4-chat-profile-extraction",
        "2026학번 컴퓨터공학과야. 자료구조와 이산수학을 들었고 전공 42학점이야.",
        empty,
    )
    run(
        "5-follow-up-reuses-profile",
        "내가 들은 과목 중 전공선택은 뭐야?",
        stated["profile"],
    )
    corrected = run(
        "6-latest-correction-wins",
        "방금 말한 전공학점은 42가 아니라 45학점이야.",
        stated["profile"],
    )
    run("7-reset-removes-profile", "내가 들은 과목 중 전공선택은 뭐야?", empty)
    run(
        "8-conflict-needs-clarification",
        "전공 42학점이고 전공 45학점이야.",
        corrected["profile"],
    )
    args.output.write_text(
        json.dumps({"endpoint": args.url, "scenarios": scenarios}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
