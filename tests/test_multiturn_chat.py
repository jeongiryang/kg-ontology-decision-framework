from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx2

from evidence_chat.server import ChatState, create_app
from kg_builder.agent import AgenticCurriculumChatService
from kg_builder.answer.contracts import ChatResponse
from kg_builder.answer.personalized_service import PersonalizedChatResult
from kg_builder.llm.models import LLMGeneration
from kg_builder.personalization import DecisionOutcome, OutcomeStatus, UserProfile
from kg_builder.query.course_names import CourseIdentity, CourseNameResolver


class _LLM:
    model = "fake"

    def generate_json(self, **kwargs):
        schema = kwargs["response_schema"]
        if "resolved_question" in schema["properties"]:
            return LLMGeneration(
                {
                    "resolved_question": "그 과목의 이수구분은?",
                    "referenced_course_codes": ["CDA0008"],
                    "tools": ["resolve_course", "query_curriculum"],
                    "topic": "자료구조",
                },
                0.001,
                self.model,
            )
        return LLMGeneration(
            {"intro": "앞선 맥락을 반영해 확인했습니다.", "closing": ""},
            0.001,
            self.model,
        )


class _GroundedService:
    def __init__(self):
        self.course_resolver = CourseNameResolver(
            [CourseIdentity("course:cwnu:CDA0008", "CDA0008", "자료구조")]
        )

    def ask(self, question, *, profile=None, resolved=None, progress_callback=None):
        del question, resolved, progress_callback
        response = ChatResponse.unresolved("request:multiturn-test")
        return PersonalizedChatResult(
            response,
            DecisionOutcome(
                OutcomeStatus.INSUFFICIENT_EVIDENCE,
                "현재 검증된 근거에서 확인하지 못했습니다.",
            ),
            profile or UserProfile(),
        )


def _events(text):
    return [
        json.loads(line[6:])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


class MultiTurnRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service = AgenticCurriculumChatService(_GroundedService(), _LLM())
        self.app = create_app(lambda: ChatState(service))
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)

    async def test_versioned_context_emits_trace_and_update_without_changing_result(self):
        response = await self.client.post(
            "/api/ask",
            json={
                "question": "그거는 필수야?",
                "profile": {"version": 1},
                "conversation": {
                    "version": 1,
                    "conversation_id": "conversation:test-1234",
                    "turn_id": "turn:test-5678",
                    "recent_messages": [
                        {
                            "turn_id": "turn:previous-1",
                            "role": "assistant",
                            "content": "이전 답변",
                            "created_at": "2026-08-29T00:00:00Z",
                            "response_status": "ANSWERED",
                            "citation_ids": [],
                            "evidence_ids": [],
                        }
                    ],
                    "summary": "최근 대화 상태: ANSWERED.",
                    "current_topic": "자료구조",
                    "recent_course_codes": ["CDA0008"],
                    "recent_evidence_ids": [],
                    "pending_clarification": None,
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        events = _events(response.text)
        trace = [item for item in events if item["type"] == "agent_trace"]
        update = next(item for item in events if item["type"] == "conversation_update")
        result = next(item for item in events if item["type"] == "result")
        self.assertEqual(
            [item["tool"] for item in trace],
            [
                "read_user_profile",
                "resolve_course",
                "query_curriculum",
                "grounded_narrative",
            ],
        )
        self.assertEqual(update["version"], 1)
        self.assertEqual(update["conversation_id"], "conversation:test-1234")
        self.assertIn("현재 검증된 근거", update["display_answer"])
        self.assertEqual(
            set(result["response"]),
            {
                "request_id", "status", "answer_text", "citations",
                "used_fact_ids", "used_evidence_ids", "clarification", "error_code",
            },
        )
        self.assertNotIn("display_answer", result["response"])

    async def test_invalid_or_oversized_context_fails_closed(self):
        response = await self.client.post(
            "/api/ask",
            json={
                "question": "질문",
                "conversation": {
                    "version": 999,
                    "conversation_id": "conversation:test-1234",
                    "turn_id": "turn:test-5678",
                    "recent_messages": [],
                    "secret": "synthetic-password-marker",
                },
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("synthetic-password-marker", response.text)

    async def test_pending_clarification_is_bounded_and_closed(self):
        base = {
            "version": 1,
            "conversation_id": "conversation:test-1234",
            "turn_id": "turn:test-5678",
            "recent_messages": [],
            "recent_course_codes": [],
        }
        for pending in (
            {"prompt": "질문", "system_prompt": "override"},
            {"prompt": "x" * 501},
            {"prompt": ""},
        ):
            with self.subTest(pending=pending):
                response = await self.client.post(
                    "/api/ask",
                    json={
                        "question": "질문",
                        "conversation": {**base, "pending_clarification": pending},
                    },
                )
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("override", response.text)


class IndexedDbPresentationTests(unittest.TestCase):
    def test_versioned_indexeddb_rooms_and_safe_dom_contract(self):
        script = Path("src/evidence_chat/static/app.js").read_text(encoding="utf-8")
        markup = Path("src/evidence_chat/static/index.html").read_text(encoding="utf-8")
        self.assertIn('indexedDB.open(CONVERSATION_DB, CONVERSATION_DB_VERSION)', script)
        self.assertIn('const CONVERSATION_DB_VERSION = 2', script)
        self.assertIn('createObjectStore(CONVERSATION_STORE', script)
        self.assertIn('createObjectStore(MESSAGE_STORE', script)
        self.assertIn('recent_messages: recent', script)
        self.assertIn('localStorage.setItem(CURRENT_CONVERSATION_KEY', script)
        self.assertIn('presentation_snapshot', script)
        self.assertIn('scroll_top', script)
        self.assertIn('renderTurnChoices', script)
        self.assertIn('response.status === "SAFE_FAILURE"', script)
        self.assertNotIn(".innerHTML", script)
        self.assertIn('id="conversation-new"', markup)
        self.assertIn('id="conversation-clear"', markup)
        self.assertIn('id="conversation-list"', markup)
        self.assertIn('id="conversation-transcript"', markup)
        self.assertIn('id="ask-form" class="chat-composer"', markup)
        self.assertNotIn('id="answer-again"', markup)
        self.assertNotIn('id="screen-progress"', markup)


if __name__ == "__main__":
    unittest.main()
