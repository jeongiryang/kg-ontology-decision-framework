"""Recompute GroundedClaim semantics and provenance from validated rows."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from kg_builder.query.schema_catalog import SchemaCatalog

from .claim_builder import _freeze
from .contracts import (
    ClaimPolarity,
    ClaimType,
    CourseClaimItem,
    FactEvidenceLink,
    GroundedClaim,
    GroundingError,
)


ALLOWED_RULE_UNITS = frozenset({"CREDIT", "COURSE", "COURSE_PER_AREA", "AREA"})


class ClaimValidator:
    """Validate values, roles, and direct fact-Evidence links independently."""

    def __init__(self, catalog: SchemaCatalog | None = None):
        self.catalog = catalog or SchemaCatalog.from_generated()

    def validate(
        self,
        claims: Sequence[GroundedClaim],
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[GroundedClaim, ...]:
        if not claims:
            raise GroundingError("ANSWER_CLAIM_EMPTY", "Claim list is empty")
        if len({claim.claim_id for claim in claims}) != len(claims):
            raise GroundingError("ANSWER_CLAIM_DUPLICATE", "Claim IDs must be unique")

        by_fact: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        row_pairs: set[FactEvidenceLink] = set()
        for row in rows:
            fact_id, evidence_id = row.get("fact_id"), row.get("evidence_id")
            if row.get("fact_status") != "VERIFIED":
                raise GroundingError("ANSWER_FACT_NOT_VERIFIED", "Claim fact is not VERIFIED")
            if row.get("evidence_verification_status") != "VERIFIED":
                raise GroundingError(
                    "ANSWER_EVIDENCE_NOT_VERIFIED", "Claim Evidence is not VERIFIED"
                )
            if not isinstance(fact_id, str) or not isinstance(evidence_id, str):
                raise GroundingError("ANSWER_CLAIM_INVALID", "row provenance is invalid")
            by_fact[fact_id].append(row)
            row_pairs.add(FactEvidenceLink(fact_id, evidence_id))

        for claim in claims:
            if not claim.provenance or any(link not in row_pairs for link in claim.provenance):
                raise GroundingError(
                    "ANSWER_FACT_EVIDENCE_MISMATCH",
                    "Claim contains a non-direct fact-Evidence pair",
                )
            if claim.claim_type is ClaimType.FIELD_VALUE:
                self._field(claim, by_fact)
            elif claim.claim_type is ClaimType.NUMERIC_REQUIREMENT:
                self._requirement(claim, by_fact)
            elif claim.claim_type is ClaimType.BOOLEAN_POLICY:
                self._boolean(claim, by_fact)
            elif claim.claim_type is ClaimType.VERIFIED_RULE_TEXT:
                self._rule_text(claim, by_fact)
            elif claim.claim_type is ClaimType.COURSE_LIST:
                self._course_list(claim, by_fact)
            elif claim.claim_type is ClaimType.AGGREGATE:
                self._aggregate(claim, by_fact)
            else:
                raise GroundingError(
                    "ANSWER_CLAIM_TYPE_UNSUPPORTED", "unsupported Claim type"
                )

        expected_facts = set(by_fact)
        claimed_facts = {item for claim in claims for item in claim.fact_ids}
        if claimed_facts != expected_facts:
            raise GroundingError(
                "ANSWER_FACT_COVERAGE_INCOMPLETE", "Claims must cover every result fact"
            )
        return tuple(claims)

    @staticmethod
    def _single_fact(claim: GroundedClaim, by_fact) -> tuple[str, Mapping[str, Any]]:
        if len(claim.fact_ids) != 1:
            raise GroundingError("ANSWER_CLAIM_INVALID", "Claim requires one fact")
        fact_id = claim.fact_ids[0]
        return fact_id, by_fact[fact_id][0]

    def _field(self, claim: GroundedClaim, by_fact) -> None:
        if claim.field == "completion_type" and len(claim.fact_ids) > 1:
            values = {_freeze(row.get(claim.field)) for fact in claim.fact_ids for row in by_fact[fact]}
            if values != {claim.value}:
                self._invalid("completion_type Claim differs from rows")
        else:
            _, row = self._single_fact(claim, by_fact)
            if claim.field not in row or _freeze(row[claim.field]) != claim.value:
                self._invalid(f"field Claim differs from row {claim.field}")
        if (
            claim.field == "completion_type"
            and claim.value not in self.catalog.controlled_vocabularies["completion_type"]
        ):
            self._invalid("completion_type is outside controlled vocabulary")
        if (
            claim.field == "semester"
            and claim.value not in self.catalog.controlled_vocabularies["semester"]
        ):
            self._invalid("semester is outside controlled vocabulary")
        if claim.field == "grade_year":
            grades = claim.value if isinstance(claim.value, tuple) else (claim.value,)
            if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 6 for v in grades):
                self._invalid("grade_year is invalid")

    def _requirement(self, claim: GroundedClaim, by_fact) -> None:
        _, row = self._single_fact(claim, by_fact)
        if (
            claim.field != "requirement_value"
            or _freeze(row.get("value")) != claim.value
            or row.get("unit") != claim.unit
            or row.get("operator") != claim.operator
            or row.get("description_ko") != claim.description_ko
        ):
            self._invalid("numeric requirement roles differ from the Rule")
        if isinstance(claim.value, bool) or not isinstance(claim.value, (int, float)):
            self._invalid("numeric requirement value is not numeric")
        if (
            claim.unit not in ALLOWED_RULE_UNITS
            or claim.operator
            not in self.catalog.controlled_vocabularies["comparison_operator"]
        ):
            self._invalid("numeric requirement unit/operator is unsupported")

    def _boolean(self, claim: GroundedClaim, by_fact) -> None:
        _, row = self._single_fact(claim, by_fact)
        if not (
            claim.field == "exemption"
            and claim.value is True
            and claim.unit == "BOOLEAN"
            and claim.polarity is ClaimPolarity.EXEMPT
            and row.get("rule_type") == "EXEMPTION"
            and row.get("description_ko") == claim.description_ko
        ):
            self._invalid("boolean/exemption polarity differs from the Rule")

    def _rule_text(self, claim: GroundedClaim, by_fact) -> None:
        _, row = self._single_fact(claim, by_fact)
        if (
            claim.field != "description_ko"
            or row.get("description_ko") != claim.value
            or claim.description_ko != claim.value
        ):
            self._invalid("verified Rule text differs from the row")

    def _course_list(self, claim: GroundedClaim, by_fact) -> None:
        if claim.field != "courses" or not isinstance(claim.value, tuple) or not claim.value:
            self._invalid("course list Claim is invalid")
        items = claim.value
        if any(not isinstance(item, CourseClaimItem) for item in items):
            self._invalid("course list items are invalid")
        if {item.fact_id for item in items} != set(by_fact):
            self._invalid("course list does not cover all result facts")
        if len({item.fact_id for item in items}) != len(items):
            self._invalid("course list contains duplicate facts")
        for item in items:
            row = by_fact[item.fact_id][0]
            expected_identity = row.get("course_identity") or row.get("course_code") or item.fact_id
            if (
                item.entity_id != expected_identity
                or item.display_name != row.get("name_ko")
                or item.course_code != row.get("course_code")
                or item.credits != row.get("credits")
            ):
                self._invalid("course list item differs from its fact")

    def _aggregate(self, claim: GroundedClaim, by_fact) -> None:
        if set(claim.fact_ids) != set(by_fact):
            self._invalid("aggregate must cover every result fact")
        if claim.field == "fact_count":
            expected, unit = len(by_fact), "COURSE"
        elif claim.field == "credits_sum":
            credits = [rows[0].get("credits") for rows in by_fact.values()]
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in credits):
                self._invalid("credit aggregate contains non-numeric values")
            expected, unit = sum(credits), "CREDIT"
        else:
            raise GroundingError(
                "ANSWER_CLAIM_TYPE_UNSUPPORTED", "unsupported aggregate field"
            )
        if claim.value != expected or claim.unit != unit:
            self._invalid("aggregate value/unit differs from recomputed rows")

    @staticmethod
    def _invalid(message: str) -> None:
        raise GroundingError("ANSWER_CLAIM_INVALID", message)
