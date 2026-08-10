"""Fail-closed validation for model-written, Evidence-grounded answer drafts."""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

from .contracts import AnswerDraft


class AnswerValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


PAGE_FIELDS = frozenset({"excerpt_page", "source_pdf_page", "printed_page"})
NON_FACT_FIELDS = frozenset(
    {
        "fact_id",
        "fact_label",
        "fact_status",
        "evidence_id",
        "evidence_verification_status",
        *PAGE_FIELDS,
    }
)
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])")
HANGUL_RE = re.compile(r"[가-힣]")
HANGUL_TOKEN_RE = re.compile(r"[가-힣]+")
PAGE_REFERENCE_RE = re.compile(r"\d+\s*(?:쪽|페이지)")
FORBIDDEN_DISCLOSURE_RE = re.compile(
    r"(?:MATCH\s*\(|RETURN\s+|system\s*prompt|api[_ -]?key|환경\s*변수)",
    re.IGNORECASE,
)

# Grammar and domain-category words are not curriculum facts. Concrete names and
# values must still come from the selected result rows.
FUNCTIONAL_KOREAN = frozenset(
    {
        "각",
        "각각",
        "가",
        "개",
        "개설",
        "개수",
        "경우",
        "과목",
        "교과목",
        "교양",
        "균형교양",
        "구분",
        "그리고",
        "과",
        "같습니다",
        "된다",
        "됩니다",
        "한다",
        "하며",
        "합니다",
        "해야",
        "있다",
        "없다",
        "면제",
        "목록",
        "모두",
        "영역",
        "영역별",
        "요건",
        "구성",
        "의무",
        "이상",
        "이",
        "이수",
        "이수구분",
        "이수학점",
        "전공선택",
        "전공필수",
        "최소",
        "총",
        "총합",
        "중",
        "다음",
        "다음과",
        "필수",
        "포함",
        "편입생",
        "학기",
        "학년",
        "학년도",
        "학점",
        "합계",
        "해당",
        "는",
        "도",
        "로",
        "를",
        "에",
        "와",
        "은",
        "을",
        "의",
    }
)
KOREAN_SUFFIXES = tuple(
    sorted(
        {
            "이어야합니다",
            "해야합니다",
            "않아도됩니다",
            "이상입니다",
            "학점입니다",
            "과목입니다",
            "개설됩니다",
            "포함됩니다",
            "면제됩니다",
            "필요합니다",
            "입니다",
            "됩니다",
            "합니다",
            "된다",
            "한다",
            "하다",
            "있다",
            "없다",
            "되며",
            "되어",
            "하고",
            "하며",
            "할",
            "들",
            "이어야",
            "이며",
            "이고",
            "에서",
            "으로",
            "에는",
            "에게",
            "까지",
            "부터",
            "처럼",
            "보다",
            "마다",
            "라도",
            "해야",
            "않아도",
            "과목",
            "학점",
            "학년",
            "학기",
            "개",
            "은",
            "는",
            "이",
            "가",
            "을",
            "를",
            "와",
            "의",
            "에",
            "도",
            "로",
        },
        key=len,
        reverse=True,
    )
)
ENUM_KOREAN = {
    "FIRST": ("학기",),
    "SECOND": ("학기",),
    "MAJOR_REQUIRED": ("전공필수",),
    "MAJOR_ELECTIVE": ("전공선택",),
    "GENERAL_REQUIRED": ("교양필수",),
    "GENERAL_ELECTIVE": ("교양선택",),
    "CREDIT": ("학점",),
    "COURSE_PER_AREA": ("과목", "영역", "영역별"),
}
SEMESTER_NUMBERS = {"FIRST": "1", "SECOND": "2"}


