"""Build immutable semantic Claims from validated dynamic query rows."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Mapping, Sequence

from kg_builder.query.query_plan import SelectionMode

from .contracts import (
    ClaimPolarity,
    ClaimSubject,
    ClaimType,
    CourseClaimItem,
    FactEvidenceLink,
    GroundedClaim,
    GroundingError,
)


COURSE_FIELDS = frozenset(
    {"course_code", "name_ko", "grade_year", "semester", "credits", "completion_type"}
)
RULE_FIELDS = frozenset(
    {"rule_id", "rule_type", "operator", "value", "unit", "description_ko"}
)


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    return value


def _claim_id(kind: str, field: str, fact_ids: Sequence[str]) -> str:
    digest = hashlib.sha256("\x1f".join(sorted(fact_ids)).encode()).hexdigest()[:16]
    return f"claim:{kind.lower()}:{field}:{digest}"


class ClaimBuilder:
    """The only component allowed to translate result values into Claim values."""

    def build(
        self,
        rows: Sequence[Mapping[str, Any]],
        query_plan: Mapping[str, Any] | None,
    ) -> tuple[GroundedClaim, ...]:
        if not rows or not isinstance(query_plan, Mapping):
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "validated rows and QueryPlan are required"
            )
        requested = query_plan.get("requested_fields")
        selection_value = query_plan.get("selection_mode")
        if not isinstance(requested, list) or not requested:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "QueryPlan requested_fields are required"
            )
        try:
            selection = SelectionMode(selection_value)
        except (TypeError, ValueError) as exc:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "unsupported result selection mode"
            ) from exc

        grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            fact_id = row.get("fact_id")
            if not isinstance(fact_id, str) or not fact_id:
                raise GroundingError("ANSWER_CLAIM_INVALID", "row has no stable fact_id")
            grouped[fact_id].append(row)
        labels = {row.get("fact_label") for row in rows}
        if labels == {"Rule"}:
            claims = self._rules(grouped, requested)
        elif labels == {"CourseOffering"}:
            claims = self._offerings(grouped, requested, selection, query_plan)
        else:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "mixed or unsupported fact labels"
            )
        if not claims:
            raise GroundingError("ANSWER_CLAIM_EMPTY", "no supported Claims were built")
        return tuple(claims)

    def _rules(
        self,
        grouped: Mapping[str, list[Mapping[str, Any]]],
        requested: Sequence[str],
    ) -> list[GroundedClaim]:
        unsupported = set(requested) - RULE_FIELDS
        if unsupported:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED",
                f"unsupported Rule fields: {sorted(unsupported)}",
            )
        claims: list[GroundedClaim] = []
        for fact_id in sorted(grouped):
            rows = grouped[fact_id]
            row = self._consistent_row(rows, RULE_FIELDS)
            provenance = self._provenance(rows)
            description = row.get("description_ko")
            if not isinstance(description, str) or not description.strip():
                raise GroundingError(
                    "ANSWER_CLAIM_INVALID", "Rule Claim requires description_ko"
                )
            rule_type = row.get("rule_type")
            if rule_type == "EXEMPTION":
                claims.append(
                    GroundedClaim(
                        _claim_id("boolean", "exemption", [fact_id]),
                        ClaimType.BOOLEAN_POLICY,
                        provenance,
                        "exemption",
                        True,
                        unit="BOOLEAN",
                        polarity=ClaimPolarity.EXEMPT,
                        description_ko=description.strip(),
                    )
                )
            elif row.get("value") is not None:
                claims.append(
                    GroundedClaim(
                        _claim_id("requirement", "value", [fact_id]),
                        ClaimType.NUMERIC_REQUIREMENT,
                        provenance,
                        "requirement_value",
                        _freeze(row["value"]),
                        unit=row.get("unit"),
                        operator=row.get("operator"),
                        description_ko=description.strip(),
                    )
                )
            else:
                claims.append(
                    GroundedClaim(
                        _claim_id("rule", "description_ko", [fact_id]),
                        ClaimType.VERIFIED_RULE_TEXT,
                        provenance,
                        "description_ko",
                        description.strip(),
                        description_ko=description.strip(),
                    )
                )
        return claims

    def _offerings(
        self,
        grouped: Mapping[str, list[Mapping[str, Any]]],
        requested: Sequence[str],
        selection: SelectionMode,
        query_plan: Mapping[str, Any],
    ) -> list[GroundedClaim]:
        unsupported = set(requested) - COURSE_FIELDS
        if unsupported:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED",
                f"unsupported CourseOffering fields: {sorted(unsupported)}",
            )
        if selection is SelectionMode.COURSE_LIST:
            return self._course_list(grouped, query_plan)
        if selection is not SelectionMode.SINGLE_COURSE or len(grouped) != 1:
            raise GroundingError(
                "ANSWER_RENDERING_UNSUPPORTED", "CourseOffering cardinality is unsupported"
            )
        fact_id = next(iter(grouped))
        rows = grouped[fact_id]
        row = self._consistent_row(rows, COURSE_FIELDS | {"course_identity"})
        subject = self._course_subject(row, fact_id)
        provenance = self._provenance(rows)
        claims: list[GroundedClaim] = []
        for field in requested:
            if field in {"course_code", "name_ko"}:
                continue
            claims.append(
                GroundedClaim(
                    _claim_id("field", field, [fact_id]),
                    ClaimType.FIELD_VALUE,
                    provenance,
                    field,
                    _freeze(row[field]),
                    subject=subject,
                    unit="CREDIT" if field == "credits" else None,
                )
            )
        return claims

    def _course_list(
        self,
        grouped: Mapping[str, list[Mapping[str, Any]]],
        query_plan: Mapping[str, Any],
    ) -> list[GroundedClaim]:
        all_links: list[FactEvidenceLink] = []
        items: list[CourseClaimItem] = []
        completion_types: set[str] = set()
        for fact_id in sorted(grouped):
            rows = grouped[fact_id]
            row = self._consistent_row(rows, COURSE_FIELDS)
            subject = self._course_subject(row, fact_id)
            credits = row.get("credits")
            if credits is not None and (
                isinstance(credits, bool) or not isinstance(credits, (int, float))
            ):
                raise GroundingError("ANSWER_CLAIM_INVALID", "credits must be numeric")
            items.append(
                CourseClaimItem(
                    fact_id,
                    subject.entity_id,
                    subject.display_name,
                    row.get("course_code"),
                    credits,
                )
            )
            all_links.extend(self._provenance(rows))
            completion_type = row.get("completion_type")
            if not isinstance(completion_type, str) or not completion_type:
                raise GroundingError(
                    "ANSWER_CLAIM_INVALID", "course list completion_type is invalid"
                )
            completion_types.add(completion_type)
        items.sort(key=lambda item: (item.course_code or "", item.display_name, item.fact_id))
        provenance = tuple(sorted(set(all_links)))
        fact_ids = [item.fact_id for item in items]
        claims = [
            GroundedClaim(
                _claim_id("list", "courses", fact_ids),
                ClaimType.COURSE_LIST,
                provenance,
                "courses",
                tuple(items),
            ),
            GroundedClaim(
                _claim_id("aggregate", "fact_count", fact_ids),
                ClaimType.AGGREGATE,
                provenance,
                "fact_count",
                len(items),
                unit="COURSE",
            ),
        ]
        if all(item.credits is not None for item in items):
            claims.append(
                GroundedClaim(
                    _claim_id("aggregate", "credits_sum", fact_ids),
                    ClaimType.AGGREGATE,
                    provenance,
                    "credits_sum",
                    sum(item.credits for item in items if item.credits is not None),
                    unit="CREDIT",
                )
            )
        plan_filters = query_plan.get("filters")
        if isinstance(plan_filters, Mapping) and "completion_type" in plan_filters:
            expected = plan_filters["completion_type"]
            if completion_types != {expected}:
                raise GroundingError(
                    "ANSWER_CLAIM_INVALID", "course list completion_type scope is inconsistent"
                )
            # QueryPlan is only the requested scope.  The Claim fact value comes
            # from the single value observed in ResultValidator-approved rows.
            result_completion_type = next(iter(completion_types))
            claims.append(
                GroundedClaim(
                    _claim_id("field", "completion_type", fact_ids),
                    ClaimType.FIELD_VALUE,
                    provenance,
                    "completion_type",
                    result_completion_type,
                )
            )
        return claims

    @staticmethod
    def _course_subject(row: Mapping[str, Any], fact_id: str) -> ClaimSubject:
        name = row.get("name_ko")
        identity = row.get("course_identity") or row.get("course_code") or fact_id
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(identity, str)
            or not identity.strip()
        ):
            raise GroundingError("ANSWER_CLAIM_INVALID", "Course Claim lacks identity/name")
        return ClaimSubject(identity, name.strip())

    @staticmethod
    def _consistent_row(
        rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
    ) -> Mapping[str, Any]:
        first = rows[0]
        for field in fields:
            values = {_freeze(row.get(field)) for row in rows if field in row}
            if len(values) > 1:
                raise GroundingError(
                    "ANSWER_CLAIM_INVALID", f"fact has inconsistent {field} values"
                )
        return first

    @staticmethod
    def _provenance(rows: Sequence[Mapping[str, Any]]) -> tuple[FactEvidenceLink, ...]:
        links: set[FactEvidenceLink] = set()
        for row in rows:
            fact_id, evidence_id = row.get("fact_id"), row.get("evidence_id")
            if not isinstance(fact_id, str) or not isinstance(evidence_id, str):
                raise GroundingError("ANSWER_CLAIM_INVALID", "Claim provenance is invalid")
            links.add(FactEvidenceLink(fact_id, evidence_id))
        if not links:
            raise GroundingError("ANSWER_CLAIM_INVALID", "Claim provenance is empty")
        return tuple(sorted(links))
