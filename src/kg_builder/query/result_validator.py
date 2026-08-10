"""Validate dynamic query rows before they can become grounded answers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .cypher_validator import ProvenanceContract
from .query_plan import QueryPlan


class ResultValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedResult:
    rows: tuple[dict[str, Any], ...]
    row_count: int
    evidence_count: int


class ResultValidator:
    EVIDENCE_FIELDS = frozenset(
        {
            "evidence_id",
            "excerpt_page",
            "source_pdf_page",
            "printed_page",
            "source_text",
            "fact_status",
            "evidence_verification_status",
        }
    )

    def __init__(
        self,
        *,
        max_rows: int = 100,
        max_row_bytes: int = 65_536,
        max_response_bytes: int = 1_048_576,
    ):
        self.max_rows = max_rows
        self.max_row_bytes = max_row_bytes
        self.max_response_bytes = max_response_bytes

    def validate(
        self,
        plan: QueryPlan,
        rows: list[dict[str, Any]],
        provenance: ProvenanceContract,
    ) -> ValidatedResult:
        if len(rows) > self.max_rows:
            self._fail("RESULT_LIMIT_EXCEEDED", f"result exceeds {self.max_rows} rows")
        if not rows:
            return ValidatedResult((), 0, 0)
        required = set(plan.requested_fields) | set(plan.filters) | {"fact_id", "fact_label"}
        if plan.evidence_required:
            required.update(self.EVIDENCE_FIELDS)
        seen_rows: set[str] = set()
        seen_fact_evidence: set[tuple[str, str]] = set()
        evidence_ids: set[str] = set()
        total_bytes = 0
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                self._fail("RESULT_NOT_OBJECT", f"row {index} is not an object")
            missing = required - set(row)
            if missing:
                self._fail(
                    "RESULT_FIELD_MISSING", f"row {index} lacks fields: {sorted(missing)}"
                )
            null_fields = {field for field in plan.requested_fields if row.get(field) is None}
            if null_fields:
                self._fail(
                    "RESULT_FIELD_NULL", f"row {index} has null requested fields: {sorted(null_fields)}"
                )
            self._validate_scope(index, plan, row)
            self._validate_fact(index, row, provenance)
            if plan.evidence_required:
                if row["fact_status"] != "VERIFIED":
                    self._fail("RESULT_FACT_NOT_VERIFIED", f"row {index} fact is not VERIFIED")
                if row["evidence_verification_status"] != "VERIFIED":
                    self._fail(
                        "RESULT_EVIDENCE_NOT_VERIFIED", f"row {index} Evidence is not VERIFIED"
                    )
                self._validate_evidence(index, row)
                evidence_ids.add(row["evidence_id"])
                pair = (row["fact_id"], row["evidence_id"])
                if pair in seen_fact_evidence:
                    self._fail(
                        "RESULT_DUPLICATE_PROVENANCE",
                        f"row {index} duplicates a fact-Evidence pair",
                    )
                seen_fact_evidence.add(pair)
            signature = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            row_bytes = len(signature.encode("utf-8"))
            if row_bytes > self.max_row_bytes:
                self._fail(
                    "RESULT_ROW_BYTES_EXCEEDED",
                    f"row {index} exceeds {self.max_row_bytes} serialized bytes",
                )
            total_bytes += row_bytes
            if total_bytes > self.max_response_bytes:
                self._fail(
                    "RESULT_TOTAL_BYTES_EXCEEDED",
                    f"result exceeds {self.max_response_bytes} serialized bytes",
                )
            if signature in seen_rows:
                self._fail("RESULT_DUPLICATE_ROW", f"row {index} duplicates a prior row")
            seen_rows.add(signature)
        return ValidatedResult(tuple(rows), len(rows), len(evidence_ids))

    def _validate_fact(
        self, index: int, row: dict[str, Any], provenance: ProvenanceContract
    ) -> None:
        if not isinstance(row["fact_id"], str) or not row["fact_id"].strip():
            self._fail("RESULT_FACT_INVALID", f"row {index} has invalid fact_id")
        if row["fact_label"] != provenance.fact_label:
            self._fail(
                "RESULT_FACT_INVALID",
                f"row {index} fact_label differs from validated provenance",
            )

    def _validate_scope(self, index: int, plan: QueryPlan, row: dict[str, Any]) -> None:
        for name, expected in plan.filters.items():
            actual = row.get(name)
            if name == "grade_year" and isinstance(actual, list):
                matches = expected in actual
            elif name == "rule_ids" and isinstance(expected, list):
                matches = actual in expected
            else:
                matches = actual == expected
            if not matches:
                self._fail(
                    "RESULT_SCOPE_MISMATCH",
                    f"row {index} scope {name} differs from QueryPlan",
                )

    def _validate_evidence(self, index: int, row: dict[str, Any]) -> None:
        if not isinstance(row["evidence_id"], str) or not row["evidence_id"].strip():
            self._fail("RESULT_EVIDENCE_INVALID", f"row {index} has invalid evidence_id")
        for name in ("excerpt_page", "source_pdf_page", "printed_page"):
            if isinstance(row[name], bool) or not isinstance(row[name], int) or row[name] < 1:
                self._fail("RESULT_EVIDENCE_INVALID", f"row {index} has invalid {name}")
        if not isinstance(row["source_text"], str) or not row["source_text"].strip():
            self._fail("RESULT_EVIDENCE_INVALID", f"row {index} has empty source_text")

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise ResultValidationError(code, message)
