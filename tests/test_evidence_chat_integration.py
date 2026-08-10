"""End-to-end Starlette SSE smoke against local Ollama and read-only Neo4j."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from time import perf_counter

from neo4j import GraphDatabase
from starlette.testclient import TestClient

from evidence_chat.server import ChatState, create_app
from kg_builder.config import Neo4jQuerySettings


@unittest.skipUnless(
    os.getenv("KG_LOCAL_LLM_INTEGRATION") == "1",
    "set KG_LOCAL_LLM_INTEGRATION=1 for Starlette/Ollama/Neo4j smoke tests",
)
class EvidenceChatIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = Neo4jQuerySettings.from_env()
        cls.count_driver = GraphDatabase.driver(
            cls.settings.uri,
            auth=(cls.settings.user, cls.settings.password),
        )
        cls.count_driver.verify_connectivity()
        cls.before = cls._counts()
        cls.trace_temp = tempfile.TemporaryDirectory()
        cls.trace_dir = Path(cls.trace_temp.name)
        cls.client = TestClient(
            create_app(lambda: ChatState(trace_dir=cls.trace_dir))
        )
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.client.__exit__(None, None, None)
            after = cls._counts()
            if after != cls.before:
                raise AssertionError(
                    f"read-only Starlette smoke changed DB: {cls.before} -> {after}"
                )
        finally:
            cls.count_driver.close()
            trace_dir = cls.trace_dir
            cls.trace_temp.cleanup()
        if trace_dir.exists():
            raise AssertionError("temporary Starlette trace directory was not cleaned")

    @classmethod
    def _counts(cls) -> tuple[int, int, int]:
        with cls.count_driver.session(database=cls.settings.database) as session:
            nodes = session.run("MATCH (n) RETURN count(n) AS n").single(strict=True)["n"]
            relationships = session.run(
                "MATCH ()-[r]->() RETURN count(r) AS n"
            ).single(strict=True)["n"]
            evidence = session.run(
                "MATCH (e:Evidence) RETURN count(e) AS n"
            ).single(strict=True)["n"]
        return nodes, relationships, evidence

    @staticmethod
    def _events(text: str) -> list[dict]:
        return [
            json.loads(line[6:])
            for line in text.splitlines()
            if line.startswith("data: ")
        ]

    def test_six_questions_through_starlette_sse(self) -> None:
        self.assertEqual(self.before, (1518, 3260, 511))
        cases = {
            "general": ("2026학년도 교양 최소 이수학점은?", ("34", "학점")),
            "balanced": (
                "2026학년도 균형교양 이수요건은?",
                ("4", "영역", "1", "과목", "12", "학점"),
            ),
            "transfer": (
                "2026학년도 편입생도 교양을 이수해야 하나?",
                ("의무가 없다",),
            ),
            "offering": (
                "2026학년도 컴퓨터공학과 자료구조는 몇 학년 몇 학기에 개설되나?",
                ("2학년", "1학기"),
            ),
            "required": (
                "2026학년도 컴퓨터공학과 전공필수 과목은?",
                ("9", "21", "0학점"),
            ),
            "completion": (
                "2026학년도 컴퓨터공학과 자료구조의 이수구분은?",
                ("전공선택",),
            ),
        }
        for name, (question, expected_tokens) in cases.items():
            started = perf_counter()
            response = self.client.post("/api/ask", json={"question": question})
            elapsed = perf_counter() - started
            self.assertEqual(response.status_code, 200)
            events = self._events(response.text)
            result = next(item for item in events if item["type"] == "result")
            wire = result["response"]
            print(
                {
                    "case": name,
                    "status": wire["status"],
                    "answer": wire["answer_text"],
                    "citations": len(wire["citations"]),
                    "elapsed_seconds": round(elapsed, 3),
                }
            )
            with self.subTest(case=name):
                self.assertEqual(wire["status"], "ANSWERABLE")
                self.assertTrue(wire["citations"])
                self.assertTrue(
                    all(
                        citation["excerpt_page"] > 0
                        and citation["source_pdf_page"] > 0
                        and citation["printed_page"] > 0
                        and citation["source_text"]
                        for citation in wire["citations"]
                    )
                )
                for token in expected_tokens:
                    self.assertIn(token, wire["answer_text"])
                serialized = json.dumps(events, ensure_ascii=False)
                self.assertNotIn("MATCH (", serialized)
                self.assertNotIn("system_prompt", serialized)
        self.assertEqual(self._counts(), self.before)


if __name__ == "__main__":
    unittest.main()
