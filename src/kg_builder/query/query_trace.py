"""Per-request runtime trace, separate from team AI simulation logs."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from .schema_catalog import ROOT


DEFAULT_TRACE_DIR = ROOT / "logs/query-runs"
DEFAULT_RETENTION_DAYS = 30
EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_PATTERN = re.compile(r"(?<!\d)01[016789][ -]?\d{3,4}[ -]?\d{4}(?!\d)")
STUDENT_ID_PATTERN = re.compile(r"(?<!\d)\d{8,10}(?!\d)")


class TraceStage(StrEnum):
    PLANNING = "PLANNING"
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
class TracePolicy:
    store_raw_question: bool = False
    fingerprint_enabled: bool = False
    fingerprint_hmac_key: str | None = field(default=None, repr=False)
    retention_days: int = DEFAULT_RETENTION_DAYS

    @classmethod
    def from_env(cls) -> "TracePolicy":
        raw_setting = os.getenv("KG_QUERY_TRACE_RAW_QUESTION", "false").strip().lower()
        if raw_setting not in {"true", "false"}:
            raise ValueError("KG_QUERY_TRACE_RAW_QUESTION must be true or false")
        fingerprint_setting = os.getenv(
            "KG_QUERY_TRACE_FINGERPRINT", "false"
        ).strip().lower()
        if fingerprint_setting not in {"true", "false"}:
            raise ValueError("KG_QUERY_TRACE_FINGERPRINT must be true or false")
        fingerprint_enabled = fingerprint_setting == "true"
        fingerprint_hmac_key = os.getenv("KG_QUERY_TRACE_HMAC_KEY")
        if fingerprint_enabled and not fingerprint_hmac_key:
            raise ValueError(
                "KG_QUERY_TRACE_HMAC_KEY is required when fingerprinting is enabled"
            )
        retention_text = os.getenv(
            "KG_QUERY_TRACE_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)
        ).strip()
        try:
            retention_days = int(retention_text)
        except ValueError as exc:
            raise ValueError("KG_QUERY_TRACE_RETENTION_DAYS must be an integer") from exc
        if not 1 <= retention_days <= 365:
            raise ValueError("KG_QUERY_TRACE_RETENTION_DAYS must be between 1 and 365")
        return cls(
            store_raw_question=raw_setting == "true",
            fingerprint_enabled=fingerprint_enabled,
            fingerprint_hmac_key=fingerprint_hmac_key if fingerprint_enabled else None,
            retention_days=retention_days,
        )


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
        store_raw_question: bool = False,
        fingerprint_hmac_key: str | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        self.request_id = str(uuid.uuid4())
        self.created_at = datetime.now(UTC).isoformat()
        self.question_length = len(question)
        self.question_fingerprint = (
            hmac.new(
                fingerprint_hmac_key.encode("utf-8"),
                question.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            if fingerprint_hmac_key is not None
            else None
        )
        self.raw_question = self._mask_question(question) if store_raw_question else None
        self.parameters = self._sanitize(parameters)
        self.trace_dir = trace_dir
        self.retention_days = retention_days
        self.ontology_version: str | None = None
        self.events: list[TraceEvent] = []
        # 계획 단계에서 모델이 무엇을 냈고 어느 계약에 걸렸는지. 이 구간이 비어 있으면
        # 계획 실패의 원인을 사후에 좁힐 수 없어 추정으로 고치게 된다.
        self.planning_attempts: list[dict[str, Any]] = []

    def record_planning(self, attempts: Any) -> None:
        """Keep the schema-level shape of each planning attempt.

        질문에서 온 값은 담지 않는다. 어느 모드를 골랐는지, 어떤 필터 이름을 채웠는지,
        어느 계약 문구에 걸렸는지만 남긴다. 값 자체는 원문 질문과 같은 성격이라
        ``store_raw_question`` 정책을 따르는 raw_question 쪽에만 노출된다.
        """

        for attempt in attempts or ():
            payload = attempt if isinstance(attempt, Mapping) else asdict(attempt)
            self.planning_attempts.append(dict(payload))

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
            "question_length": self.question_length,
            "parameters": self.parameters,
            "retention_days": self.retention_days,
            "access_policy": "application-operator-only",
            "events": [asdict(event) for event in self.events],
        }
        if self.planning_attempts:
            payload["planning_attempts"] = self.planning_attempts
        if self.raw_question is not None:
            payload["raw_question"] = self.raw_question
        if self.question_fingerprint is not None:
            payload["question_fingerprint"] = self.question_fingerprint
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

    @staticmethod
    def _mask_question(question: str) -> str:
        masked = EMAIL_PATTERN.sub("<redacted-email>", question)
        masked = PHONE_PATTERN.sub("<redacted-phone>", masked)
        return STUDENT_ID_PATTERN.sub("<redacted-student-id>", masked)
