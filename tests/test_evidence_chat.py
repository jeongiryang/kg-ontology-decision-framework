"""챗봇 화면 계층 단위 테스트.

Neo4j 없이 동작한다. 질의 실행기는 가짜 구현으로 대체하고, PDF 강조 로직은
테스트 안에서 만든 합성 PDF로 검증한다.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from unittest import mock

from kg_builder.query_contracts import (
    Answerability,
    Intent,
    QueryRequest,
    QueryResponse,
    EvidenceDTO,
)

from evidence_chat import answer as answer_module
from evidence_chat import pdf_evidence
from evidence_chat.pipeline import STEP_SEQUENCE, ChatPipeline, StepStatus
from evidence_chat.planner import (
    EXAMPLE_QUESTIONS,
    PlannerError,
    RuleBasedPlanner,
    normalize_question,
)


SAMPLE_RAW_TEXT = (
    "기초교양 | 미래설계 | GEA8001 | 대학생활의설계 | 1학점 | 이론 1 | 실기 0"
)


@contextmanager
def _pdf_env(path: str):
    """발췌 PDF 경로를 바꿔 끼운다. 없는 경로를 주면 미탑재 상태가 된다."""
    with mock.patch.dict("os.environ", {pdf_evidence.PDF_PATH_ENV: path}):
        yield


def _compose(intent: Intent, answerability: Answerability, answer: dict) -> dict:
    """compose에 넘길 응답 봉투를 채워 준다."""
    return answer_module.compose(
        {
            "intent": intent.value,
            "answerability": answerability.value,
            "answer": answer,
            "scope": {},
            "evidence": [],
            "warnings": [],
        }
    )


def _response(
    intent: Intent,
    answerability: Answerability,
    answer: dict,
    evidence: tuple = None,
    warnings: tuple = (),
) -> QueryResponse:
    """QueryResponse를 키워드로 만든다. 필드 순서 변경에 흔들리지 않게 한다."""
    if evidence is None:
        evidence = (_evidence(),) if answerability is Answerability.ANSWERABLE else ()
    return QueryResponse(
        intent=intent,
        answerability=answerability,
        answer=answer,
        scope={"academic_year": 2026},
        evidence=evidence,
        warnings=warnings,
    )


def _evidence(evidence_id: str = "evidence:test:1", page: int = 3) -> EvidenceDTO:
    return EvidenceDTO(
        evidence_id=evidence_id,
        excerpt_page=page,
        source_pdf_page=page + 30,
        printed_page=page + 20,
        source_text=SAMPLE_RAW_TEXT,
    )


class FakeRunner:
    """QueryService 자리에 끼우는 결정론적 실행기."""

    def __init__(self, response: QueryResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.requests: list[QueryRequest] = []

    def execute(self, request: QueryRequest) -> QueryResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class NormalizeQuestionTests(unittest.TestCase):
    def test_normalizes_fullwidth_and_whitespace(self):
        self.assertEqual(normalize_question("  자료구조　　학점  "), "자료구조 학점")

    def test_rejects_empty_and_blank(self):
        for value in ("", "   ", "\n\t"):
            with self.subTest(value=value):
                with self.assertRaises(PlannerError):
                    normalize_question(value)

    def test_rejects_non_string(self):
        with self.assertRaises(PlannerError):
            normalize_question(None)  # type: ignore[arg-type]

    def test_rejects_overlong_question(self):
        with self.assertRaises(PlannerError):
            normalize_question("가" * 301)


class RuleBasedPlannerTests(unittest.TestCase):
    def setUp(self):
        self.planner = RuleBasedPlanner(course_lexicon={"자료구조": "CDA0008"})

    def test_example_questions_map_to_valid_requests(self):
        expected = {
            EXAMPLE_QUESTIONS[0]: Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
            EXAMPLE_QUESTIONS[1]: Intent.GET_BALANCED_GENERAL_REQUIREMENT,
            EXAMPLE_QUESTIONS[2]: Intent.GET_TRANSFER_GENERAL_EXEMPTION,
            EXAMPLE_QUESTIONS[3]: Intent.GET_COURSE_OFFERING,
            EXAMPLE_QUESTIONS[4]: Intent.GET_MAJOR_REQUIRED_COURSES,
            EXAMPLE_QUESTIONS[5]: Intent.GET_COURSE_COMPLETION_TYPE,
        }
        for question, intent in expected.items():
            with self.subTest(question=question):
                plan = self.planner.plan(question)
                self.assertEqual(plan.intent, intent)
                # 계약 검증을 통과하는 요청이어야 한다.
                request = QueryRequest.from_dict(plan.request_payload)
                self.assertEqual(request.intent, intent)

    def test_extracts_academic_year(self):
        plan = self.planner.plan("2027학년도 교양 최소 학점 알려줘")
        self.assertEqual(plan.parameters["academic_year"], 2027)

    def test_defaults_year_and_records_note(self):
        plan = self.planner.plan("교양 최소 학점 알려줘")
        self.assertEqual(plan.parameters["academic_year"], 2026)
        self.assertTrue(any("기본값" in note for note in plan.notes))

    def test_course_code_takes_priority_over_name(self):
        plan = self.planner.plan("CDA0143 은 몇 학년에 열려?")
        self.assertEqual(plan.parameters["course_code"], "CDA0143")
        self.assertNotIn("course_name", plan.parameters)

    def test_course_code_is_uppercased(self):
        plan = self.planner.plan("cda0143 편성 알려줘")
        self.assertEqual(plan.parameters["course_code"], "CDA0143")

    def test_completion_type_intent_needs_course(self):
        plan = self.planner.plan("자료구조 이수구분 알려줘")
        self.assertEqual(plan.intent, Intent.GET_COURSE_COMPLETION_TYPE)

    def test_major_required_without_course(self):
        plan = self.planner.plan("전공필수 뭐가 있어?")
        self.assertEqual(plan.intent, Intent.GET_MAJOR_REQUIRED_COURSES)

    def test_out_of_scope_question_is_rejected(self):
        for question in ("오늘 점심 뭐 먹지", "장학금 신청 방법 알려줘"):
            with self.subTest(question=question):
                with self.assertRaises(PlannerError):
                    self.planner.plan(question)

    def test_plan_exposes_debug_fields(self):
        plan = self.planner.plan("균형교양 요건 알려줘")
        payload = plan.to_dict()
        self.assertEqual(payload["planner"], plan.planner_name)
        self.assertIn("matched_signals", payload)
        self.assertIn("normalized_question", payload)


class AnswerCompositionTests(unittest.TestCase):
    def test_credit_unit_is_localized(self):
        composed = _compose(
            Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
            Answerability.ANSWERABLE,
            {"requirement": {"value": 34, "unit": "CREDIT", "rule_id": "r"}},
        )
        self.assertIn("34학점", composed["headline"])
        self.assertTrue(composed["is_confirmed"])

    def test_unknown_unit_is_preserved(self):
        composed = _compose(
            Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
            Answerability.ANSWERABLE,
            {"requirement": {"value": 3, "unit": "WEEK"}},
        )
        self.assertIn("3WEEK", composed["headline"])

    def test_offering_headline_uses_korean_labels(self):
        composed = _compose(
            Intent.GET_COURSE_OFFERING,
            Answerability.ANSWERABLE,
            {
                    "course": {"course_code": "CDA0008", "course_name": "자료구조"},
                    "offerings": [
                        {
                            "grade_year": [2],
                            "semester": "FIRST",
                            "credits": 3,
                            "completion_type": "MAJOR_ELECTIVE",
                            "lecture_hours": 3,
                            "practice_hours": 0,
                        }
                    ],
                },
        )
        self.assertIn("2학년", composed["headline"])
        self.assertIn("1학기", composed["headline"])
        self.assertIn("전공선택", composed["details"][0])

    def test_empty_grade_year_is_not_invented(self):
        composed = _compose(
            Intent.GET_COURSE_OFFERING,
            Answerability.ANSWERABLE,
            {
                    "course": {"course_code": "GEA8001", "course_name": "대학생활의설계"},
                    "offerings": [
                        {
                            "grade_year": [],
                            "semester": "BOTH",
                            "credits": 1,
                            "completion_type": "GENERAL_ELECTIVE",
                            "lecture_hours": 1,
                            "practice_hours": 0,
                        }
                    ],
                },
        )
        self.assertIn("개설 학년 미표기", composed["headline"])

    def test_non_answerable_states_are_labeled(self):
        cases = {
            Answerability.CLARIFICATION_REQUIRED: "학수번호",
            Answerability.UNRESOLVED: "검토 보류",
            Answerability.OUT_OF_SCOPE: "지원 범위",
            Answerability.NOT_FOUND: "찾지 못했습니다",
        }
        for answerability, needle in cases.items():
            with self.subTest(answerability=answerability):
                composed = answer_module.compose(
                    {
                        "intent": Intent.GET_COURSE_OFFERING.value,
                        "answerability": answerability.value,
                        "answer": {"candidates": []},
                        "scope": {},
                        "evidence": [],
                        "warnings": [],
                    }
                )
                self.assertFalse(composed["is_confirmed"])
                self.assertIn(needle, composed["headline"])


class NeedleExtractionTests(unittest.TestCase):
    def test_splits_on_table_separators(self):
        needles = pdf_evidence.needles_from_raw_text(SAMPLE_RAW_TEXT)
        self.assertIn("GEA8001", needles)
        self.assertIn("대학생활의설계", needles)

    def test_drops_short_numeric_noise(self):
        needles = pdf_evidence.needles_from_raw_text("이론 1 | 실기 0 | GEA8001")
        self.assertNotIn("1", needles)
        self.assertNotIn("0", needles)
        self.assertIn("GEA8001", needles)

    def test_empty_text_yields_no_needles(self):
        self.assertEqual(pdf_evidence.needles_from_raw_text(""), [])

    def test_longer_needles_come_first(self):
        needles = pdf_evidence.needles_from_raw_text("가나 | 가나다라마 | 가나다")
        self.assertEqual(needles[0], "가나다라마")


class PdfEvidenceTests(unittest.TestCase):
    """합성 PDF로 렌더링과 강조 좌표 계산을 검증한다."""

    @classmethod
    def setUpClass(cls):
        try:
            import pymupdf
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("pymupdf가 설치되지 않았습니다.")
        cls._pymupdf = pymupdf
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

    def test_missing_pdf_reports_unavailable_without_raising(self):
        source = pdf_evidence.inspect_pdf(Path("/nonexistent/curriculum.pdf"))
        self.assertFalse(source.available)
        self.assertIn("발췌 PDF가 없습니다", source.reason or "")
        self.assertEqual(source.page_count, 0)

    def test_inspect_reports_page_count_and_hash_mismatch(self):
        source = pdf_evidence.inspect_pdf(self.pdf_path)
        self.assertTrue(source.available)
        self.assertEqual(source.page_count, 2)
        self.assertFalse(source.sha256_matches)
        self.assertIn("해시", source.reason or "")

    def test_find_highlights_returns_normalized_boxes(self):
        highlights = pdf_evidence.find_highlights("GEA8001", 1, self.pdf_path)
        self.assertTrue(highlights)
        for box in highlights:
            self.assertGreater(box.width, 0)
            self.assertGreater(box.height, 0)
            for value in (box.x, box.y, box.width, box.height):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)

    def test_find_highlights_is_empty_for_absent_text(self):
        self.assertEqual(
            pdf_evidence.find_highlights("존재하지않는문자열", 1, self.pdf_path), []
        )

    def test_find_highlights_rejects_out_of_range_page(self):
        self.assertEqual(pdf_evidence.find_highlights("GEA8001", 99, self.pdf_path), [])

    def test_render_page_png_returns_png_bytes(self):
        with _pdf_env(str(self.pdf_path)):
            image = pdf_evidence.render_page_png(1)
        self.assertTrue(image.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_render_page_png_raises_for_missing_pdf(self):
        with _pdf_env("/nonexistent.pdf"):
            with self.assertRaises(pdf_evidence.PdfEvidenceError):
                pdf_evidence.render_page_png(1)

    def test_render_page_png_raises_for_out_of_range_page(self):
        with _pdf_env(str(self.pdf_path)):
            with self.assertRaises(pdf_evidence.PdfEvidenceError):
                pdf_evidence.render_page_png(99)

    def test_build_evidence_pages_groups_only_referenced_pages(self):
        with _pdf_env(str(self.pdf_path)):
            pages = pdf_evidence.build_evidence_pages(
                [
                    _evidence("e1", page=1).to_dict(),
                    _evidence("e2", page=1).to_dict(),
                    _evidence("e3", page=2).to_dict(),
                ]
            )
        self.assertEqual([page["excerpt_page"] for page in pages], [1, 2])
        self.assertEqual(len(pages[0]["evidence"]), 2)
        self.assertTrue(pages[0]["evidence"][0]["highlight_found"])

    def test_build_evidence_pages_without_pdf_keeps_text_only(self):
        with _pdf_env("/nonexistent.pdf"):
            pages = pdf_evidence.build_evidence_pages([_evidence(page=5).to_dict()])
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["evidence"][0]["highlights"], [])
        self.assertFalse(pages[0]["evidence"][0]["highlight_found"])
        self.assertEqual(pages[0]["evidence"][0]["source_text"], SAMPLE_RAW_TEXT)


class PipelineTests(unittest.TestCase):
    def _pipeline(self, runner) -> ChatPipeline:
        return ChatPipeline(
            runner=runner,
            planner=RuleBasedPlanner(course_lexicon={"자료구조": "CDA0008"}),
        )

    @staticmethod
    def _collect(events):
        steps: dict[str, str] = {}
        result = None
        errors = []
        for event in events:
            if event["type"] == "step":
                if event["status"] != StepStatus.RUNNING.value:
                    steps[event["step_id"]] = event["status"]
            elif event["type"] == "result":
                result = event
            elif event["type"] == "error":
                errors.append(event)
        return steps, result, errors

    def test_answerable_run_reports_every_step(self):
        response = _response(Intent.GET_GENERAL_EDUCATION_MIN_CREDITS, Answerability.ANSWERABLE, {"requirement": {"value": 34, "unit": "CREDIT", "rule_id": "r"}})
        pipeline = self._pipeline(FakeRunner(response=response))
        with _pdf_env("/nonexistent.pdf"):
            steps, result, errors = self._collect(pipeline.run("교양 최소 학점 알려줘"))
        self.assertEqual(errors, [])
        self.assertIsNotNone(result)
        self.assertEqual(steps["normalize"], "done")
        self.assertEqual(steps["neo4j"], "done")
        self.assertEqual(steps["evidence"], "done")
        # PDF가 없으면 done이 아니라 skipped로 보고해야 한다.
        self.assertEqual(steps["pdf"], "skipped")
        self.assertEqual(steps["compose"], "done")
        self.assertTrue(result["answer"]["is_confirmed"])
        self.assertEqual(len(result["evidence_pages"]), 1)

    def test_cypher_step_exposes_template_for_debugging(self):
        response = _response(Intent.GET_BALANCED_GENERAL_REQUIREMENT, Answerability.ANSWERABLE, {"requirements": [{"value": 12, "unit": "CREDIT"}]})
        pipeline = self._pipeline(FakeRunner(response=response))
        cypher_events = [
            event
            for event in pipeline.run("균형교양 요건 알려줘")
            if event["type"] == "step" and event["step_id"] == "cypher"
        ]
        done = [event for event in cypher_events if event["status"] == "done"]
        self.assertEqual(len(done), 1)
        self.assertIn("MATCH", done[0]["data"]["cypher"])

    def test_planner_failure_skips_remaining_steps(self):
        pipeline = self._pipeline(FakeRunner())
        steps, result, errors = self._collect(pipeline.run("오늘 점심 뭐 먹지"))
        self.assertIsNone(result)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["stage"], "plan")
        # 정규화는 성공한다. 실패는 Intent를 고르는 단계에서 난다.
        self.assertEqual(steps["normalize"], "done")
        self.assertEqual(steps["plan"], "failed")
        for step_id in ("contract", "cypher", "neo4j", "evidence", "pdf", "compose"):
            self.assertEqual(steps[step_id], "skipped")

    def test_blank_question_fails_at_normalize_step(self):
        pipeline = self._pipeline(FakeRunner())
        steps, result, errors = self._collect(pipeline.run("   "))
        self.assertIsNone(result)
        self.assertEqual(errors[0]["stage"], "normalize")
        self.assertEqual(steps["normalize"], "failed")
        self.assertEqual(steps["plan"], "skipped")

    def test_neo4j_failure_is_reported_not_swallowed(self):
        pipeline = self._pipeline(FakeRunner(error=RuntimeError("boom")))
        steps, result, errors = self._collect(pipeline.run("교양 최소 학점 알려줘"))
        self.assertIsNone(result)
        self.assertEqual(errors[0]["stage"], "neo4j")
        self.assertEqual(steps["neo4j"], "failed")
        self.assertEqual(steps["compose"], "skipped")

    def test_evidence_step_skipped_when_no_evidence(self):
        response = _response(
            Intent.GET_COURSE_OFFERING,
            Answerability.NOT_FOUND,
            {},
            evidence=(),
            warnings=("일치하는 VERIFIED 과목 편성이 없습니다.",),
        )
        pipeline = self._pipeline(FakeRunner(response=response))
        steps, result, _ = self._collect(pipeline.run("자료구조 편성 알려줘"))
        self.assertEqual(steps["evidence"], "skipped")
        self.assertEqual(steps["pdf"], "skipped")
        self.assertEqual(result["evidence_pages"], [])
        self.assertFalse(result["answer"]["is_confirmed"])

    def test_runner_receives_validated_request(self):
        runner = FakeRunner(
            response=_response(
                Intent.GET_MAJOR_REQUIRED_COURSES,
                Answerability.ANSWERABLE,
                {"courses": [], "course_count": 0, "total_credits": 0},
            )
        )
        pipeline = self._pipeline(runner)
        list(pipeline.run("전공필수 알려줘"))
        self.assertEqual(len(runner.requests), 1)
        self.assertEqual(runner.requests[0].intent, Intent.GET_MAJOR_REQUIRED_COURSES)
        self.assertEqual(runner.requests[0].parameters["academic_year"], 2026)


class ServerEndpointTests(unittest.TestCase):
    """Neo4j 없이도 화면과 오류 응답이 동작하는지 확인한다."""

    @classmethod
    def setUpClass(cls):
        try:
            from starlette.testclient import TestClient
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("starlette TestClient를 사용할 수 없습니다.")
        cls._TestClient = TestClient

    def _client(self):
        """앱 인스턴스를 새로 만들어 상태가 테스트 간에 새지 않게 한다."""
        from evidence_chat import server

        # 연결 시도를 막아 Neo4j 없는 상태를 재현한다.
        patcher = mock.patch.object(
            server.ChatState,
            "open",
            lambda self: setattr(self, "error", "Neo4j 연결 실패: 테스트"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return self._TestClient(server.create_app())

    def test_index_serves_three_screen_markup(self):
        with self._client() as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        for marker in ("screen-ask", "screen-progress", "screen-answer"):
            self.assertIn(marker, response.text)

    def test_health_reports_disconnected_state(self):
        with self._client() as client:
            payload = client.get("/api/health").json()
        self.assertFalse(payload["neo4j_connected"])
        self.assertIn("Neo4j 연결 실패", payload["error"])
        self.assertEqual(len(payload["examples"]), len(EXAMPLE_QUESTIONS))

    def test_ask_requires_question_field(self):
        with self._client() as client:
            response = client.post("/api/ask", json={})
        self.assertEqual(response.status_code, 400)

    def test_ask_rejects_invalid_json(self):
        with self._client() as client:
            response = client.post(
                "/api/ask",
                content=b"{not json",
                headers={"Content-Type": "application/json"},
            )
        self.assertEqual(response.status_code, 400)

    def test_ask_returns_503_when_neo4j_unavailable(self):
        with self._client() as client:
            response = client.post("/api/ask", json={"question": "교양 최소 학점"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("Neo4j", response.json()["error"])

    def test_ask_rejects_oversized_body(self):
        with self._client() as client:
            response = client.post("/api/ask", json={"question": "가" * 20000})
        self.assertEqual(response.status_code, 413)

    def test_pdf_page_returns_404_without_pdf(self):
        with _pdf_env("/nonexistent.pdf"):
            with self._client() as client:
                response = client.get("/api/pdf/page/1.png")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    def test_app_state_is_per_instance(self):
        """전역 상태를 쓰지 않으므로 두 앱 인스턴스가 서로 영향을 주지 않는다."""
        with self._client() as first, self._client() as second:
            first.app.state.chat.error = "첫 번째 앱만의 오류"
            self.assertEqual(second.app.state.chat.error, "Neo4j 연결 실패: 테스트")

    def test_ask_streams_steps_when_pipeline_available(self):
        response = _response(
            Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
            Answerability.ANSWERABLE,
            {"requirement": {"value": 34, "unit": "CREDIT", "rule_id": "r"}},
        )
        with self._client() as client:
            client.app.state.chat.pipeline = ChatPipeline(
                runner=FakeRunner(response=response),
                planner=RuleBasedPlanner(course_lexicon={}),
            )
            with _pdf_env("/nonexistent.pdf"):
                streamed = client.post("/api/ask", json={"question": "교양 최소 학점 알려줘"})
        self.assertEqual(streamed.status_code, 200)
        self.assertEqual(streamed.headers["content-type"].split(";")[0], "text/event-stream")
        events = [
            json.loads(line[6:])
            for line in streamed.text.splitlines()
            if line.startswith("data: ")
        ]
        kinds = [event["type"] for event in events]
        self.assertIn("result", kinds)
        self.assertEqual(kinds[-1], "end")
        result = next(event for event in events if event["type"] == "result")
        self.assertIn("34학점", result["answer"]["headline"])


class ContractDriftTests(unittest.TestCase):
    """선언과 구현이 조용히 어긋나는 것을 막는 검사."""

    def test_emitted_steps_match_declared_sequence(self):
        """방출된 단계 순서가 STEP_SEQUENCE와 정확히 같아야 한다.

        선언만 하고 실제로 수행하지 않는 단계, 또는 순서가 뒤바뀐 방출을 잡는다.
        """
        pipeline = ChatPipeline(
            runner=FakeRunner(
                response=_response(
                    Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
                    Answerability.ANSWERABLE,
                    {"requirement": {"value": 34, "unit": "CREDIT", "rule_id": "r"}},
                )
            ),
            planner=RuleBasedPlanner(course_lexicon={}),
        )
        with _pdf_env("/nonexistent.pdf"):
            events = [
                event
                for event in pipeline.run("교양 최소 학점 알려줘")
                if event["type"] == "step"
            ]
        declared = [step_id for step_id, _ in STEP_SEQUENCE]
        self.assertEqual([event["step_id"] for event in events[::2]], declared)
        # 각 단계는 RUNNING 다음에 종료 상태가 정확히 한 번 온다.
        for start, end in zip(events[::2], events[1::2]):
            with self.subTest(step=start["step_id"]):
                self.assertEqual(start["status"], StepStatus.RUNNING.value)
                self.assertEqual(end["step_id"], start["step_id"])
                self.assertNotEqual(end["status"], StepStatus.RUNNING.value)
        for event in events:
            self.assertEqual(event["total"], len(declared))

    def test_every_intent_has_a_builder_and_label(self):
        """Intent가 늘어나면 요청 중이 아니라 여기서 먼저 실패해야 한다."""
        self.assertEqual(set(answer_module._BUILDERS), set(Intent))
        self.assertEqual(set(answer_module.INTENT_LABELS), set(Intent))
        self.assertEqual(set(answer_module.ANSWERABILITY_LABELS), set(Answerability))

    def test_display_labels_cover_ontology_vocabulary(self):
        """화면 라벨 맵이 온톨로지 통제어휘를 빠짐없이 덮는지 확인한다.

        라벨 문구는 명세 설명문과 의도적으로 다르므로 값을 비교하지 않고
        키 집합만 검사한다. 어휘가 늘어나면 여기서 드러난다.
        """
        spec_path = pdf_evidence.bundle.ROOT / "ontology/ontology_spec.json"
        if not spec_path.is_file():  # pragma: no cover
            self.skipTest("온톨로지 명세를 찾을 수 없습니다.")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        vocabularies = spec.get("controlled_vocabularies", {})
        checks = {
            "semester": answer_module.SEMESTER_LABELS,
            "completion_type": answer_module.COMPLETION_TYPE_LABELS,
        }
        for name, labels in checks.items():
            with self.subTest(vocabulary=name):
                declared = {entry["value"] for entry in vocabularies[name]["values"]}
                self.assertEqual(declared - set(labels), set())

    def test_planner_default_scope_matches_query_service(self):
        """플래너 기본 범위가 질의 계층이 강제하는 범위와 같아야 한다."""
        from kg_builder.query_service import DEPARTMENT_ALIASES, SUPPORTED_YEAR

        from evidence_chat import planner as planner_module

        self.assertEqual(planner_module.DEFAULT_ACADEMIC_YEAR, SUPPORTED_YEAR)
        self.assertIn(planner_module.DEFAULT_DEPARTMENT, DEPARTMENT_ALIASES)

    def test_expected_pdf_hash_comes_from_verified_bundle(self):
        """기준 해시를 코드에 복사하지 않고 bundle에서 읽는지 확인한다."""
        document = pdf_evidence.bundle.source_document()
        self.assertEqual(pdf_evidence.expected_sha256(), document.get("sha256"))
        self.assertRegex(pdf_evidence.expected_sha256() or "", r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
