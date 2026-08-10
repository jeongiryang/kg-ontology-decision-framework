from __future__ import annotations

import unittest
from typing import Any, Mapping

from kg_builder.cypher_queries import QUERY_SPECS, UnsafeCypherError, ensure_read_only
from kg_builder.query_contracts import (
    Answerability,
    EvidenceDTO,
    Intent,
    QueryRequest,
    QueryResponse,
    QueryValidationError,
)
from kg_builder.query_service import QueryService, UnresolvedCatalog


EVIDENCE = {
    "evidence_id": "evidence:test:p1",
    "excerpt_page": 1,
    "source_pdf_page": 33,
    "printed_page": 25,
    "source_text": "검증된 원문",
}


class FakeExecutor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def execute(self, cypher: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((cypher, parameters))
        return self.rows


def request(intent: Intent, **parameters: Any) -> QueryRequest:
    return QueryRequest.from_dict({"intent": intent.value, "parameters": parameters})


class QueryContractTests(unittest.TestCase):
    def test_supported_intent_and_normalization(self) -> None:
        result = request(
            Intent.GET_COURSE_OFFERING,
            academic_year=2026,
            department=" 컴퓨터공학과 ",
            course_code="cda0008",
        )
        self.assertEqual(result.parameters["course_code"], "CDA0008")
        self.assertEqual(result.parameters["department"], "컴퓨터공학과")

    def test_unsupported_intent_is_rejected(self) -> None:
        with self.assertRaisesRegex(QueryValidationError, "unsupported intent"):
            QueryRequest.from_dict({"intent": "RUN_CYPHER", "parameters": {}})

    def test_missing_required_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(QueryValidationError, "missing required"):
            QueryRequest.from_dict(
                {"intent": Intent.GET_BALANCED_GENERAL_REQUIREMENT, "parameters": {}}
            )

    def test_empty_wrong_type_and_unknown_parameter_are_rejected(self) -> None:
        bad_payloads = [
            {
                "intent": Intent.GET_COURSE_OFFERING,
                "parameters": {
                    "academic_year": 2026,
                    "department": "",
                    "course_code": "CDA0008",
                },
            },
            {
                "intent": Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
                "parameters": {"academic_year": "2026"},
            },
            {
                "intent": Intent.GET_BALANCED_GENERAL_REQUIREMENT,
                "parameters": {"academic_year": 2026, "extra": True},
            },
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload), self.assertRaises(QueryValidationError):
                QueryRequest.from_dict(payload)

    def test_exactly_one_course_selector_is_required(self) -> None:
        for selector in ({}, {"course_code": "A", "course_name": "B"}):
            with self.subTest(selector=selector), self.assertRaisesRegex(
                QueryValidationError, "exactly one"
            ):
                request(
                    Intent.GET_COURSE_COMPLETION_TYPE,
                    academic_year=2026,
                    department="컴퓨터공학과",
                    **selector,
                )

    def test_transfer_intent_requires_transfer_value(self) -> None:
        with self.assertRaisesRegex(QueryValidationError, "TRANSFER"):
            request(
                Intent.GET_TRANSFER_GENERAL_EXEMPTION,
                academic_year=2026,
                admission_type="REGULAR",
            )


class CypherSafetyTests(unittest.TestCase):
    def test_all_templates_are_read_only_and_parameterized(self) -> None:
        self.assertEqual(set(QUERY_SPECS), set(Intent))
        for intent, spec in QUERY_SPECS.items():
            with self.subTest(intent=intent):
                self.assertEqual(ensure_read_only(spec.cypher), spec.cypher)
                self.assertIn("$academic_year", spec.cypher)
                self.assertNotIn("2026", spec.cypher)
                self.assertIn("VERIFIED", spec.cypher)
                self.assertIn("SUPPORTED_BY", spec.cypher)

    def test_write_and_procedure_queries_are_blocked(self) -> None:
        for keyword in (
            "CREATE",
            "MERGE",
            "SET",
            "DELETE",
            "DETACH DELETE",
            "REMOVE",
            "DROP",
            "CALL",
            "LOAD CSV",
            "FOREACH",
        ):
            with self.subTest(keyword=keyword), self.assertRaises(UnsafeCypherError):
                ensure_read_only(f"MATCH (n) {keyword} n")


class QueryServiceTests(unittest.TestCase):
    def make_service(
        self,
        rows: list[dict[str, Any]],
        unresolved: list[dict[str, Any]] | None = None,
    ) -> tuple[QueryService, FakeExecutor]:
        executor = FakeExecutor(rows)
        return QueryService(executor, UnresolvedCatalog(unresolved)), executor

    def test_general_minimum_returns_verified_evidence(self) -> None:
        rows = [
            {
                "rule_id": "rule:cwnu:2026:general:min-total-default",
                "rule_type": "CREDIT_REQUIREMENT",
                "operator": "GTE",
                "value": 34,
                "unit": "CREDIT",
                "description_ko": "최소 34학점",
                **EVIDENCE,
            }
        ]
        service, executor = self.make_service(rows)
        response = service.execute(
            request(Intent.GET_GENERAL_EDUCATION_MIN_CREDITS, academic_year=2026)
        )
        self.assertEqual(response.answerability, Answerability.ANSWERABLE)
        self.assertEqual(response.answer["requirement"]["value"], 34)
        self.assertEqual(response.evidence[0].source_pdf_page, 33)
        self.assertEqual(executor.calls[0][1]["academic_year"], 2026)

    def test_answerable_response_cannot_omit_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "Evidence"):
            QueryResponse(
                Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
                Answerability.ANSWERABLE,
                {},
                {},
            )
        service, _ = self.make_service(
            [{"rule_id": "rule", "rule_type": "CREDIT_REQUIREMENT", "value": 34}]
        )
        response = service.execute(
            request(Intent.GET_GENERAL_EDUCATION_MIN_CREDITS, academic_year=2026)
        )
        self.assertEqual(response.answerability, Answerability.UNRESOLVED)

    def test_multiple_course_identities_require_clarification(self) -> None:
        rows = [
            {
                "course_id": f"course:{code}",
                "course_code": code,
                "course_name": "동일과목",
                "offering_id": f"offering:{code}",
                "completion_type": "GENERAL_ELECTIVE",
                **EVIDENCE,
            }
            for code in ("AAA0001", "BBB0001")
        ]
        service, _ = self.make_service(rows)
        response = service.execute(
            request(
                Intent.GET_COURSE_OFFERING,
                academic_year=2026,
                department="컴퓨터공학과",
                course_name="동일과목",
            )
        )
        self.assertEqual(response.answerability, Answerability.CLARIFICATION_REQUIRED)
        self.assertEqual(len(response.answer["candidates"]), 2)

    def test_not_found_and_out_of_scope_are_distinct(self) -> None:
        service, executor = self.make_service([])
        not_found = service.execute(
            request(
                Intent.GET_COURSE_OFFERING,
                academic_year=2026,
                department="컴퓨터공학과",
                course_code="NONE000",
            )
        )
        outside = service.execute(
            request(Intent.GET_BALANCED_GENERAL_REQUIREMENT, academic_year=2027)
        )
        self.assertEqual(not_found.answerability, Answerability.NOT_FOUND)
        self.assertEqual(outside.answerability, Answerability.OUT_OF_SCOPE)
        self.assertEqual(len(executor.calls), 1)

    def test_unresolved_course_is_not_reported_as_not_found(self) -> None:
        service, _ = self.make_service(
            [],
            [
                {
                    "course_code": "CDA0147",
                    "name_ko": "현장실습4",
                    "note": "실기 열이 주 단위임",
                }
            ],
        )
        response = service.execute(
            request(
                Intent.GET_COURSE_OFFERING,
                academic_year=2026,
                department="컴퓨터공학과",
                course_code="CDA0147",
            )
        )
        self.assertEqual(response.answerability, Answerability.UNRESOLVED)

    def test_review_required_rows_are_excluded_by_template(self) -> None:
        for spec in QUERY_SPECS.values():
            self.assertNotIn("REVIEW_REQUIRED", spec.cypher)
            self.assertIn("'VERIFIED'", spec.cypher)

    def test_evidence_dto_preserves_pages_and_source_text(self) -> None:
        item = EvidenceDTO.from_record(EVIDENCE)
        self.assertIsNotNone(item)
        self.assertEqual(
            item.to_dict(),
            {
                "evidence_id": "evidence:test:p1",
                "excerpt_page": 1,
                "source_pdf_page": 33,
                "printed_page": 25,
                "source_text": "검증된 원문",
            },
        )


if __name__ == "__main__":
    unittest.main()
