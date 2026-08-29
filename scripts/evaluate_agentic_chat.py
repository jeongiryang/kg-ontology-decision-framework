"""Run the held-out single/multi-turn set through the real Starlette SSE route."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SET = ROOT / "eval/agentic-generalization-v1.json"


def _id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"


def _empty_profile() -> dict[str, Any]:
    return {"version": 1}


def _empty_context() -> dict[str, Any]:
    return {
        "version": 1,
        "conversation_id": _id("conversation"),
        "turn_id": _id("turn"),
        "recent_messages": [],
        "summary": "",
        "current_topic": None,
        "recent_course_codes": [],
        "recent_evidence_ids": [],
        "pending_clarification": None,
    }


def _call(
    base_url: str,
    question: str,
    profile: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    body = json.dumps(
        {"question": question, "profile": profile, "conversation": context},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/ask",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=240) as response:
        raw = response.read().decode("utf-8")
    events = [
        json.loads(line[6:])
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]
    result = next((item for item in events if item.get("type") == "result"), None)
    if result is None:
        result = {
            "type": "result",
            "response": {"status": "MISSING_RESULT", "citations": []},
        }
    return result, events, time.perf_counter() - started


def _turn(
    base_url: str,
    question: str,
    profile: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    result, events, elapsed = _call(base_url, question, profile, context)
    outcome = next(
        (item for item in events if item.get("type") == "outcome"),
        {"status": "MISSING_OUTCOME", "message": ""},
    )
    update = next(
        (item for item in events if item.get("type") == "conversation_update"),
        None,
    )
    profile_update = next(
        (item for item in events if item.get("type") == "profile_update"),
        None,
    )
    wire = result["response"]
    trace = [item for item in events if item.get("type") == "agent_trace"]
    narrative = next(
        (item.get("metadata", {}) for item in reversed(trace)
         if item.get("tool") == "grounded_narrative"),
        {},
    )
    record = {
        "question": question,
        "outcome_status": outcome.get("status"),
        "wire_status": wire.get("status"),
        "answer": update.get("display_answer") if update else outcome.get("message"),
        "citation_count": len(wire.get("citations") or []),
        "tool_order": [item.get("tool") for item in trace],
        "agent_trace": trace,
        "tool_call_count": len(trace),
        "evidence_assessment_count": sum(
            item.get("tool") == "assess_evidence" for item in trace
        ),
        "narrative_metrics": narrative,
        "cypher_executed": any(
            item.get("type") == "progress" and item.get("phase") == "GRAPH_EXECUTION"
            for item in events
        ),
        "elapsed_seconds": round(elapsed, 3),
        "error": next(
            (item.get("message") for item in events if item.get("type") == "error"),
            None,
        ),
        "recent_course_codes": list(update.get("recent_course_codes", []))
        if update
        else [],
    }
    next_profile = profile_update["profile"] if profile_update else profile
    if update is None:
        return record, next_profile, context
    now = update["created_at"]
    recent = list(context["recent_messages"])
    recent.extend(
        [
            {
                "turn_id": context["turn_id"],
                "role": "user",
                "content": question,
                "created_at": now,
                "response_status": None,
                "citation_ids": [],
                "evidence_ids": [],
            },
            {
                "turn_id": context["turn_id"],
                "role": "assistant",
                "content": update["display_answer"],
                "created_at": now,
                "response_status": update["response_status"],
                "citation_ids": update["citation_ids"],
                "evidence_ids": update["evidence_ids"],
            },
        ]
    )
    next_context = {
        "version": 1,
        "conversation_id": context["conversation_id"],
        "turn_id": _id("turn"),
        "recent_messages": recent[-8:],
        "summary": update["summary"],
        "current_topic": update["current_topic"],
        "recent_course_codes": update["recent_course_codes"],
        "recent_evidence_ids": update["evidence_ids"],
        "pending_clarification": update.get("pending_clarification"),
    }
    return record, next_profile, next_context


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", type=Path, default=DEFAULT_SET)
    parser.add_argument("--base-url", default="http://127.0.0.1:8501")
    parser.add_argument("--mode", choices=("single", "multi", "all"), default="all")
    parser.add_argument(
        "--ids",
        default="",
        help="comma-separated evaluation IDs; empty runs every item in the selected mode",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.set.read_text(encoding="utf-8"))
    selected_ids = {item.strip() for item in args.ids.split(",") if item.strip()}
    report: dict[str, Any] = {"version": 1, "single_turn": [], "multi_turn": []}
    if args.mode in {"single", "all"}:
        for item in source["single_turn_variants"]:
            if selected_ids and item["id"] not in selected_ids:
                continue
            record, _, _ = _turn(
                args.base_url, item["question"], _empty_profile(), _empty_context()
            )
            report["single_turn"].append({"id": item["id"], **record})
            print(item["id"], record["outcome_status"], record["citation_count"], flush=True)
    if args.mode in {"multi", "all"}:
        for scenario in source["multi_turn_scenarios"]:
            if selected_ids and scenario["id"] not in selected_ids:
                continue
            profile = _empty_profile()
            context = _empty_context()
            turns = []
            for question in scenario["turns"]:
                record, profile, context = _turn(
                    args.base_url, question, profile, context
                )
                turns.append(record)
            report["multi_turn"].append({"id": scenario["id"], "turns": turns})
            print(
                scenario["id"],
                ",".join(str(item["outcome_status"]) for item in turns),
                flush=True,
            )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
