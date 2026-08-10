"""Starlette presentation tests without a live model or Neo4j server."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

from evidence_chat import pdf_evidence
from evidence_chat.chat_adapter import CHAT_RESPONSE_FIELDS, ChatResponseAdapter
from evidence_chat.server import ChatState, create_app
from kg_builder.answer.contracts import ChatErrorCode, ChatResponse, ChatStatus
from kg_builder.answer.service import CurriculumChatService
from kg_builder.query.natural_language_service import NaturalLanguageResult
from kg_builder.query.query_plan import MAX_QUESTION_LENGTH


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

    def ask(self, question: str) -> NaturalLanguageResult:
        del question
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

    def ask(self, question: str) -> ChatResponse:
        self.questions.append(question)
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
        for index in range(2):
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


class StarletteRouteTests(unittest.TestCase):
    def setUp(self):
        self.chat = _ChatStub(_answerable_response())
        self.client = TestClient(create_app(lambda: ChatState(self.chat)))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def test_health_is_path_and_connection_secret_free(self):
        payload = self.client.get("/api/health").json()
        self.assertTrue(payload["service_ready"])
        self.assertNotIn("neo4j_endpoint", payload)
        self.assertNotIn("neo4j_database", payload)
        self.assertNotIn("path", payload["pdf"])
        self.assertGreaterEqual(payload["client_timeout_seconds"], 60)

    def test_ask_streams_generic_progress_and_approved_response(self):
        response = self.client.post("/api/ask", json={"question": "자료구조 이수구분"})
        self.assertEqual(response.status_code, 200)
        events = _events(response.text)
        self.assertEqual(
            [item["phase"] for item in events if item["type"] == "progress"],
            ["SUBMITTED", "CHECKING", "COMPLETED"],
        )
        result = next(item for item in events if item["type"] == "result")
        self.assertEqual(result["response"]["status"], "ANSWERABLE")
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("MATCH ", serialized)
        self.assertNotIn("QueryPlan", serialized)
        self.assertEqual(self.chat.questions, ["자료구조 이수구분"])

    def test_input_validation_rejects_empty_unknown_and_overlong(self):
        self.assertEqual(self.client.post("/api/ask", json={"question": ""}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/ask", json={"question": "x", "extra": 1}).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/api/ask", json={"question": "가" * (MAX_QUESTION_LENGTH + 1)}
            ).status_code,
            422,
        )

    def test_non_integer_and_traversal_pdf_paths_are_not_routed(self):
        self.assertEqual(self.client.get("/api/pdf/page/not-int.png").status_code, 404)
        self.assertEqual(self.client.get("/api/pdf/page/%2e%2e/1.png").status_code, 404)

    def test_xss_and_timeout_contract_use_safe_browser_apis(self):
        script = (Path(__file__).parents[1] / "src/evidence_chat/static/app.js").read_text()
        self.assertNotIn("innerHTML", script)
        self.assertIn("textContent", script)
        self.assertIn("AbortController", script)
        self.assertIn("inFlight", script)
        self.assertIn("Math.max(60000", script)
        self.assertIn("페이지 이미지를 표시하지 못했습니다", script)

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


if __name__ == "__main__":
    unittest.main()