class AnswerValidator:
    def __init__(self, *, max_answer_chars: int = 2_000, max_citations: int = 20):
        if max_answer_chars < 1 or max_citations < 1:
            raise ValueError("answer and Citation limits must be positive")
        self.max_answer_chars = max_answer_chars
        self.max_citations = max_citations

    def validate(
        self,
        draft: AnswerDraft,
        rows: Sequence[Mapping[str, Any]],
        *,
        question: str | None = None,
    ) -> AnswerDraft:
        text = draft.answer_text.strip()
        if not text:
            self._fail("ANSWER_EMPTY", "answer_text is empty")
        if len(text) > self.max_answer_chars:
            self._fail("ANSWER_TOO_LARGE", "answer_text exceeds the configured limit")
        if not HANGUL_RE.search(text):
            self._fail("ANSWER_NOT_KOREAN", "answer_text must contain Korean")
        if PAGE_REFERENCE_RE.search(text):
            self._fail(
                "ANSWER_PAGE_REFERENCE_FORBIDDEN",
                "page references are assembled from Evidence by Python",
            )
        if FORBIDDEN_DISCLOSURE_RE.search(text):
            self._fail("ANSWER_INTERNAL_DISCLOSURE", "answer exposes an internal detail")

        fact_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        evidence_rows: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        direct_pairs: set[tuple[str, str]] = set()
        for row in rows:
            fact_id = row.get("fact_id")
            evidence_id = row.get("evidence_id")
            if isinstance(fact_id, str):
                fact_rows[fact_id].append(row)
            if isinstance(evidence_id, str):
                evidence_rows[evidence_id].append(row)
            if isinstance(fact_id, str) and isinstance(evidence_id, str):
                direct_pairs.add((fact_id, evidence_id))

        unknown_facts = set(draft.used_fact_ids) - set(fact_rows)
        if unknown_facts:
            self._fail("ANSWER_UNKNOWN_FACT", "answer selected an unknown Fact ID")
        if set(draft.used_fact_ids) != set(fact_rows):
            self._fail(
                "ANSWER_FACT_COVERAGE_INCOMPLETE",
                "answer must cover every Fact returned for the scoped question",
            )
        unknown_evidence = set(draft.used_evidence_ids) - set(evidence_rows)
        if unknown_evidence:
            self._fail("ANSWER_UNKNOWN_EVIDENCE", "answer selected an unknown Evidence ID")
        if len(draft.used_evidence_ids) > self.max_citations:
            self._fail("ANSWER_TOO_MANY_CITATIONS", "answer selected too many citations")

        selected_facts = set(draft.used_fact_ids)
        selected_evidence = set(draft.used_evidence_ids)
        selected_pairs = direct_pairs.intersection(
            {(fact_id, evidence_id) for fact_id in selected_facts for evidence_id in selected_evidence}
        )
        for fact_id in selected_facts:
            if any(row.get("fact_status") != "VERIFIED" for row in fact_rows[fact_id]):
                self._fail("ANSWER_FACT_NOT_VERIFIED", "answer selected a non-VERIFIED Fact")
            if not any(pair[0] == fact_id for pair in selected_pairs):
                self._fail(
                    "ANSWER_FACT_EVIDENCE_MISMATCH",
                    "a selected Fact lacks directly connected selected Evidence",
                )
        for evidence_id in selected_evidence:
            if any(
                row.get("evidence_verification_status") != "VERIFIED"
                for row in evidence_rows[evidence_id]
            ):
                self._fail(
                    "ANSWER_EVIDENCE_NOT_VERIFIED",
                    "answer selected non-VERIFIED Evidence",
                )
            if not any(pair[1] == evidence_id for pair in selected_pairs):
                self._fail(
                    "ANSWER_FACT_EVIDENCE_MISMATCH",
                    "selected Evidence is unrelated to the selected Facts",
                )

        selected_rows = [
            row
            for row in rows
            if (row.get("fact_id"), row.get("evidence_id")) in selected_pairs
        ]
        named_facts = {
            str(row["fact_id"]): row.get("name_ko")
            for row in selected_rows
            if isinstance(row.get("name_ko"), str) and row["name_ko"].strip()
        }
        if named_facts and any(name not in text for name in named_facts.values()):
            self._fail(
                "ANSWER_ENTITY_COVERAGE_INCOMPLETE",
                "answer omitted a named Fact returned for the scoped question",
            )
        self._validate_numbers(text, selected_rows)
        self._validate_korean_terms(text, selected_rows, question=question)
        return AnswerDraft(text, draft.used_fact_ids, draft.used_evidence_ids)

    def _validate_numbers(self, text: str, rows: Sequence[Mapping[str, Any]]) -> None:
        claimed = {self._normalize_number(value) for value in NUMBER_RE.findall(text)}
        allowed: set[str] = set()
        unique_facts: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            unique_facts.setdefault(str(row["fact_id"]), row)
            for key, value in row.items():
                if key in NON_FACT_FIELDS:
                    continue
                allowed.update(self._numbers_in_value(value))
                if isinstance(value, str) and value in SEMESTER_NUMBERS:
                    allowed.add(SEMESTER_NUMBERS[value])
        if unique_facts:
            allowed.add(str(len(unique_facts)))
        numeric_fields = {
            key
            for row in unique_facts.values()
            for key, value in row.items()
            if key not in NON_FACT_FIELDS
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        }
        for key in numeric_fields:
            values = [row.get(key) for row in unique_facts.values()]
            if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
                allowed.add(self._normalize_number(sum(values)))
        unsupported = claimed - allowed
        if unsupported:
            self._fail(
                "ANSWER_UNSUPPORTED_NUMBER",
                "answer contains a number absent from the selected Facts and Evidence",
            )

    def _validate_korean_terms(
        self,
        text: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        question: str | None,
    ) -> None:
        grounded: set[str] = set(FUNCTIONAL_KOREAN)
        for row in rows:
            for key, value in row.items():
                if key in PAGE_FIELDS or key.endswith("_id"):
                    continue
                for scalar in self._scalars(value):
                    if isinstance(scalar, str):
                        grounded.update(self._normalized_hangul_tokens(scalar))
                        grounded.update(ENUM_KOREAN.get(scalar, ()))
        if question and any(row.get("department_id") for row in rows):
            # A validated department_id proves department scope, while the dynamic
            # query currently returns only that stable ID. Permit only a Korean
            # department phrase explicitly present in the question; do not admit
            # arbitrary question terms such as injected course names or claims.
            for token in HANGUL_TOKEN_RE.findall(question):
                if token.endswith("학과") and len(token) > 2:
                    grounded.add(token)
                    grounded.add(token[:-1])
        unsupported = {
            token
            for token in self._normalized_hangul_tokens(text)
            if token and token not in grounded
        }
        if unsupported:
            self._fail(
                "ANSWER_UNSUPPORTED_ENTITY",
                "answer contains Korean terms absent from the selected Facts and Evidence",
            )

    @classmethod
    def _normalized_hangul_tokens(cls, text: str) -> set[str]:
        return {cls._strip_suffix(token) for token in HANGUL_TOKEN_RE.findall(text)}

    @staticmethod
    def _strip_suffix(token: str) -> str:
        current = token
        for _ in range(3):
            for suffix in KOREAN_SUFFIXES:
                if current.endswith(suffix) and len(current) > len(suffix):
                    current = current[: -len(suffix)]
                    break
            else:
                return current
        return current

    @classmethod
    def _numbers_in_value(cls, value: Any) -> set[str]:
        found: set[str] = set()
        for scalar in cls._scalars(value):
            if isinstance(scalar, bool):
                continue
            if isinstance(scalar, (int, float)):
                found.add(cls._normalize_number(scalar))
            elif isinstance(scalar, str):
                found.update(cls._normalize_number(item) for item in NUMBER_RE.findall(scalar))
        return found

    @staticmethod
    def _scalars(value: Any) -> Iterable[Any]:
        if isinstance(value, Mapping):
            for nested in value.values():
                yield from AnswerValidator._scalars(nested)
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                yield from AnswerValidator._scalars(nested)
        else:
            yield value

    @staticmethod
    def _normalize_number(value: Any) -> str:
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return str(value)
        if number == number.to_integral():
            return str(number.quantize(Decimal("1")))
        return format(number.normalize(), "f")

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise AnswerValidationError(code, message)
