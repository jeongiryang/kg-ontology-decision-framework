"""Run the PR #10 Markdown question set through the public SSE endpoint.

The evaluator deliberately starts every question without profile or resolution
state.  It records only public SSE presentation data and checkpoints after each
request so a long local-model run can be resumed safely.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


QUESTION_PATTERN = re.compile(r"^(?P<number>\d+)\.\s+(?P<question>.+)$")


def _question_markdown(git_ref: str, path: str) -> str:
    completed = subprocess.run(
        ["git", "show", f"{git_ref}:{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _questions(markdown: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for line in markdown.splitlines():
        match = QUESTION_PATTERN.match(line.strip())
        if match:
            parsed.append(
                {
                    "number": int(match.group("number")),
                    "question": match.group("question"),
                }
            )
    if len(parsed) != 50 or [item["number"] for item in parsed] != list(range(1, 51)):
        raise ValueError("The evaluation source must contain questions 1 through 50 exactly once")
    return parsed


def _post_sse(url: str, question: str, timeout: float) -> tuple[list[dict[str, Any]], float]:
    request = urllib.request.Request(
        url,
        data=json.dumps({"question": question}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.monotonic()
    events: list[dict[str, Any]] = []
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data_lines:
                    payload = json.loads("\n".join(data_lines))
                    event_name = payload.get("type")
                    if not isinstance(event_name, str):
                        raise ValueError("SSE payload is missing its type discriminator")
                    events.append({"event": event_name, "data": payload})
                data_lines = []
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
    return events, round(time.monotonic() - started, 3)


def _summary(item: dict[str, Any], events: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    result_event = next((event["data"] for event in reversed(events) if event["event"] == "result"), {})
    error_event = next((event["data"] for event in reversed(events) if event["event"] == "error"), {})
    response = result_event.get("response", {}) if isinstance(result_event, dict) else {}
    presentation = result_event.get("presentation", {}) if isinstance(result_event, dict) else {}
    clarification = next(
        (event["data"] for event in events if event["event"] == "clarification_options"),
        None,
    )
    inspections = [event["data"] for event in events if event["event"] == "inspection_update"]
    progress = [event["data"] for event in events if event["event"] == "progress"]
    profile_update = next(
        (event["data"] for event in reversed(events) if event["event"] == "profile_update"),
        {},
    )
    outcome = next(
        (event["data"] for event in reversed(events) if event["event"] == "outcome"),
        {},
    )
    fulfillment = next(
        (
            event["data"]
            for event in reversed(events)
            if event["event"] == "request_fulfillment"
        ),
        {},
    )
    citations = response.get("citations") or []
    return {
        **item,
        "status": outcome.get("status") or response.get("status") or "HTTP_ERROR",
        "chat_status": response.get("status"),
        "answer_text": outcome.get("message") or response.get("answer_text") or error_event.get("message"),
        "profile": profile_update.get("profile"),
        "changed_profile_fields": profile_update.get("changed_fields") or [],
        "required_user_fields": outcome.get("required_user_fields") or [],
        "used_profile_fields": outcome.get("used_profile_fields") or [],
        "limitations": outcome.get("limitations") or [],
        "fulfillment_status": fulfillment.get("status"),
        "requested_items": fulfillment.get("requested_items") or [],
        "citation_count": len(citations),
        "citations": citations,
        "clarification": response.get("clarification"),
        "clarification_options": clarification,
        "selected_schema": presentation.get("schema"),
        "approved_cypher": presentation.get("approved_cypher"),
        "inspection_updates": inspections,
        "progress": progress,
        "elapsed_seconds": elapsed,
        "error": error_event or None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--git-ref", default="origin/docs/evaluate-question")
    parser.add_argument("--source", default="eval/question-set-v1.md")
    parser.add_argument("--url", default="http://127.0.0.1:8501/api/ask")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--numbers",
        help="comma-separated question numbers for a focused rerun",
    )
    args = parser.parse_args()

    source = _question_markdown(args.git_ref, args.source)
    questions = _questions(source)
    if args.numbers:
        selected = {int(value.strip()) for value in args.numbers.split(",")}
        if not selected or not selected.issubset(range(1, 51)):
            raise ValueError("--numbers must contain values from 1 through 50")
        questions = [item for item in questions if item["number"] in selected]
    results: list[dict[str, Any]] = []
    if args.resume and args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        results = list(existing.get("results", []))
    completed_numbers = {item["number"] for item in results}

    for item in questions:
        if item["number"] in completed_numbers:
            continue
        try:
            events, elapsed = _post_sse(args.url, item["question"], args.timeout)
            summary = _summary(item, events, elapsed)
        except Exception as exc:  # the failure itself is part of the evaluation result
            summary = {
                **item,
                "status": "TRANSPORT_ERROR",
                "answer_text": None,
                "citation_count": 0,
                "citations": [],
                "clarification": None,
                "clarification_options": None,
                "selected_schema": None,
                "approved_cypher": None,
                "inspection_updates": [],
                "progress": [],
                "elapsed_seconds": None,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        results.append(summary)
        payload = {
            "source": {"git_ref": args.git_ref, "path": args.source},
            "endpoint": args.url,
            "fresh_state_per_question": True,
            "results": sorted(results, key=lambda result: result["number"]),
        }
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"[{item['number']:02d}/50] {summary['status']} "
            f"citations={summary['citation_count']} elapsed={summary['elapsed_seconds']}s",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
