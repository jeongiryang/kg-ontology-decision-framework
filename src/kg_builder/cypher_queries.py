"""Allowlisted, parameterized, read-only Cypher for supported query intents."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .query_contracts import Intent


class UnsafeCypherError(ValueError):
    """Raised when a query template contains a forbidden write or procedure keyword."""


FORBIDDEN_CYPHER = re.compile(
    r"\b(?:CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|CALL|LOAD\s+CSV|FOREACH)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class QuerySpec:
    cypher: str


def ensure_read_only(cypher: str) -> str:
    match = FORBIDDEN_CYPHER.search(cypher)
    if match:
        raise UnsafeCypherError(f"forbidden Cypher keyword: {match.group(0).upper()}")
    if not re.search(r"\bMATCH\b", cypher, re.IGNORECASE):
        raise UnsafeCypherError("read-only query must contain MATCH")
    return cypher


EVIDENCE_RETURN = """
       e.evidence_id AS evidence_id,
       e.excerpt_page AS excerpt_page,
       e.source_pdf_page AS source_pdf_page,
       e.printed_page AS printed_page,
       e.raw_text AS source_text
"""


QUERY_SPECS: dict[Intent, QuerySpec] = {
    Intent.GET_GENERAL_EDUCATION_MIN_CREDITS: QuerySpec(
        """
MATCH (cv:CurriculumVersion {academic_year: $academic_year, scope_type: 'COMMON'})
      -[:HAS_RULE]->(r:Rule:CreditRequirement {status: 'VERIFIED'})
WHERE r.rule_id = 'rule:cwnu:' + toString($academic_year) + ':general:min-total-default'
MATCH (r)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN r.rule_id AS rule_id, r.rule_type AS rule_type, r.operator AS operator,
       r.value AS value, r.unit AS unit, r.description_ko AS description_ko,
       cv.curriculum_id AS curriculum_id,
""" + EVIDENCE_RETURN
    ),
    Intent.GET_BALANCED_GENERAL_REQUIREMENT: QuerySpec(
        """
MATCH (cv:CurriculumVersion {academic_year: $academic_year, scope_type: 'COMMON'})
      -[:HAS_RULE]->(r:Rule {status: 'VERIFIED'})
WHERE r.rule_id IN [
  'rule:cwnu:' + toString($academic_year) + ':general:balanced-min-credits',
  'rule:cwnu:' + toString($academic_year) + ':general:balanced-each-area-one'
]
OPTIONAL MATCH (r)-[:TARGETS]->(a:EducationArea)
WITH cv, r, collect(DISTINCT {area_id: a.area_id, name_ko: a.name_ko}) AS areas
MATCH (r)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN r.rule_id AS rule_id, r.rule_type AS rule_type, r.operator AS operator,
       r.value AS value, r.unit AS unit, r.description_ko AS description_ko,
       cv.curriculum_id AS curriculum_id, areas,
""" + EVIDENCE_RETURN
    ),
    Intent.GET_TRANSFER_GENERAL_EXEMPTION: QuerySpec(
        """
MATCH (cv:CurriculumVersion {academic_year: $academic_year, scope_type: 'COMMON'})
      -[:HAS_RULE]->(r:Rule:ExemptionRule {status: 'VERIFIED'})
WHERE r.rule_id = 'rule:cwnu:' + toString($academic_year) + ':general:transfer-exemption'
MATCH (r)-[:APPLIES_TO]->(s:ApplicabilityScope {admission_type: $admission_type})
MATCH (r)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN r.rule_id AS rule_id, r.rule_type AS rule_type, r.description_ko AS description_ko,
       s.scope_id AS scope_id, s.admission_type AS admission_type,
       cv.curriculum_id AS curriculum_id,
""" + EVIDENCE_RETURN
    ),
    Intent.GET_COURSE_OFFERING: QuerySpec(
        """
MATCH (cv:CurriculumVersion {academic_year: $academic_year})-[:HAS_OFFERING]->
      (o:CourseOffering {status: 'VERIFIED'})-[:OF_COURSE]->(c:Course)
OPTIONAL MATCH (cv)-[:FOR_DEPARTMENT]->(d:Department)
WITH cv, o, c, d
WHERE (cv.scope_type = 'COMMON' OR d.department_id = $department_id)
  AND (($course_code IS NOT NULL AND c.course_code = $course_code)
       OR ($course_name IS NOT NULL AND c.name_ko = $course_name))
MATCH (o)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN c.course_id AS course_id, c.course_code AS course_code, c.name_ko AS course_name,
       o.offering_id AS offering_id, o.grade_year AS grade_year,
       o.semester AS semester, o.credits AS credits,
       o.lecture_hours AS lecture_hours, o.practice_hours AS practice_hours,
       o.completion_type AS completion_type, cv.curriculum_id AS curriculum_id,
""" + EVIDENCE_RETURN
    ),
    Intent.GET_MAJOR_REQUIRED_COURSES: QuerySpec(
        """
MATCH (cv:CurriculumVersion {academic_year: $academic_year, scope_type: 'DEPARTMENT'})
      -[:FOR_DEPARTMENT]->(d:Department {department_id: $department_id})
MATCH (cv)-[:HAS_OFFERING]->(o:CourseOffering {
  status: 'VERIFIED', completion_type: 'MAJOR_REQUIRED'
})-[:OF_COURSE]->(c:Course)
MATCH (o)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN c.course_id AS course_id, c.course_code AS course_code, c.name_ko AS course_name,
       o.offering_id AS offering_id, o.grade_year AS grade_year,
       o.semester AS semester, o.credits AS credits,
       o.completion_type AS completion_type, cv.curriculum_id AS curriculum_id,
""" + EVIDENCE_RETURN
    ),
    Intent.GET_COURSE_COMPLETION_TYPE: QuerySpec(
        """
MATCH (cv:CurriculumVersion {academic_year: $academic_year})-[:HAS_OFFERING]->
      (o:CourseOffering {status: 'VERIFIED'})-[:OF_COURSE]->(c:Course)
OPTIONAL MATCH (cv)-[:FOR_DEPARTMENT]->(d:Department)
WITH cv, o, c, d
WHERE (cv.scope_type = 'COMMON' OR d.department_id = $department_id)
  AND (($course_code IS NOT NULL AND c.course_code = $course_code)
       OR ($course_name IS NOT NULL AND c.name_ko = $course_name))
MATCH (o)-[:SUPPORTED_BY]->(e:Evidence {verification_status: 'VERIFIED'})
RETURN c.course_id AS course_id, c.course_code AS course_code, c.name_ko AS course_name,
       o.offering_id AS offering_id, o.completion_type AS completion_type,
       cv.curriculum_id AS curriculum_id,
""" + EVIDENCE_RETURN
    ),
}

for _query_spec in QUERY_SPECS.values():
    ensure_read_only(_query_spec.cypher)
