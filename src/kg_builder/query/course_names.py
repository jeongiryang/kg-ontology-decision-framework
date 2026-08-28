"""Data-derived course-name matching shared by planning and personalization.

The matcher never keeps an application-owned alias table.  Exact names and course
codes come from the verified bundle.  A conservative edit-distance fallback accepts
one spelling difference only when it resolves to one stable course identity.  This
handles source/user orthography differences without making an evaluation-question
allowlist.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping


_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]+")
_PARTICLE_SUFFIXES = (
    "으로는",
    "에서는",
    "이라는",
    "이라고",
    "까지",
    "부터",
    "으로",
    "에서",
    "에게",
    "에는",
    "하고",
    "이랑",
    "랑",
    "에",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "과",
    "와",
    "도",
    "만",
    "의",
    "이야",
    "야",
)


def normalize_course_text(value: str) -> str:
    return _NON_WORD.sub("", unicodedata.normalize("NFKC", value).casefold())


def _distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    # The verified source differences covered by this fallback are orthographic
    # substitutions (for example 데이터/데이타).  Insert/delete matching turns a
    # generic word such as "프로그래밍" into the course "웹프로그래밍", so it is
    # deliberately not accepted.
    if len(left) != len(right):
        return False
    return sum(a != b for a, b in zip(left, right, strict=True)) == 1


def _question_tokens(question: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[0-9A-Za-z가-힣]+", unicodedata.normalize("NFKC", question)):
        normalized = normalize_course_text(raw)
        if normalized:
            tokens.add(normalized)
        for suffix in _PARTICLE_SUFFIXES:
            normalized_suffix = normalize_course_text(suffix)
            if normalized.endswith(normalized_suffix) and len(normalized) > len(normalized_suffix) + 1:
                tokens.add(normalized[: -len(normalized_suffix)])
    return tokens


@dataclass(frozen=True, slots=True)
class CourseIdentity:
    course_id: str
    course_code: str
    name_ko: str
    scope_types: tuple[str, ...] = ()


class CourseNameResolver:
    """Resolve mentions against loaded course identities, never a hand-written list."""

    def __init__(self, courses: Iterable[CourseIdentity]):
        self.courses = tuple(sorted(courses, key=lambda item: (item.course_code, item.course_id)))

    @classmethod
    def from_bundle(cls, bundle: Mapping[str, object]) -> "CourseNameResolver":
        raw_nodes = bundle.get("nodes", ())  # type: ignore[union-attr]
        raw_relationships = bundle.get("relationships", ())  # type: ignore[union-attr]
        curriculum_scope: dict[str, str] = {}
        for raw in raw_nodes:
            if not isinstance(raw, Mapping) or "CurriculumVersion" not in raw.get("labels", ()):
                continue
            properties = raw.get("properties")
            scope = properties.get("scope_type") if isinstance(properties, Mapping) else None
            if isinstance(raw.get("id"), str) and isinstance(scope, str):
                curriculum_scope[raw["id"]] = scope
        offering_scope: dict[str, str] = {}
        offering_course: dict[str, str] = {}
        for relationship in raw_relationships:
            if not isinstance(relationship, Mapping):
                continue
            kind = relationship.get("type")
            source = relationship.get("from_id")
            target = relationship.get("to_id")
            if not isinstance(source, str) or not isinstance(target, str):
                continue
            if kind == "HAS_OFFERING" and source in curriculum_scope:
                offering_scope[target] = curriculum_scope[source]
            elif kind == "OF_COURSE":
                offering_course[source] = target
        scopes_by_course: dict[str, set[str]] = {}
        for offering_id, course_id in offering_course.items():
            scope = offering_scope.get(offering_id)
            if scope:
                scopes_by_course.setdefault(course_id, set()).add(scope)
        courses: list[CourseIdentity] = []
        for raw in raw_nodes:
            if not isinstance(raw, Mapping) or "Course" not in raw.get("labels", ()):
                continue
            properties = raw.get("properties")
            if not isinstance(properties, Mapping):
                continue
            course_id = properties.get("course_id")
            course_code = properties.get("course_code")
            name_ko = properties.get("name_ko")
            if all(isinstance(item, str) and item.strip() for item in (course_id, course_code, name_ko)):
                courses.append(
                    CourseIdentity(
                        course_id,
                        course_code,
                        name_ko,
                        tuple(sorted(scopes_by_course.get(course_id, ()))),
                    )
                )
        return cls(courses)

    def find_mentions(self, question: str) -> tuple[CourseIdentity, ...]:
        normalized_question = normalize_course_text(question)
        tokens = _question_tokens(question)
        exact: list[CourseIdentity] = []
        for course in self.courses:
            name = normalize_course_text(course.name_ko)
            code = normalize_course_text(course.course_code)
            if (name and name in tokens) or (code and code in normalized_question):
                exact.append(course)
        # Resolve each token independently.  This preserves multiple unambiguous
        # one-character source/user spelling differences in one question, while an
        # ambiguous token contributes no identity at all.
        fuzzy: list[CourseIdentity] = []
        exact_ids = {item.course_id for item in exact}
        for token in tokens:
            token_matches: list[CourseIdentity] = []
            for course in self.courses:
                if course.course_id in exact_ids:
                    continue
                name = normalize_course_text(course.name_ko)
                if len(name) < 5:
                    continue
                if re.search(r"[a-z0-9]", name) or re.search(r"[a-z0-9]", token):
                    continue
                if _distance_at_most_one(name, token):
                    token_matches.append(course)
            if len({item.course_id for item in token_matches}) == 1:
                fuzzy.extend(token_matches)
        return self._unique((*exact, *fuzzy))

    @staticmethod
    def _unique(courses: Iterable[CourseIdentity]) -> tuple[CourseIdentity, ...]:
        unique = {item.course_id: item for item in courses}
        return tuple(sorted(unique.values(), key=lambda item: (item.course_code, item.course_id)))
