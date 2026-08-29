"""Generate one strictly contracted Cypher candidate from a validated QueryPlan."""

from __future__ import annotations

from typing import Any, Mapping

from kg_builder.query.query_plan import QueryPlan, SelectionMode
from kg_builder.query.query_plan import resolve_filter_bindings
from kg_builder.query.fact_families import family_for_result

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
    # 모드를 키로 찾고 라벨이 일치할 때만 채택한다. 같은 라벨이 소유자별로 여러
    # 모드에 걸리므로 라벨만으로는 어떤 MATCH 경로를 써야 하는지 정해지지 않는다.
    extended = family_for_result(plan.selection_mode, family) if isinstance(family, str) else None
    if family == "CourseOffering":
        aliases = {
            "CurriculumVersion": "cv",
            "Department": "d",
            "CourseOffering": "o",
            "Course": "c",
            "Evidence": "e",
        }
        matches = []
        if "department_id" in plan.filters:
            matches.extend(
                [
                    "MATCH (cv:CurriculumVersion)-[:FOR_DEPARTMENT]->(d:Department)",
                    "MATCH (cv)-[:HAS_OFFERING]->(o:CourseOffering)-[:OF_COURSE]->(c:Course)",
                ]
            )
        else:
            matches.append(
                "MATCH (cv:CurriculumVersion)-[:HAS_OFFERING]->"
                "(o:CourseOffering)-[:OF_COURSE]->(c:Course)"
            )
        matches.append("MATCH (o)-[:SUPPORTED_BY]->(e:Evidence)")
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
    elif extended is not None:
        # 선언형 확장 family. MATCH 경로와 필드 소유 alias 는 fact_families 에만
        # 존재하므로, family 를 추가할 때 이 함수를 다시 고칠 필요가 없다.
        aliases = dict(extended.aliases)
        matches = list(extended.base_matches)
        for filter_name, extra_match in extended.conditional_matches.items():
            if filter_name in plan.filters:
                matches.append(extra_match)
        fact_alias, fact_id = extended.fact_alias, extended.fact_id_property
    else:
        raise LLMResponseError(
            "LLM_FACT_FAMILY_UNSUPPORTED", f"unsupported fact family: {family}"
        )

    bindings = resolve_filter_bindings(plan.selection_mode)
    predicates: list[str] = []
    scope_returns: list[str] = []
    for name in plan.filters:
        binding = bindings[name]
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
        # 같은 속성이 범위 필터이면서 요청 필드일 수 있다(예: credit_category).
        # RETURN 계약은 별칭 하나만 허용하고 검증기가 그 별칭이 필터가 바인딩한
        # 그래프 속성과 같은지 확인하므로, 여기서 중복 별칭만 걷어낸다.
        if name not in plan.requested_fields:
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
        elif extended is not None:
            owner = extended.field_owners.get(field)
        if owner is None:
            raise LLMResponseError(
                "LLM_REQUEST_FIELD_PATH_UNSUPPORTED", f"no fact path for requested field {field}"
            )
        requested_returns.append(f"{owner}.{field} AS {field}")

    subject_returns: list[str] = []
    if (
        plan.selection_mode is SelectionMode.SINGLE_COURSE
        and "name_ko" not in plan.requested_fields
        and "name_ko" not in plan.filters
    ):
        # This is a Claim-subject support field, not another answer slot.  A stable
        # course-code lookup still needs the display identity in the same verified
        # row so ClaimBuilder never has to consult an ungrounded side channel.
        subject_returns.append("c.name_ko AS name_ko")
    returns = requested_returns + scope_returns + subject_returns + [
        item.format(fact=fact_alias, fact_id=fact_id) for item in PROVENANCE_RETURNS
    ]
    # 탐색 화면이 뿌리 노드를 "2026학년도 대학 공통 교양 교육과정" 처럼 실제 이름으로
    # 부를 수 있게 한다. 검증기가 선택 별칭으로 허용한다.
    if aliases.get("CurriculumVersion") == "cv":
        returns.append("cv.version_name AS scope_identity")
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
