"""Rebuild and approve canonical Claims from ResultValidator-approved rows."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from kg_builder.query.schema_catalog import SchemaCatalog

from .claim_builder import NARRATIVE_SOURCES, ClaimBuilder, _freeze
from .contracts import (
    AggregateClaimItem,
    AlignmentClaimItem,
    AllocationClaimItem,
    ClaimPolarity,
    ClaimType,
    CompetencyAlignmentClaimItem,
    CompetencyClaimItem,
    CourseClaimItem,
    FactEvidenceLink,
    GroundedClaim,
    GroundingError,
    NarrativeClaimItem,
    RecommendationClaimItem,
    RoadmapClaimItem,
)


ALLOWED_RULE_UNITS = frozenset({"CREDIT", "COURSE", "COURSE_PER_AREA", "AREA"})
FIELD_UNITS: Mapping[str, str | None] = {
    "course_code": None,
    "grade_year": None,
    "semester": None,
    "credits": "CREDIT",
    "completion_type": None,
}
EXTENDED_LIST_CLAIM_TYPES = frozenset(
    {
        ClaimType.ALLOCATION_LIST,
        ClaimType.ROADMAP_LIST,
        ClaimType.NARRATIVE_LIST,
        ClaimType.RECOMMENDATION_LIST,
        ClaimType.COMPETENCY_LIST,
        ClaimType.AGGREGATE_LIST,
        ClaimType.ALIGNMENT_LIST,
    }
)
# (Claim 항목 속성, 그 값이 와야 하는 결과 행 컬럼). 서술형 항목은 fact label 마다
# 원문 컬럼이 달라 ``_item_columns`` 에서 따로 해석한다.
EXTENDED_ITEM_COLUMNS: Mapping[type, tuple[tuple[str, str], ...]] = {
    AllocationClaimItem: (
        ("credit_category", "credit_category"),
        ("allocated_credits", "allocated_credits"),
        ("grade_year", "grade_year"),
        ("semester", "semester"),
        ("is_total", "is_total"),
    ),
    RoadmapClaimItem: (
        ("raw_label", "raw_label"),
        ("entry_type", "entry_type"),
        ("grade_year", "grade_year"),
        ("semester", "semester"),
        ("is_required", "is_required"),
    ),
    RecommendationClaimItem: (
        ("course_name_ko", "course_name_ko"),
        ("course_code", "course_code"),
        ("area_raw", "area_raw"),
        ("recommended_grade_year", "recommended_grade_year"),
        ("recommended_semester", "recommended_semester"),
        ("credits", "credits"),
    ),
    CompetencyClaimItem: (
        ("name_ko", "name_ko"),
        ("competency_type", "competency_type"),
        ("description_ko", "description_ko"),
        ("normalized_name_ko", "normalized_name_ko"),
    ),
    AggregateClaimItem: (
        ("aggregate_type", "aggregate_type"),
        ("is_total", "is_total"),
        ("name_ko", "name_ko"),
        ("course_count", "course_count"),
        ("credit_value", "credit_value"),
        ("lecture_hours", "lecture_hours"),
        ("practice_hours", "practice_hours"),
        ("boolean_value", "boolean_value"),
        ("unit", "unit"),
    ),
    AlignmentClaimItem: (
        ("alignment_type", "alignment_type"),
        ("strength", "strength"),
        ("source_text", "description_ko"),
        ("name_ko", "name_ko"),
        ("source_value", "source_value"),
    ),
    CompetencyAlignmentClaimItem: (
        ("alignment_type", "alignment_type"),
        ("strength", "strength"),
        ("normalized_name_ko", "normalized_name_ko"),
        ("name_ko", "name_ko"),
        ("source_value", "source_value"),
    ),
    NarrativeClaimItem: (),
}
_VALIDATION_SEAL = object()
_VALIDATION_KEY = secrets.token_bytes(32)


@dataclass(frozen=True, slots=True, order=True)
class ValidatedCitationSource:
    fact_id: str
    evidence_id: str
    excerpt_page: int
    source_pdf_page: int
    printed_page: int
    source_text: str


def _approval_digest(
    claims: tuple[GroundedClaim, ...],
    provenance: tuple[FactEvidenceLink, ...],
    citation_sources: tuple[ValidatedCitationSource, ...],
) -> str:
    payload = repr((claims, provenance, citation_sources)).encode("utf-8")
    return hmac.new(_VALIDATION_KEY, payload, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True, init=False)
class ValidatedClaims:
    """Validator-issued immutable Claims and their bound citation sources."""

    claims: tuple[GroundedClaim, ...]
    provenance: tuple[FactEvidenceLink, ...]
    citation_sources: tuple[ValidatedCitationSource, ...]
    _approval: str = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)

    def __init__(
        self,
        claims: tuple[GroundedClaim, ...],
        provenance: tuple[FactEvidenceLink, ...],
        citation_sources: tuple[ValidatedCitationSource, ...],
        *,
        _approval: str = "",
        _seal: object | None = None,
    ) -> None:
        expected = _approval_digest(claims, provenance, citation_sources)
        if _seal is not _VALIDATION_SEAL or not hmac.compare_digest(_approval, expected):
            raise TypeError("ValidatedClaims can only be issued by ClaimValidator")
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "provenance", provenance)
        object.__setattr__(self, "citation_sources", citation_sources)
        object.__setattr__(self, "_approval", _approval)
        object.__setattr__(self, "_seal", _seal)

    @classmethod
    def _issue(
        cls,
        claims: tuple[GroundedClaim, ...],
        citation_sources: tuple[ValidatedCitationSource, ...],
    ) -> "ValidatedClaims":
        provenance = tuple(
            sorted({link for claim in claims for link in claim.provenance})
        )
        source_provenance = tuple(
            sorted(
                FactEvidenceLink(source.fact_id, source.evidence_id)
                for source in citation_sources
            )
        )
        if provenance != source_provenance:
            raise GroundingError(
                "ANSWER_FACT_EVIDENCE_MISMATCH",
                "approved Claims and citation sources must have identical provenance",
            )
        return cls(
            claims,
            provenance,
            citation_sources,
            _approval=_approval_digest(claims, provenance, citation_sources),
            _seal=_VALIDATION_SEAL,
        )

    def _is_approved(self) -> bool:
        if self._seal is not _VALIDATION_SEAL:
            return False
        source_provenance = tuple(
            sorted(
                FactEvidenceLink(source.fact_id, source.evidence_id)
                for source in self.citation_sources
            )
        )
        if self.provenance != source_provenance:
            return False
        expected = _approval_digest(self.claims, self.provenance, self.citation_sources)
        return hmac.compare_digest(self._approval, expected)

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(sorted({link.fact_id for link in self.provenance}))

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(sorted({link.evidence_id for link in self.provenance}))


class ClaimValidator:
    """Rebuild canonical Claims, compare every field, and issue approval."""

    def __init__(self, catalog: SchemaCatalog | None = None):
        self.catalog = catalog or SchemaCatalog.from_generated()

    def validate(
        self,
        claims: Sequence[GroundedClaim],
        rows: Sequence[Mapping[str, Any]],
        query_plan: Mapping[str, Any],
    ) -> ValidatedClaims:
        if not claims:
            raise GroundingError("ANSWER_CLAIM_EMPTY", "Claim list is empty")
        if any(not isinstance(claim, GroundedClaim) for claim in claims):
            self._invalid("Claim list contains a non-GroundedClaim value")
        if len({claim.claim_id for claim in claims}) != len(claims):
            raise GroundingError("ANSWER_CLAIM_DUPLICATE", "Claim IDs must be unique")
        if not rows or not isinstance(query_plan, Mapping):
            self._invalid("validated rows and QueryPlan are required")

        by_fact: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        row_pairs: set[FactEvidenceLink] = set()
        source_by_pair: dict[FactEvidenceLink, ValidatedCitationSource] = {}
        for row in rows:
            fact_id, evidence_id = row.get("fact_id"), row.get("evidence_id")
            if row.get("fact_status") != "VERIFIED":
                raise GroundingError("ANSWER_FACT_NOT_VERIFIED", "Claim fact is not VERIFIED")
            if row.get("evidence_verification_status") != "VERIFIED":
                raise GroundingError(
                    "ANSWER_EVIDENCE_NOT_VERIFIED", "Claim Evidence is not VERIFIED"
                )
            if not isinstance(fact_id, str) or not isinstance(evidence_id, str):
                self._invalid("row provenance is invalid")
            by_fact[fact_id].append(row)
            link = FactEvidenceLink(fact_id, evidence_id)
            row_pairs.add(link)
            source = self._citation_source(row, fact_id, evidence_id)
            previous = source_by_pair.setdefault(link, source)
            if previous != source:
                raise GroundingError(
                    "ANSWER_CITATION_INVALID", "one provenance pair has inconsistent Evidence"
                )

        # Rebuild instead of approving caller-owned objects. QueryPlan controls the
        # requested shape; every factual value in the rebuilt Claims comes from rows.
        canonical = ClaimBuilder().build(rows, query_plan)
        submitted_by_id = {claim.claim_id: claim for claim in claims}
        canonical_by_id = {claim.claim_id: claim for claim in canonical}
        if submitted_by_id.keys() != canonical_by_id.keys():
            self._invalid("Claim IDs differ from canonical Claims")
        for claim_id, expected in canonical_by_id.items():
            if submitted_by_id[claim_id] != expected:
                self._invalid(f"Claim differs from canonical row-derived Claim: {claim_id}")

        for claim in canonical:
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
            elif claim.claim_type in EXTENDED_LIST_CLAIM_TYPES:
                self._extended_list(claim, by_fact)
            else:
                raise GroundingError(
                    "ANSWER_CLAIM_TYPE_UNSUPPORTED", "unsupported Claim type"
                )

        expected_facts = set(by_fact)
        claimed_facts = {item for claim in canonical for item in claim.fact_ids}
        if claimed_facts != expected_facts:
            raise GroundingError(
                "ANSWER_FACT_COVERAGE_INCOMPLETE", "Claims must cover every result fact"
            )
        citation_sources = tuple(sorted(source_by_pair.values()))
        return ValidatedClaims._issue(tuple(canonical), citation_sources)

    @staticmethod
    def _citation_source(
        row: Mapping[str, Any], fact_id: str, evidence_id: str
    ) -> ValidatedCitationSource:
        pages = (row.get("excerpt_page"), row.get("source_pdf_page"), row.get("printed_page"))
        source_text = row.get("source_text")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in pages):
            raise GroundingError("ANSWER_CITATION_INVALID", "Evidence pages are invalid")
        if not isinstance(source_text, str) or not source_text.strip():
            raise GroundingError("ANSWER_CITATION_INVALID", "Evidence source_text is empty")
        return ValidatedCitationSource(
            fact_id,
            evidence_id,
            pages[0],
            pages[1],
            pages[2],
            source_text,
        )

    @staticmethod
    def _single_fact(claim: GroundedClaim, by_fact) -> tuple[str, Mapping[str, Any]]:
        if len(claim.fact_ids) != 1:
            raise GroundingError("ANSWER_CLAIM_INVALID", "Claim requires one fact")
        fact_id = claim.fact_ids[0]
        return fact_id, by_fact[fact_id][0]

    def _field(self, claim: GroundedClaim, by_fact) -> None:
        if claim.field not in FIELD_UNITS:
            self._invalid("unsupported FIELD_VALUE field")
        if (
            claim.unit != FIELD_UNITS[claim.field]
            or claim.operator is not None
            or claim.polarity is not ClaimPolarity.POSITIVE
            or claim.description_ko is not None
        ):
            self._invalid("FIELD_VALUE contains metadata outside its field policy")
        if claim.field == "completion_type" and claim.subject is None:
            values = {
                _freeze(row.get(claim.field))
                for fact in claim.fact_ids
                for row in by_fact[fact]
            }
            if values != {claim.value}:
                self._invalid("completion_type Claim differs from rows")
        else:
            _, row = self._single_fact(claim, by_fact)
            identity = row.get("course_identity") or row.get("course_code") or claim.fact_ids[0]
            name = row.get("name_ko")
            if (
                claim.subject is None
                or claim.subject.entity_id != identity
                or claim.subject.display_name != name
                or not isinstance(identity, str)
                or not identity.strip()
                or not isinstance(name, str)
                or not name.strip()
            ):
                self._invalid("FIELD_VALUE subject differs from its Course")
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
        if claim.field == "credits" and (
            isinstance(claim.value, bool) or not isinstance(claim.value, (int, float))
        ):
            self._invalid("credits is not numeric")
        if claim.field == "course_code" and (
            not isinstance(claim.value, str) or not claim.value.strip()
        ):
            self._invalid("course_code is invalid")

    def _requirement(self, claim: GroundedClaim, by_fact) -> None:
        _, row = self._single_fact(claim, by_fact)
        if claim.subject is not None or claim.polarity is not ClaimPolarity.POSITIVE:
            self._invalid("numeric requirement contains unsupported subject/polarity")
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
            or claim.operator not in self.catalog.controlled_vocabularies["comparison_operator"]
        ):
            self._invalid("numeric requirement unit/operator is unsupported")

    def _boolean(self, claim: GroundedClaim, by_fact) -> None:
        _, row = self._single_fact(claim, by_fact)
        if not (
            claim.subject is None
            and claim.field == "exemption"
            and claim.value is True
            and claim.unit == "BOOLEAN"
            and claim.operator is None
            and claim.polarity is ClaimPolarity.EXEMPT
            and row.get("rule_type") == "EXEMPTION"
            and row.get("description_ko") == claim.description_ko
        ):
            self._invalid("boolean/exemption polarity differs from the Rule")

    def _rule_text(self, claim: GroundedClaim, by_fact) -> None:
        _, row = self._single_fact(claim, by_fact)
        if not (
            claim.subject is None
            and claim.field == "description_ko"
            and row.get("description_ko") == claim.value
            and claim.description_ko == claim.value
            and claim.unit is None
            and claim.operator is None
            and claim.polarity is ClaimPolarity.POSITIVE
        ):
            self._invalid("verified Rule text differs from the row")

    def _course_list(self, claim: GroundedClaim, by_fact) -> None:
        if not (
            claim.subject is None
            and claim.field == "courses"
            and isinstance(claim.value, tuple)
            and claim.value
            and claim.unit is None
            and claim.operator is None
            and claim.polarity is ClaimPolarity.POSITIVE
            and claim.description_ko is None
        ):
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

    def _extended_list(self, claim: GroundedClaim, by_fact) -> None:
        """Re-check every extended list item against the row it came from.

        ``ClaimBuilder``가 만든 값을 그대로 승인하지 않고, 항목 속성 하나하나를 승인된
        행과 다시 맞춰 본다. 문자열은 표시용 공백 제거만 허용하고, 그 밖의 변형은
        모두 불일치로 잡는다.
        """

        if not (
            claim.subject is None
            and isinstance(claim.value, tuple)
            and claim.value
            and claim.unit is None
            and claim.operator is None
            and claim.polarity is ClaimPolarity.POSITIVE
            and claim.description_ko is None
        ):
            self._invalid("extended list Claim contains unsupported metadata")
        items = claim.value
        kinds = {type(item) for item in items}
        if len(kinds) != 1 or next(iter(kinds)) not in EXTENDED_ITEM_COLUMNS:
            self._invalid("extended list items are invalid")
        if {item.fact_id for item in items} != set(by_fact):
            self._invalid("extended list does not cover all result facts")
        if len({item.fact_id for item in items}) != len(items):
            self._invalid("extended list contains duplicate facts")
        for item in items:
            row = by_fact[item.fact_id][0]
            for attribute, column in self._item_columns(item, row):
                expected = row.get(column)
                if isinstance(expected, str):
                    expected = expected.strip()
                if getattr(item, attribute) != expected:
                    self._invalid(f"extended list item differs from its fact: {attribute}")

    @staticmethod
    def _item_columns(item: Any, row: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
        if isinstance(item, NarrativeClaimItem):
            fact_label = row.get("fact_label")
            if fact_label not in NARRATIVE_SOURCES:
                raise GroundingError(
                    "ANSWER_CLAIM_INVALID", "narrative Claim has an unknown fact label"
                )
            text_column, order_column = NARRATIVE_SOURCES[fact_label]
            return (("text", text_column), ("order", order_column))
        return EXTENDED_ITEM_COLUMNS[type(item)]

    def _aggregate(self, claim: GroundedClaim, by_fact) -> None:
        if (
            claim.subject is not None
            or claim.operator is not None
            or claim.polarity is not ClaimPolarity.POSITIVE
            or claim.description_ko is not None
        ):
            self._invalid("aggregate contains unsupported metadata")
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
