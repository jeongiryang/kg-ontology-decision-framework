"""Per-request runtime trace, separate from team AI simulation logs."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from .schema_catalog import ROOT


DEFAULT_TRACE_DIR = ROOT / "logs/query-runs"


class TraceStage(StrEnum):
    PLAN_VALIDATION = "PLAN_VALIDATION"
    SCHEMA_SELECTION = "SCHEMA_SELECTION"
    CYPHER_VALIDATION = "CYPHER_VALIDATION"
    NEO4J_EXPLAIN = "NEO4J_EXPLAIN"
    EXECUTION = "EXECUTION"
    RESULT_VALIDATION = "RESULT_VALIDATION"


class TraceStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class TraceEvent:
    stage: str
    status: str
    duration_ms: float
    error_code: str | None = None
    row_count: int | None = None


class QueryTrace:
    def __init__(
        self,
        *,
        question: str,
        parameters: Mapping[str, Any],
        trace_dir: Path = DEFAULT_TRACE_DIR,
    ):
        self.request_id = str(uuid.uuid4())
        self.created_at = datetime.now(UTC).isoformat()
        self.question = question
        self.parameters = self._sanitize(parameters)
        self.trace_dir = trace_dir
        self.ontology_version: str | None = None
        self.events: list[TraceEvent] = []

    def record(
        self,
        stage: TraceStage,
        status: TraceStatus,
        duration_ms: float,
        *,
        error_code: str | None = None,
        row_count: int | None = None,
    ) -> None:
        self.events.append(
            TraceEvent(stage.value, status.value, round(duration_ms, 3), error_code, row_count)
        )

    def skip_after(self, failed_stage: TraceStage) -> None:
        stages = list(TraceStage)
        for stage in stages[stages.index(failed_stage) + 1 :]:
            if not any(event.stage == stage.value for event in self.events):
                self.record(stage, TraceStatus.SKIPPED, 0.0)

    def write(self) -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        path = self.trace_dir / f"{self.request_id}.json"
        payload = {
            "request_id": self.request_id,
            "created_at": self.created_at,
            "ontology_version": self.ontology_version,
            "question": self.question,
            "parameters": self.parameters,
            "events": [asdict(event) for event in self.events],
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _sanitize(parameters: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in parameters.items():
            if any(marker in key.lower() for marker in ("password", "token", "secret", "key")):
                result[key] = "<redacted>"
            else:
                result[key] = value
        return result
