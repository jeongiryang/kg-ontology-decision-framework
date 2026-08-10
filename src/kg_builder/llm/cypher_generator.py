"""Generate one strictly contracted Cypher candidate from a validated QueryPlan."""

from __future__ import annotations

from typing import Any, Mapping

from kg_builder.query.query_plan import QueryPlan, SelectionMode
from kg_builder.query.query_plan import FILTER_BINDINGS

from .client import LLMResponseError, StructuredLLMClient
from .prompts import CYPHER_SYSTEM_PROMPT, cypher_prompt


CYPHER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"cypher": {"type": "string"}},
    "required": ["cypher"],
    "additionalProperties": False,
}


def public_plan_payload(plan: QueryPlan) -> dict[str, Any]:
    return {
        "intent": plan.intent,
        "filters": dict(plan.filters),
        "requested_fields": list(plan.requested_fields),
        "evidence_required": plan.evidence_required,
        "selection_mode": plan.selection_mode.value if plan.selection_mode else None,
    }


PROVENANCE_RETURNS = [
    "{fact}.{fact_id} AS fact_id",
    "{fact}.status AS fact_status",
    "e.evidence_id AS evidence_id",
    "e.excerpt_page AS excerpt_page",
    "e.source_pdf_page AS source_pdf_page",
    "e.printed_page AS printed_page",
    "e.raw_text AS source_text",
    "e.verification_status AS evidence_verification_status",
]


def build_syntax_scaffold(plan: QueryPlan, schema_subset: Mapping[str, Any]) -> str:
    """Build a plan-specific grammar rail; it contains no answer values or question branches."""

    family = schema_subset.get("selected_fact_family")
    if family == "CourseOffering":
        aliases = {
            "CurriculumVersion": "cv",
            "Department": "d",
            "CourseOffering": "o",
            "Course": "c",
            "Evidence": "e",
        }
        matches = [
            "MATCH (cv:CurriculumVersion)-[:FOR_DEPARTMENT]->(d:Department)",
            "MATCH (cv)-[:HAS_OFFERING]->(o:CourseOffering)-[:OF_COURSE]->(c:Course)",
            "MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)",
        ]
        fact_alias, fact_id = "o", "offering_id"
    elif family == "Rule":
        aliases = {
            "CurriculumVersion": "cv",
            "Rule": "r",
            "EducationArea": "a",
            "ApplicabilityScope": "s",
            "Evidence": "e",
        }
        matches = ["MATCH (cv:CurriculumVersion)-[:HAS_RULE]->(r:Rule)"]
        if "area_id" in plan.filters:
            matches.append("MATCH (r)-[:TARGETS]->(a:EducationArea)")
        if "admission_type" in plan.filters or "major_type" in plan.filters:
            matches.append("MATCH (r)-[:APPLIES_TO]->(s:ApplicabilityScope)")
        matches.append("MATCH (r)-[:SUPPORTED_BY]->(e:Evidence)")
        fact_alias, fact_id = "r", "rule_id"
    else:
        raise LLMResponseError(
            "LLM_FACT_FAMILY_UNSUPPORTED", f"unsupported fact family: {family}"
        )

    predicates: list[str] = []
    scope_returns: list[str] = []
    for name in plan.filters:
        binding = FILTER_BINDINGS[name]
        alias = aliases.get(binding.label)
        if alias is None:
            raise LLMResponseError(
                "LLM_FILTER_PATH_UNSUPPORTED", f"no selected alias for filter {name}"
            )
        if binding.operator == "PARAMETER_IN_PROPERTY":
            predicates.append(f"${name} IN {alias}.{binding.property_name}")
        elif binding.operator == "PROPERTY_IN_PARAMETER":
            predicates.append(f"{alias}.{binding.property_name} IN ${name}")
        else:
            predicates.append(f"{alias}.{binding.property_name} = ${name}")
        scope_returns.append(f"{alias}.{binding.property_name} AS {name}")
    predicates.extend([f"{fact_alias}.status = 'VERIFIED'", "e.verification_status = 'VERIFIED'"])

    requested_returns: list[str] = []
    for field in plan.requested_fields:
        owner = None
        if family == "CourseOffering":
            if field in {"course_code", "name_ko"}:
                owner = "c"
            elif field in {
                "grade_year",
                "semester",
                "credits",
                "lecture_hours",
                "practice_hours",
                "completion_type",
            }:
                owner = "o"
        elif family == "Rule" and field in {
            "rule_id",
            "rule_type",
            "operator",
            "value",
            "unit",
            "description_ko",
        }:
            owner = "r"
        if owner is None:
            raise LLMResponseError(
                "LLM_REQUEST_FIELD_PATH_UNSUPPORTED", f"no fact path for requested field {field}"
            )
        requested_returns.append(f"{owner}.{field} AS {field}")

    returns = requested_returns + scope_returns + [
        item.format(fact=fact_alias, fact_id=fact_id) for item in PROVENANCE_RETURNS
    ]
    if plan.selection_mode is SelectionMode.SINGLE_COURSE:
        if family != "CourseOffering":
            raise LLMResponseError(
                "LLM_COURSE_IDENTITY_UNSUPPORTED",
                "SINGLE_COURSE requires the CourseOffering fact family",
            )
        returns.append("c.course_id AS course_identity")
    return (
        "\n".join(matches)
        + "\nWHERE "
        + "\n  AND ".join(predicates)
        + "\nRETURN "
        + ",\n       ".join(returns)
        + "\nLIMIT 100"
    )


class LocalCypherGenerator:
    def __init__(self, client: StructuredLLMClient):
        self.client = client

    def generate(
        self,
        plan: QueryPlan,
        schema_subset: Mapping[str, Any],
        *,
        previous_error_code: str | None = None,
    ) -> str:
        generation = self.client.generate_json(
            system_prompt=CYPHER_SYSTEM_PROMPT,
            user_prompt=cypher_prompt(
                public_plan_payload(plan),
                schema_subset,
                syntax_scaffold=build_syntax_scaffold(plan, schema_subset),
                previous_error_code=previous_error_code,
            ),
            response_schema=CYPHER_RESPONSE_SCHEMA,
        )
        cypher = generation.payload.get("cypher")
        if not isinstance(cypher, str) or not cypher.strip():
            raise LLMResponseError("LLM_CYPHER_MISSING", "generator returned no Cypher")
        return cypher.strip()
