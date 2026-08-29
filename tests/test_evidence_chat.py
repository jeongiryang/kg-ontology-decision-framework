"""Starlette presentation tests without a live model or Neo4j server."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import httpx2

from evidence_chat import pdf_evidence
from evidence_chat.chat_adapter import CHAT_RESPONSE_FIELDS, ChatResponseAdapter
from evidence_chat.graph_projection import (
    build_provenance_projection,
    build_query_structure_projection,
)
from evidence_chat.server import ChatState, InspectionCollector, create_app
from kg_builder.answer.contracts import ChatErrorCode, ChatResponse, ChatStatus
from kg_builder.answer.personalized_service import PersonalizedCurriculumChatService
from kg_builder.answer.service import CurriculumChatService
from kg_builder.query.natural_language_service import NaturalLanguageResult
from kg_builder.query.query_plan import MAX_QUESTION_LENGTH
from kg_builder.query.progress import ProgressEvent, ProgressPhase, ProgressState


SAMPLE_RAW_TEXT = (
    "기초교양 | 미래설계 | GEA8001 | 대학생활의설계 | 1학점 | 이론 1 | 실기 0"
)


@contextmanager
def _pdf_env(path: str):
    with mock.patch.dict("os.environ", {pdf_evidence.PDF_PATH_ENV: path}):
        yield


def _offering_row(index: int = 0, *, page: int = 17) -> dict[str, Any]:
    code = f"CDA{index + 8:04d}"
    return {
        "course_code": code,
        "name_ko": "자료구조" if index == 0 else f"전공과목{index + 1}",
        "grade_year": [2],
        "semester": "FIRST",
        "credits": 3 if index < 7 else 0,
        "completion_type": "MAJOR_REQUIRED",
        "academic_year": 2026,
        "department_id": "department:cwnu:cse",
        "fact_id": f"offering:cwnu:2026:cse:{code}:first",
        "fact_label": "CourseOffering",
        "fact_status": "VERIFIED",
        "evidence_id": f"evidence:curriculum:{page}:{code}",
        "excerpt_page": page,
        "source_pdf_page": page + 245,
        "printed_page": page + 237,
        "source_text": f"{code} 전공필수 과목 근거",
        "evidence_verification_status": "VERIFIED",
        "course_identity": f"course:cwnu:{code}",
    }


def _result(rows: list[dict[str, Any]], plan: dict[str, Any]) -> NaturalLanguageResult:
    return NaturalLanguageResult(
        request_id="request-test",
        status="ANSWERABLE",
        model="fake-model",
        elapsed_seconds=0.01,
        query_plan=plan,
        rows=tuple(rows),
        evidence_count=len({row["evidence_id"] for row in rows}),
    )


class _QueryStub:
    def __init__(self, result: NaturalLanguageResult):
        self.result = result

    def ask(
        self,
        question: str,
        *,
        resolved: Any = None,
        progress_callback=None,
    ) -> NaturalLanguageResult:
        del question, resolved, progress_callback
        return self.result


def _answerable_response(*, count: int = 1, same_page: bool = False) -> ChatResponse:
    rows = [
        _offering_row(index, page=17 if same_page else 17 + index)
        for index in range(count)
    ]
    if count == 1:
        plan = {
            "intent": "course_query",
            "filters": {
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "course_code": rows[0]["course_code"],
            },
            "requested_fields": ["completion_type"],
            "evidence_required": True,
            "selection_mode": "SINGLE_COURSE",
        }
    else:
        plan = {
            "intent": "course_list",
            "filters": {
                "academic_year": 2026,
                "department_id": "department:cwnu:cse",
                "completion_type": "MAJOR_REQUIRED",
            },
            "requested_fields": ["course_code", "name_ko", "credits"],
            "evidence_required": True,
            "selection_mode": "COURSE_LIST",
        }
    return CurriculumChatService(_QueryStub(_result(rows, plan))).ask("질문")


class _ChatStub:
    def __init__(self, response: ChatResponse):
        self.response = response
        self.questions: list[str] = []

    def ask(
        self,
        question: str,
        *,
        resolved: Any = None,
        progress_callback=None,
    ) -> ChatResponse:
        self.questions.append(question)
        self.resolved = resolved
        row = _offering_row()
        details = {
            ProgressPhase.QUESTION_ANALYSIS: {
                "planning_status": "READY",
                "query_plan": {
                    "filters": {"academic_year": 2026},
                    "requested_fields": ["completion_type"],
                },
                "missing": [],
                "clarification_options": [],
            },
            ProgressPhase.SCHEMA_SELECTION: {
                "labels": ["CourseOffering", "Evidence"],
                "relationship_types": ["SUPPORTED_BY"],
            },
            ProgressPhase.STATIC_VALIDATION: {
                "validated_cypher": "MATCH (o:CourseOffering)-[:SUPPORTED_BY]->(e:Evidence) WHERE o.status = 'VERIFIED' RETURN o LIMIT 1",
                "parameters": {"academic_year": 2026},
                "labels": ["CourseOffering", "Evidence"],
                "relationship_types": ["SUPPORTED_BY"],
                "limit": 1,
                "parameter_binding_verified": True,
                "direct_evidence_path_verified": True,
            },
            ProgressPhase.NEO4J_EXPLAIN: {"operators": ["NodeIndexSeek"]},
            ProgressPhase.GRAPH_EXECUTION: {"row_count": 1},
            ProgressPhase.RESULT_VALIDATION: {
                "row_count": 1,
                "fact_count": 1,
                "evidence_count": 1,
                "fact_status_verified": True,
                "evidence_status_verified": True,
                "direct_provenance_verified": True,
                "rejected_row_count": 0,
            },
            ProgressPhase.CLAIM_BUILDING: {
                "claim_count": 1,
                "claim_types": ["FIELD_VALUE"],
                "aggregate": False,
                "citation_target_count": 1,
                "validated_rows": [row],
                "approved_provenance": [(row["fact_id"], row["evidence_id"])],
            },
            ProgressPhase.ANSWER_RENDERING: {
                "citation_count": 1,
                "deterministic_renderer": True,
                "final_answer_llm_calls": 0,
            },
            ProgressPhase.COMPLETED: {
                "final_status": "ANSWERABLE",
                "citation_count": 1,
            },
        }
        if progress_callback:
            for phase in ProgressPhase:
                phase_details = dict(details.get(phase, {}))
                if phase in {
                    ProgressPhase.CYPHER_GENERATION,
                    ProgressPhase.STATIC_VALIDATION,
                    ProgressPhase.NEO4J_EXPLAIN,
                }:
                    phase_details["candidate_attempt"] = 1
                if phase is not ProgressPhase.COMPLETED:
                    progress_callback(
                        ProgressEvent(
                            phase, ProgressState.STARTED, 0, phase_details
                        )
                    )
                progress_callback(
                    ProgressEvent(
                        phase,
                        ProgressState.COMPLETED,
                        1,
                        phase_details,
                    )
                )
        return self.response


class _RetryChatStub(_ChatStub):
    def ask(
        self,
        question: str,
        *,
        resolved: Any = None,
        progress_callback=None,
    ) -> ChatResponse:
        self.questions.append(question)
        self.resolved = resolved
        if progress_callback:
            def emit(phase, state, attempt, **details):
                progress_callback(
                    ProgressEvent(
                        phase,
                        state,
                        1,
                        {"candidate_attempt": attempt, **details},
                    )
                )

            emit(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 1)
            emit(ProgressPhase.CYPHER_GENERATION, ProgressState.COMPLETED, 1)
            emit(ProgressPhase.STATIC_VALIDATION, ProgressState.STARTED, 1)
            emit(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                1,
                validated_cypher="MATCH (first:Rule) RETURN first LIMIT 1",
                parameters={"candidate": "first"},
                labels=["Rule"],
                relationship_types=[],
            )
            emit(ProgressPhase.NEO4J_EXPLAIN, ProgressState.STARTED, 1)
            emit(
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.FAILED,
                1,
                error_code="NEO4J_EXPLAIN_FAILED",
            )
            emit(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 2)
            emit(ProgressPhase.CYPHER_GENERATION, ProgressState.COMPLETED, 2)
            emit(ProgressPhase.STATIC_VALIDATION, ProgressState.STARTED, 2)
            emit(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                2,
                validated_cypher="MATCH (second:Rule) RETURN second LIMIT 1",
                parameters={"candidate": "second"},
                labels=["Rule"],
                relationship_types=[],
            )
            emit(ProgressPhase.NEO4J_EXPLAIN, ProgressState.STARTED, 2)
            emit(
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.COMPLETED,
                2,
                operators=["NodeByLabelScan"],
            )
        return self.response


class _ClarificationChatStub(_ChatStub):
    def ask(
        self,
        question: str,
        *,
        resolved: Any = None,
        progress_callback=None,
    ) -> ChatResponse:
        self.questions.append(question)
        self.resolved = resolved
        if progress_callback:
            progress_callback(
                ProgressEvent(
                    ProgressPhase.QUESTION_ANALYSIS,
                    ProgressState.STARTED,
                    0,
                    {},
                )
            )
            progress_callback(
                ProgressEvent(
                    ProgressPhase.QUESTION_ANALYSIS,
                    ProgressState.COMPLETED,
                    1,
                    {
                        "planning_status": "CLARIFICATION_REQUIRED",
                        "missing": ["DEPARTMENT"],
                        "clarification_options": [
                            type(
                                "Choice",
                                (),
                                {
                                    "filter_name": "department_id",
                                    "value": "department:cwnu:cse",
                                    "label": "컴퓨터공학과",
                                    "detail": None,
                                },
                            )()
                        ],
                    },
                )
            )
        return self.response


def _events(response_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[6:])
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


class ChatResponseAdapterTests(unittest.TestCase):
    def test_answerable_preserves_wire_contract_and_hides_internal_ids_from_page_cards(self):
        response = _answerable_response()
        adapted = ChatResponseAdapter().adapt(response)
        self.assertEqual(adapted["response"], response.to_dict())
        self.assertEqual(frozenset(adapted["response"]), CHAT_RESPONSE_FIELDS)
        self.assertEqual(adapted["response"]["status"], "ANSWERABLE")
        entry = adapted["presentation"]["evidence_pages"][0]["evidence"][0]
        self.assertNotIn("evidence_id", entry)
        self.assertNotIn("fact_ids", entry)
        self.assertIsNone(adapted["presentation"]["debug"])

    def test_all_non_answerable_statuses_have_safe_presentations(self):
        responses = (
            ChatResponse.clarification_required("r1", "학과를 알려 주세요."),
            ChatResponse.out_of_scope("r2"),
            ChatResponse.unsupported("r3"),
            ChatResponse.unresolved("r4"),
            ChatResponse.not_found("r5"),
            ChatResponse.safe_failure("r6", ChatErrorCode.QUERY_SAFE_FAILURE),
        )
        for response in responses:
            with self.subTest(status=response.status):
                adapted = ChatResponseAdapter().adapt(response)
                self.assertFalse(adapted["presentation"]["evidence_pages"])
                self.assertEqual(adapted["response"]["answer_text"], response.answer_text)
                self.assertEqual(adapted["response"]["citations"], [])

    def test_debug_exposes_only_request_id_and_sanitized_error_code(self):
        response = ChatResponse.safe_failure("request-1", "synthetic secret")
        debug = ChatResponseAdapter(debug=True).adapt(response)["presentation"]["debug"]
        self.assertEqual(
            debug,
            {"request_id": "request-1", "error_code": "UNKNOWN_SAFE_FAILURE"},
        )
        self.assertNotIn("synthetic secret", json.dumps(debug))

    def test_nine_citations_are_grouped_without_changing_citations(self):
        response = _answerable_response(count=9, same_page=True)
        adapted = ChatResponseAdapter().adapt(response)
        self.assertEqual(adapted["response"]["citations"], response.to_dict()["citations"])
        pages = adapted["presentation"]["evidence_pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]["evidence"]), 9)

    def test_unsupported_reason_uses_fixed_non_personal_comparison_message(self):
        result = NaturalLanguageResult(
            request_id="request-comparison",
            status="UNSUPPORTED",
            model="fake-model",
            elapsed_seconds=0.01,
            unsupported_reason="SINGLE_CONDITION_COMPARISON",
        )
        response = CurriculumChatService(_QueryStub(result)).ask("단일 조건 비교")
        self.assertEqual(response.status, ChatStatus.UNSUPPORTED)
        self.assertIn("단일 점수·학점", response.answer_text)
        self.assertNotIn("개인 수강 이력", response.answer_text)


class InspectionApprovalTests(unittest.TestCase):
    @staticmethod
    def _event(phase, state, attempt, **details):
        return ProgressEvent(
            phase,
            state,
            1,
            {"candidate_attempt": attempt, **details},
        )

    def test_explain_failure_discards_statically_validated_candidate(self):
        collector = InspectionCollector()
        collector.record(self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 1))
        collector.record(
            self._event(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                1,
                validated_cypher="MATCH (first:Rule) RETURN first LIMIT 1",
                parameters={"candidate": "first"},
                labels=["Rule"],
                relationship_types=["SUPPORTED_BY"],
            )
        )
        failed = collector.record(
            self._event(
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.FAILED,
                1,
                error_code="NEO4J_EXPLAIN_FAILED",
            )
        )
        self.assertEqual(failed["type"], "inspection_update")
        self.assertEqual(failed["summary"], {"error_code": "NEO4J_EXPLAIN_FAILED"})
        self.assertNotIn("cypher", json.dumps(failed).lower())

    def test_retry_approves_only_second_candidate_without_mixing(self):
        collector = InspectionCollector()
        collector.record(self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 1))
        collector.record(
            self._event(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                1,
                validated_cypher="MATCH (first:Rule) RETURN first LIMIT 1",
                parameters={"candidate": "first"},
                labels=["Rule"],
                relationship_types=["SUPPORTED_BY"],
            )
        )
        collector.record(
            self._event(ProgressPhase.NEO4J_EXPLAIN, ProgressState.FAILED, 1)
        )
        collector.record(self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 2))
        collector.record(
            self._event(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                2,
                validated_cypher="MATCH (second:Rule) RETURN second LIMIT 1",
                parameters={"candidate": "second"},
                labels=["Rule", "Evidence"],
                relationship_types=["SUPPORTED_BY"],
            )
        )
        approved = collector.record(
            self._event(
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.COMPLETED,
                2,
                operators=["NodeIndexSeek"],
            )
        )
        self.assertEqual(approved["type"], "inspection_update")
        summary = approved["summary"]
        self.assertIn("second:Rule", summary["approved_cypher"])
        self.assertNotIn("first:Rule", summary["approved_cypher"])
        self.assertEqual(summary["parameters"], {"candidate": "second"})
        self.assertEqual(summary["operators"], ["NodeIndexSeek"])
        self.assertEqual(summary["labels"], ["Evidence", "Rule"])

    def test_static_completion_never_exposes_candidate_text(self):
        collector = InspectionCollector()
        collector.record(self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 1))
        update = collector.record(
            self._event(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                1,
                validated_cypher="MATCH (hidden:Rule) RETURN hidden LIMIT 1",
                parameters={"secret": "not-public-yet"},
                labels=["Rule"],
                relationship_types=[],
            )
        )
        serialized = json.dumps(update, ensure_ascii=False)
        self.assertNotIn("MATCH", serialized)
        self.assertNotIn("not-public-yet", serialized)

    def test_noncanonical_commented_candidate_is_not_approved_or_exposed(self):
        marker = "synthetic-system-prompt-marker"
        collector = InspectionCollector()
        collector.record(self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 1))
        static = collector.record(
            self._event(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                1,
                validated_cypher=(
                    f"// {marker}\nMATCH (hidden:Rule) RETURN hidden LIMIT 1"
                ),
                parameters={},
                labels=["Rule"],
                relationship_types=[],
            )
        )
        explained = collector.record(
            self._event(
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.COMPLETED,
                1,
                operators=["NodeByLabelScan"],
            )
        )
        self.assertNotIn(marker, json.dumps(static, ensure_ascii=False))
        self.assertIsNone(explained)

    def test_retry_start_retracts_a_previously_approved_candidate(self):
        collector = InspectionCollector()
        collector.record(self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 1))
        collector.record(
            self._event(
                ProgressPhase.STATIC_VALIDATION,
                ProgressState.COMPLETED,
                1,
                validated_cypher="MATCH (old:Rule) RETURN old LIMIT 1",
                parameters={},
                labels=["Rule"],
                relationship_types=[],
            )
        )
        collector.record(
            self._event(
                ProgressPhase.NEO4J_EXPLAIN,
                ProgressState.COMPLETED,
                1,
                operators=["NodeByLabelScan"],
            )
        )
        reset = collector.record(
            self._event(ProgressPhase.CYPHER_GENERATION, ProgressState.STARTED, 2)
        )
        self.assertEqual(
            reset["summary"],
            {"retry": True, "discard_previous_candidate": True},
        )

    def test_public_progress_reports_safe_retry_and_error_without_details(self):
        retry = self._event(
            ProgressPhase.CYPHER_GENERATION,
            ProgressState.STARTED,
            2,
            system_prompt="never expose",
        ).public_payload()
        self.assertEqual(retry["attempt"], 2)
        self.assertTrue(retry["retry"])
        self.assertIn("다시 생성", retry["message"])
        self.assertNotIn("system_prompt", retry)

        failed = self._event(
            ProgressPhase.STATIC_VALIDATION,
            ProgressState.FAILED,
            2,
            error_code="CYPHER_UNKNOWN_RELATIONSHIP",
            validated_cypher="MATCH (unsafe)",
        ).public_payload()
        self.assertEqual(failed["error_code"], "CYPHER_UNKNOWN_RELATIONSHIP")
        self.assertNotIn("validated_cypher", failed)


class NeedleExtractionTests(unittest.TestCase):
    def test_splits_and_drops_short_numeric_noise(self):
        needles = pdf_evidence.needles_from_raw_text(SAMPLE_RAW_TEXT)
        self.assertIn("GEA8001", needles)
        self.assertIn("대학생활의설계", needles)
        self.assertNotIn("1", needles)


class PdfEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import pymupdf
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("pymupdf가 설치되지 않았습니다.")
        cls._tmp = TemporaryDirectory()
        cls.pdf_path = Path(cls._tmp.name) / "synthetic.pdf"
        document = pymupdf.open()
        for index in range(19):
            page = document.new_page(width=595, height=842)
            page.insert_text((72, 120), f"page {index + 1}", fontsize=14)
            page.insert_text((72, 160), "GEA8001", fontsize=12)
        document.save(cls.pdf_path)
        document.close()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_public_state_omits_paths_and_hashes(self):
        source = pdf_evidence.inspect_pdf(self.pdf_path)
        public = source.to_public_dict()
        self.assertNotIn("path", public)
        self.assertNotIn("sha256", public)
        self.assertNotIn(str(self.pdf_path), json.dumps(public))

    def test_missing_pdf_uses_path_free_reason(self):
        target = Path("/synthetic/private/path/curriculum.pdf")
        source = pdf_evidence.inspect_pdf(target)
        self.assertFalse(source.available)
        self.assertNotIn(str(target), source.reason or "")

    def test_highlight_and_png_rendering(self):
        highlights = pdf_evidence.find_highlights("GEA8001", 1, self.pdf_path)
        self.assertTrue(highlights)
        self.assertTrue(all(0 <= item.x <= 1 for item in highlights))
        with _pdf_env(str(self.pdf_path)):
            image = pdf_evidence.render_page_png(1)
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_page_grouping_deduplicates_evidence(self):
        citation = {
            "evidence_id": "e1",
            "fact_ids": ["f1"],
            "excerpt_page": 1,
            "source_pdf_page": 33,
            "printed_page": 25,
            "source_text": "GEA8001",
        }
        pages = pdf_evidence.build_evidence_pages(
            [citation, dict(citation)], self.pdf_path
        )
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]["evidence"]), 1)
        self.assertTrue(pages[0]["evidence"][0]["highlight_found"])

    def test_page_render_rejects_out_of_range(self):
        with _pdf_env(str(self.pdf_path)), self.assertRaises(pdf_evidence.PdfEvidenceError):
            pdf_evidence.render_page_png(99)


class StarletteRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.chat = _ChatStub(_answerable_response())
        self.app = create_app(lambda: ChatState(self.chat))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def test_health_is_path_and_connection_secret_free(self):
        payload = (await self.client.get("/api/health")).json()
        self.assertTrue(payload["service_ready"])
        self.assertNotIn("neo4j_endpoint", payload)
        self.assertNotIn("neo4j_database", payload)
        self.assertIn("pdf_mounted", payload)
        self.assertNotIn("pdf", payload)
        self.assertGreaterEqual(payload["client_timeout_seconds"], 60)

    async def test_ask_streams_generic_progress_and_approved_response(self):
        response = await self.client.post("/api/ask", json={"question": "자료구조 이수구분"})
        self.assertEqual(response.status_code, 200)
        events = _events(response.text)
        completed = [
            item["phase"]
            for item in events
            if item["type"] == "progress" and item["state"] == "COMPLETED"
        ]
        self.assertEqual(completed, [item.value for item in ProgressPhase])
        result = next(item for item in events if item["type"] == "result")
        self.assertEqual(result["response"]["status"], "ANSWERABLE")
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("MATCH ", serialized)
        self.assertNotIn("QueryPlan", serialized)
        self.assertFalse(any(item["type"] == "inspection_update" for item in events))
        self.assertFalse(any(item["type"] == "clarification_options" for item in events))
        self.assertEqual(self.chat.questions, ["자료구조 이수구분"])

    async def test_clarification_choices_use_separate_versioned_envelope(self):
        chat = _ClarificationChatStub(
            ChatResponse.clarification_required("request-clarify", "어느 학과를 말씀하시나요?")
        )
        app = create_app(lambda: ChatState(chat))
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                events = _events(
                    (await client.post(
                        "/api/ask",
                        json={
                            "question": "전공 과목",
                            "resolved": {"department_id": "department:cwnu:cse"},
                        },
                    )).text
                )
        envelope = next(item for item in events if item["type"] == "clarification_options")
        result = next(item for item in events if item["type"] == "result")
        self.assertEqual(envelope["version"], 1)
        self.assertEqual(envelope["missing"], ["DEPARTMENT"])
        self.assertEqual(envelope["options"][0]["filter"], "department_id")
        self.assertEqual(envelope["options"][0]["label"], "컴퓨터공학과")
        self.assertRegex(envelope["options"][0]["choice_id"], r"^choice:[0-9a-f]{24}$")
        self.assertEqual(
            set(result["response"]),
            {
                "request_id",
                "status",
                "answer_text",
                "citations",
                "used_fact_ids",
                "used_evidence_ids",
                "clarification",
                "error_code",
            },
        )
        self.assertNotIn("options", result["response"])
        self.assertNotIn("missing", result["response"])
        self.assertEqual(
            chat.resolved,
            {"department_id": "department:cwnu:cse"},
        )

    async def test_profile_and_five_state_outcome_use_separate_versioned_envelopes(self):
        base = _ChatStub(_answerable_response())
        personalized = PersonalizedCurriculumChatService(base)
        app = create_app(lambda: ChatState(personalized))
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                events = _events(
                    (
                        await client.post(
                            "/api/ask",
                            json={
                                "question": "2026학번 컴퓨터공학과이고 자료구조를 들었어.",
                                "profile": {"version": 1},
                            },
                        )
                    ).text
                )
        profile = next(item for item in events if item["type"] == "profile_update")
        outcome = next(item for item in events if item["type"] == "outcome")
        result = next(item for item in events if item["type"] == "result")
        self.assertEqual(profile["version"], 1)
        self.assertEqual(profile["profile"]["admission_year"], 2026)
        self.assertEqual(profile["profile"]["department_id"], "CSE")
        self.assertEqual(len(profile["profile"]["completed_courses"]), 1)
        self.assertEqual(outcome["version"], 1)
        self.assertEqual(outcome["status"], "ANSWERED")
        self.assertEqual(set(result["response"]), set(CHAT_RESPONSE_FIELDS))
        self.assertNotIn("profile", result["response"])
        self.assertNotIn("outcome", result["response"])

    async def test_invalid_profile_fails_closed_without_exposing_input(self):
        response = await self.client.post(
            "/api/ask",
            json={
                "question": "질문",
                "profile": {"version": 99, "note": "synthetic-private-marker"},
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("synthetic-private-marker", response.text)

    async def test_inspection_updates_stream_before_result_and_only_approved_cypher(self):
        with mock.patch.dict("os.environ", {"KG_CHAT_SHOW_QUERY_DETAILS": "true"}):
            app = create_app(lambda: ChatState(self.chat))
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    events = _events(
                        (await client.post(
                            "/api/ask", json={"question": "자료구조 이수구분"}
                        )).text
                    )
        updates = [item for item in events if item["type"] == "inspection_update"]
        static = next(item for item in updates if item["stage"] == "STATIC_VALIDATION")
        approved = next(item for item in updates if item["stage"] == "NEO4J_EXPLAIN")
        schema = next(item for item in updates if item["stage"] == "SCHEMA_SELECTION")
        result_validation = next(
            item for item in updates if item["stage"] == "RESULT_VALIDATION"
        )
        self.assertNotIn("approved_cypher", static["summary"])
        self.assertEqual(schema["summary"]["labels"], ["CourseOffering", "Evidence"])
        self.assertIn("MATCH (o:CourseOffering)", approved["summary"]["approved_cypher"])
        self.assertEqual(approved["summary"]["operators"], ["NodeIndexSeek"])
        self.assertEqual(approved["summary"]["query_graph"]["version"], 1)
        self.assertNotIn("fact_graph", result_validation["summary"])
        claims = next(item for item in updates if item["stage"] == "CLAIM_BUILDING")
        provenance = claims["summary"]["provenance_graph"]
        self.assertEqual(provenance["kind"], "RESULT_PROVENANCE")
        self.assertEqual(len(provenance["edges"]), 1)
        self.assertTrue(all(node["id"].startswith("ui:") for node in provenance["nodes"]))
        serialized_provenance = json.dumps(provenance, ensure_ascii=False)
        self.assertNotIn("offering:cwnu", serialized_provenance)
        self.assertNotIn("evidence:curriculum", serialized_provenance)
        result_index = next(index for index, item in enumerate(events) if item["type"] == "result")
        schema_index = events.index(schema)
        approved_index = events.index(approved)
        validation_index = events.index(result_validation)
        claims_index = events.index(claims)
        self.assertLess(schema_index, approved_index)
        self.assertLess(approved_index, validation_index)
        self.assertLess(validation_index, claims_index)
        self.assertLess(claims_index, result_index)
        self.assertLess(approved_index, result_index)
        allowed = {
            "QUESTION_ANALYSIS": {
                "status",
                "query_plan",
                "missing",
                "clarification_available",
            },
            "SCHEMA_SELECTION": {
                "labels",
                "relationships",
                "node_label_count",
                "relationship_count",
            },
            "CYPHER_GENERATION": {
                "candidate_generated",
                "candidate_attempt",
                "retry",
                "message",
            },
            "STATIC_VALIDATION": {
                "read_only_syntax_verified",
                "ontology_schema_verified",
                "parameter_binding_verified",
                "direct_evidence_path_verified",
                "comment_free_canonical",
                "limit",
            },
            "NEO4J_EXPLAIN": {
                "approved_cypher",
                "parameters",
                "operators",
                "labels",
                "relationships",
                "limit",
                "query_graph",
            },
            "GRAPH_EXECUTION": {"row_count", "query_elapsed_ms"},
            "RESULT_VALIDATION": {
                "row_count",
                "fact_count",
                "verified_evidence_count",
                "fact_status_verified",
                "evidence_status_verified",
                "direct_provenance_verified",
                "rejected_row_count",
            },
            "CLAIM_BUILDING": {
                "claim_count",
                "claim_types",
                "aggregate",
                "citation_target_count",
                "provenance_graph",
            },
            "ANSWER_RENDERING": {
                "citation_count",
                "deterministic_renderer",
                "final_answer_llm_calls",
            },
            "COMPLETED": {
                "total_elapsed_ms",
                "stage_timings_ms",
                "final_status",
                "retry_count",
                "citation_count",
            },
        }
        for update in updates:
            self.assertEqual(update["version"], 2)
            self.assertLessEqual(set(update["summary"]), allowed[update["stage"]])
        serialized = json.dumps(updates, ensure_ascii=False)
        for secret in ("system_prompt", "password", "bolt://", "traceback"):
            self.assertNotIn(secret, serialized)

    async def test_retry_stream_exposes_only_the_final_explain_approved_candidate(self):
        retry_chat = _RetryChatStub(_answerable_response())
        with mock.patch.dict("os.environ", {"KG_CHAT_SHOW_QUERY_DETAILS": "true"}):
            app = create_app(lambda: ChatState(retry_chat))
            async with app.router.lifespan_context(app):
                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://testserver",
                ) as client:
                    events = _events(
                        (await client.post("/api/ask", json={"question": "재시도"})).text
                    )
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("first:Rule", serialized)
        self.assertIn("second:Rule", serialized)
        retry = next(
            item
            for item in events
            if item["type"] == "progress" and item.get("retry") is True
        )
        self.assertIn("다시 생성", retry["message"])
        failed = next(
            item
            for item in events
            if item["type"] == "progress" and item["state"] == "FAILED"
        )
        self.assertEqual(failed["error_code"], "NEO4J_EXPLAIN_FAILED")

    async def test_input_validation_rejects_empty_unknown_and_overlong(self):
        self.assertEqual(
            (await self.client.post("/api/ask", json={"question": ""})).status_code,
            400,
        )
        self.assertEqual(
            (await self.client.post(
                "/api/ask", json={"question": "x", "extra": 1}
            )).status_code,
            400,
        )
        self.assertEqual(
            (await self.client.post(
                "/api/ask", json={"question": "가" * (MAX_QUESTION_LENGTH + 1)}
            )).status_code,
            422,
        )

    async def test_non_integer_and_traversal_pdf_paths_are_not_routed(self):
        self.assertEqual(
            (await self.client.get("/api/pdf/page/not-int.png")).status_code, 404
        )
        self.assertEqual(
            (await self.client.get("/api/pdf/page/%2e%2e/1.png")).status_code, 404
        )

    def test_xss_and_timeout_contract_use_safe_browser_apis(self):
        script = (Path(__file__).parents[1] / "src/evidence_chat/static/app.js").read_text()
        markup = (Path(__file__).parents[1] / "src/evidence_chat/static/index.html").read_text()
        style = (Path(__file__).parents[1] / "src/evidence_chat/static/app.css").read_text()
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("insertAdjacentHTML", script)
        self.assertNotIn("eval(", script)
        self.assertIn("textContent", script)
        self.assertIn("AbortController", script)
        self.assertIn("inFlight", script)
        self.assertIn("Math.max(60000", script)
        self.assertIn("페이지 이미지를 표시하지 못했습니다", script)
        self.assertIn("openPdfModal", script)
        self.assertIn("renderInspectionUpdate", script)
        self.assertIn("navigator.clipboard.writeText", script)
        self.assertIn("presentation_snapshot", script)
        self.assertIn("renderTimelineInto", script)
        self.assertIn("markTimelineCancelled", script)
        self.assertIn("markTimelineFailed", script)
        self.assertIn("timelineEvents = [];", script)
        self.assertNotIn("PIPELINE_PHASES.map", script)
        self.assertNotIn('state: "WAITING"', script)
        self.assertIn("started_at_ms: performance.now()", script)
        self.assertIn("Number.isFinite(running.started_at_ms)", script)
        self.assertIn("Number.isFinite(event.elapsed_ms)", script)
        self.assertIn('setAttribute("aria-expanded"', script)
        self.assertIn('setAttribute("aria-controls"', script)
        self.assertIn('setAttribute("role", "tablist"', script)
        self.assertNotIn("progress-exploration", markup)
        self.assertNotIn("answer-exploration", markup)
        self.assertNotIn("screen-progress", markup)
        self.assertNotIn("screen-answer", markup)
        self.assertNotIn("answer-again", markup)
        self.assertIn('id="composer-status"', markup)
        self.assertIn('id="jump-latest"', markup)
        self.assertIn("finishConversationTurn", script)
        self.assertIn("renderAssistantDetails", script)
        self.assertIn("event.isComposing", script)
        self.assertIn("ResizeObserver", script)
        self.assertNotIn("graph-path-traverse", style)
        self.assertNotIn("is-query-pulse", script)
        self.assertNotIn("animatedGraphs", script)
        self.assertIn("overflow-x: hidden", style)
        self.assertIn("graphLabelLines", script)
        self.assertIn("renderGraphFallback", script)
        self.assertIn("createElementNS", script)
        self.assertIn("학사규정이나 교육과정에 대해 질문해 주세요", markup)
        self.assertNotIn("Cypher 및 지식그래프 탐색 정보 보기", markup)
        self.assertIn("resize: none", style)
        self.assertIn("overflow-y: hidden", style)
        self.assertIn('const PROFILE_KEY = "evidence-chat-profile-v1"', script)
        self.assertIn("localStorage.getItem(PROFILE_KEY)", script)
        self.assertIn("localStorage.setItem(PROFILE_KEY", script)
        self.assertIn("localStorage.removeItem(PROFILE_KEY)", script)
        self.assertIn("validProfileShape", script)
        self.assertIn('payload.type === "profile_update"', script)
        self.assertIn('payload.type === "outcome"', script)
        self.assertIn("이 브라우저의 localStorage에만", markup)

    def test_frontend_does_not_construct_backend_contracts(self):
        root = Path(__file__).parents[1] / "src/evidence_chat"
        runtime = "\n".join(
            path.read_text()
            for path in root.glob("*.py")
            if path.name not in {"__init__.py"}
        )
        self.assertNotIn("ChatResponse(", runtime)
        self.assertNotIn("RuleBasedPlanner", runtime)
        self.assertNotIn("ChatPipeline", runtime)


class InspectionGraphProjectionTests(unittest.TestCase):
    def test_result_graph_requires_completed_result_validation(self):
        collector = InspectionCollector()
        row = _offering_row()
        claim_event = ProgressEvent(
            ProgressPhase.CLAIM_BUILDING,
            ProgressState.COMPLETED,
            1,
            {
                "claim_count": 1,
                "validated_rows": [row],
                "approved_provenance": [(row["fact_id"], row["evidence_id"])],
            },
        )
        before = collector.record(claim_event)
        self.assertIsNone(before["summary"]["provenance_graph"])

        collector.record(
            ProgressEvent(
                ProgressPhase.RESULT_VALIDATION,
                ProgressState.COMPLETED,
                1,
                {
                    "row_count": 1,
                    "fact_count": 1,
                    "evidence_count": 1,
                    "fact_status_verified": True,
                    "evidence_status_verified": True,
                    "direct_provenance_verified": True,
                },
            )
        )
        after = collector.record(claim_event)
        self.assertEqual(after["summary"]["provenance_graph"]["kind"], "RESULT_PROVENANCE")

    def test_query_projection_uses_only_approved_schema_members(self):
        graph = build_query_structure_projection(
            ["CourseOffering", "Course", "Evidence"],
            ["OF_COURSE", "SUPPORTED_BY", "NOT_A_RELATION"],
            opaque_key=b"query-test-key",
        )
        self.assertIsNotNone(graph)
        self.assertEqual(graph["version"], 1)
        self.assertEqual(graph["kind"], "QUERY_STRUCTURE")
        self.assertEqual(
            {edge["relationship"] for edge in graph["edges"]},
            {"OF_COURSE", "SUPPORTED_BY"},
        )
        serialized = json.dumps(graph, ensure_ascii=False)
        self.assertNotIn("NOT_A_RELATION", serialized)

    def test_provenance_projection_requires_exact_verified_pairs(self):
        row = _offering_row()
        pair = (row["fact_id"], row["evidence_id"])
        graph = build_provenance_projection(
            [row], [pair], opaque_key=b"result-test-key"
        )
        self.assertIsNotNone(graph)
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        serialized = json.dumps(graph, ensure_ascii=False)
        self.assertNotIn(row["fact_id"], serialized)
        self.assertNotIn(row["evidence_id"], serialized)

        unrelated = _offering_row(1)
        self.assertIsNone(
            build_provenance_projection(
                [row, unrelated], [pair], opaque_key=b"result-test-key"
            )
        )
        review_required = {**row, "fact_status": "REVIEW_REQUIRED"}
        self.assertIsNone(
            build_provenance_projection(
                [review_required], [pair], opaque_key=b"result-test-key"
            )
        )


if __name__ == "__main__":
    unittest.main()
