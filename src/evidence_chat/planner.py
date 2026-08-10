"""자연어 질문을 지원 Intent와 파라미터로 바꾸는 결정론적 플래너.

이 모듈은 임의 Cypher를 만들지 않는다. `kg_builder.query_contracts`가 정의한
6개 Intent와 허용 파라미터만 산출하고, 실제 검증은 `QueryRequest.from_dict`가 한다.

LLM 플래너는 `Planner` 프로토콜을 구현해 교체할 수 있다. 현재 기본 구현은
규칙 기반이며, 진행 화면에는 실제로 사용된 플래너 이름을 그대로 표시한다.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from kg_builder.query_contracts import Intent


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE_PATH = ROOT / "data/verified/2026/2026_curriculum_kg_data.json"
DEFAULT_ACADEMIC_YEAR = 2026
DEFAULT_DEPARTMENT = "컴퓨터공학과"

COURSE_CODE_PATTERN = re.compile(r"\b([A-Za-z]{2,4}\s?\d{4})\b")
YEAR_PATTERN = re.compile(r"\b(20\d{2})\s*(?:학년도|년도|년|학번)?")
MAX_QUESTION_LENGTH = 300


class PlannerError(ValueError):
    """질문에서 지원 Intent를 찾지 못했을 때 발생한다."""


@dataclass(frozen=True, slots=True)
class Plan:
    """플래너 산출물. `request_payload`는 그대로 계약 검증에 넘긴다."""

    intent: Intent
    parameters: dict[str, Any]
    planner_name: str
    normalized_question: str
    matched_signals: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def request_payload(self) -> dict[str, Any]:
        return {"intent": self.intent.value, "parameters": dict(self.parameters)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "parameters": dict(self.parameters),
            "planner": self.planner_name,
            "normalized_question": self.normalized_question,
            "matched_signals": list(self.matched_signals),
            "notes": list(self.notes),
        }


class Planner(Protocol):
    """LLM 플래너를 끼울 수 있는 인터페이스."""

    name: str

    def plan(self, question: str) -> Plan: ...


def normalize_question(question: str) -> str:
    """전각 문자와 중복 공백을 정리한다. 의미를 바꾸는 치환은 하지 않는다."""
    if not isinstance(question, str):
        raise PlannerError("질문은 문자열이어야 합니다.")
    text = unicodedata.normalize("NFKC", question).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        raise PlannerError("질문이 비어 있습니다.")
    if len(text) > MAX_QUESTION_LENGTH:
        raise PlannerError(f"질문이 너무 깁니다. {MAX_QUESTION_LENGTH}자 이내로 입력하세요.")
    return text


def load_course_lexicon(path: Path = DEFAULT_BUNDLE_PATH) -> dict[str, str]:
    """Verified bundle에서 과목명 → 학수번호 사전을 만든다.

    Neo4j에 임의 Cypher를 보내지 않기 위해 로컬 Verified 파일을 읽는다.
    반환 사전의 키는 공백을 제거한 과목명이다.
    """
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    lexicon: dict[str, str] = {}
    for node in bundle.get("nodes", []):
        if "Course" not in node.get("labels", []):
            continue
        properties = node.get("properties", {})
        name = properties.get("name_ko")
        code = properties.get("course_code")
        if isinstance(name, str) and isinstance(code, str):
            lexicon.setdefault(name.replace(" ", ""), code)
    return lexicon


@dataclass(slots=True)
class RuleBasedPlanner:
    """한국어 표층 신호로 Intent를 고르는 기본 플래너."""

    course_lexicon: dict[str, str] = field(default_factory=load_course_lexicon)
    name: str = "규칙 기반 플래너"

    def plan(self, question: str) -> Plan:
        text = normalize_question(question)
        compact = text.replace(" ", "")
        signals: list[str] = []
        notes: list[str] = []

        year = DEFAULT_ACADEMIC_YEAR
        year_match = YEAR_PATTERN.search(compact)
        if year_match:
            year = int(year_match.group(1))
            signals.append(f"학년도 표현 '{year_match.group(0)}'")
        else:
            notes.append(f"질문에 학년도가 없어 기본값 {DEFAULT_ACADEMIC_YEAR}학년도로 조회합니다.")

        course_code, course_name, course_signal = self._extract_course(text, compact)
        if course_signal:
            signals.append(course_signal)

        intent = self._match_intent(compact, bool(course_code or course_name), signals)
        parameters = self._build_parameters(intent, year, course_code, course_name, notes)
        return Plan(
            intent=intent,
            parameters=parameters,
            planner_name=self.name,
            normalized_question=text,
            matched_signals=tuple(signals),
            notes=tuple(notes),
        )

    def _extract_course(
        self, text: str, compact: str
    ) -> tuple[str | None, str | None, str | None]:
        code_match = COURSE_CODE_PATTERN.search(text)
        if code_match:
            code = code_match.group(1).replace(" ", "").upper()
            return code, None, f"학수번호 '{code}'"
        matches = [name for name in self.course_lexicon if name and name in compact]
        if matches:
            best = max(matches, key=len)
            return None, best, f"과목명 '{best}'"
        return None, None, None

    def _match_intent(self, compact: str, has_course: bool, signals: list[str]) -> Intent:
        def has(*needles: str) -> bool:
            return any(needle in compact for needle in needles)

        if has("균형교양"):
            signals.append("키워드 '균형교양'")
            return Intent.GET_BALANCED_GENERAL_REQUIREMENT

        if has("편입") and has("면제", "교양", "인정"):
            signals.append("키워드 '편입' + 면제/교양")
            return Intent.GET_TRANSFER_GENERAL_EXEMPTION

        if has("전공필수") and not has_course:
            signals.append("키워드 '전공필수' (과목 지정 없음)")
            return Intent.GET_MAJOR_REQUIRED_COURSES

        if has_course and has("이수구분", "전공필수인지", "전공선택인지", "전공선택", "전공필수", "분류"):
            signals.append("과목 지정 + 이수구분 표현")
            return Intent.GET_COURSE_COMPLETION_TYPE

        if has_course:
            signals.append("과목 지정 + 편성 조회")
            return Intent.GET_COURSE_OFFERING

        if has("교양") and has("학점", "이수요건", "최소", "졸업"):
            signals.append("키워드 '교양' + 학점/요건")
            return Intent.GET_GENERAL_EDUCATION_MIN_CREDITS

        raise PlannerError(
            "지원하는 6개 조회 유형에 해당하지 않습니다. "
            "교양 최소학점, 균형교양 요건, 편입생 교양 면제, 과목 편성, "
            "전공필수 목록, 과목 이수구분 중 하나로 질문해 주세요."
        )

    @staticmethod
    def _build_parameters(
        intent: Intent,
        year: int,
        course_code: str | None,
        course_name: str | None,
        notes: list[str],
    ) -> dict[str, Any]:
        parameters: dict[str, Any] = {"academic_year": year}
        if intent in {
            Intent.GET_COURSE_OFFERING,
            Intent.GET_COURSE_COMPLETION_TYPE,
            Intent.GET_MAJOR_REQUIRED_COURSES,
        }:
            parameters["department"] = DEFAULT_DEPARTMENT
            notes.append(f"학과는 현재 지원 범위인 {DEFAULT_DEPARTMENT}로 조회합니다.")
        if intent in {Intent.GET_COURSE_OFFERING, Intent.GET_COURSE_COMPLETION_TYPE}:
            if course_code:
                parameters["course_code"] = course_code
            elif course_name:
                parameters["course_name"] = course_name
        if intent is Intent.GET_TRANSFER_GENERAL_EXEMPTION:
            parameters["admission_type"] = "TRANSFER"
        return parameters


EXAMPLE_QUESTIONS: tuple[str, ...] = (
    "2026학번 교양은 최소 몇 학점을 들어야 해?",
    "균형교양 이수요건이 어떻게 돼?",
    "편입생인데 교양을 면제받을 수 있어?",
    "자료구조는 몇 학년 몇 학기에 열려?",
    "컴퓨터공학과 전공필수 과목을 알려줘",
    "자료구조의 이수구분은 뭐야?",
)
