"""Validated, request-local curriculum profile and five-state outcome contracts.

Profiles originate in browser localStorage and are sent with each request.  The server
validates them but never persists them or writes student nodes to the curriculum KG.
Chat statements are parsed into typed assertions whose provenance remains
``USER_ASSERTION``; they are never treated as VERIFIED curriculum facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from kg_builder.query.course_names import CourseNameResolver


PROFILE_VERSION = 1
MAX_PROFILE_COURSES = 200
MAX_NOTE_CHARS = 500


class OutcomeStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NEEDS_USER_INFO = "NEEDS_USER_INFO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    ADVISORY = "ADVISORY"


class ProfileValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CompletedCourse:
    course_code: str
    name_ko: str
    provenance: str = "USER_ASSERTION"

    def to_dict(self) -> dict[str, str]:
        return {
            "course_code": self.course_code,
            "name_ko": self.name_ko,
            "provenance": self.provenance,
        }


@dataclass(frozen=True, slots=True)
class EnglishCredential:
    test: str
    value: int | float | str
    provenance: str = "USER_ASSERTION"

    def to_dict(self) -> dict[str, Any]:
        return {"test": self.test, "value": self.value, "provenance": self.provenance}


@dataclass(frozen=True, slots=True)
class UserProfile:
    version: int = PROFILE_VERSION
    admission_year: int | None = None
    curriculum_year: int | None = None
    department_id: str | None = None
    current_grade_year: int | None = None
    current_semester: str | None = None
    admission_type: str | None = None
    major_type: str | None = None
    completed_courses: tuple[CompletedCourse, ...] = ()
    credits: tuple[tuple[str, float], ...] = ()
    english_credentials: tuple[EnglishCredential, ...] = ()
    career_goal: str | None = None
    note: str | None = None

    @property
    def credits_by_category(self) -> dict[str, float]:
        return dict(self.credits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "admission_year": self.admission_year,
            "curriculum_year": self.curriculum_year,
            "department_id": self.department_id,
            "current_grade_year": self.current_grade_year,
            "current_semester": self.current_semester,
            "admission_type": self.admission_type,
            "major_type": self.major_type,
            "completed_courses": [item.to_dict() for item in self.completed_courses],
            "credits": dict(self.credits),
            "english_credentials": [item.to_dict() for item in self.english_credentials],
            "career_goal": self.career_goal,
            "note": self.note,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> "UserProfile":
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise ProfileValidationError("profile must be an object")
        allowed = {
            "version",
            "admission_year",
            "curriculum_year",
            "department_id",
            "current_grade_year",
            "current_semester",
            "admission_type",
            "major_type",
            "completed_courses",
            "credits",
            "english_credentials",
            "career_goal",
            "note",
        }
        if set(payload) - allowed:
            raise ProfileValidationError("profile contains unsupported fields")
        version = payload.get("version", PROFILE_VERSION)
        if version != PROFILE_VERSION:
            # Browser migrations may discard old fields, but the server never guesses
            # the meaning of an unknown schema version.
            raise ProfileValidationError("profile version is not supported")

        def year(name: str) -> int | None:
            value = payload.get(name)
            if value is None:
                return None
            if isinstance(value, bool) or not isinstance(value, int) or not 1900 <= value <= 9999:
                raise ProfileValidationError(f"{name} must be a four-digit year")
            return value

        grade = payload.get("current_grade_year")
        if grade is not None and (
            isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 6
        ):
            raise ProfileValidationError("current_grade_year must be from 1 to 6")
        semester = payload.get("current_semester")
        if semester is not None and semester not in {"FIRST", "SECOND"}:
            raise ProfileValidationError("current_semester is invalid")
        department = _optional_text(payload.get("department_id"), "department_id", 80)
        admission_type = _optional_vocab(
            payload.get("admission_type"), "admission_type", {"NEW", "TRANSFER", "CHANGE_MAJOR"}
        )
        major_type = _optional_vocab(
            payload.get("major_type"),
            "major_type",
            {"SINGLE", "DOUBLE", "MINOR", "LINKED", "CONVERGENCE"},
        )
        courses_raw = payload.get("completed_courses", [])
        if not isinstance(courses_raw, list) or len(courses_raw) > MAX_PROFILE_COURSES:
            raise ProfileValidationError("completed_courses is invalid")
        courses: dict[str, CompletedCourse] = {}
        for item in courses_raw:
            if not isinstance(item, Mapping):
                raise ProfileValidationError("completed course must be an object")
            code = _required_text(item.get("course_code"), "course_code", 80)
            name = _required_text(item.get("name_ko"), "name_ko", 160)
            courses[code] = CompletedCourse(code, name)

        credit_raw = payload.get("credits", {})
        if not isinstance(credit_raw, Mapping):
            raise ProfileValidationError("credits must be an object")
        credits: list[tuple[str, float]] = []
        for key, value in credit_raw.items():
            if key not in {"total", "general", "major", "free_elective"}:
                raise ProfileValidationError("credit category is unsupported")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 300:
                raise ProfileValidationError("credit values must be from 0 to 300")
            credits.append((key, float(value)))

        credentials_raw = payload.get("english_credentials", [])
        if not isinstance(credentials_raw, list) or len(credentials_raw) > 20:
            raise ProfileValidationError("english_credentials is invalid")
        credentials: dict[str, EnglishCredential] = {}
        for item in credentials_raw:
            if not isinstance(item, Mapping):
                raise ProfileValidationError("English credential must be an object")
            test = _required_text(item.get("test"), "test", 80).upper()
            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ProfileValidationError("English credential value is invalid")
            if isinstance(value, (int, float)) and not 0 <= value <= 1000:
                raise ProfileValidationError("English credential score is out of range")
            if isinstance(value, str):
                value = _required_text(value, "credential value", 40).upper()
            credentials[test] = EnglishCredential(test, value)

        return cls(
            admission_year=year("admission_year"),
            curriculum_year=year("curriculum_year"),
            department_id=department,
            current_grade_year=grade,
            current_semester=semester,
            admission_type=admission_type,
            major_type=major_type,
            completed_courses=tuple(sorted(courses.values(), key=lambda item: item.course_code)),
            credits=tuple(sorted(credits)),
            english_credentials=tuple(sorted(credentials.values(), key=lambda item: item.test)),
            career_goal=_optional_text(payload.get("career_goal"), "career_goal", 160),
            note=_optional_text(payload.get("note"), "note", MAX_NOTE_CHARS),
        )


def _required_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ProfileValidationError(f"{name} is invalid")
    return value.strip()


def _optional_text(value: Any, name: str, limit: int) -> str | None:
    return None if value is None else _required_text(value, name, limit)


def _optional_vocab(value: Any, name: str, values: set[str]) -> str | None:
    if value is None:
        return None
    if value not in values:
        raise ProfileValidationError(f"{name} is invalid")
    return value


_CREDIT_CATEGORIES = {
    "총": "total",
    "전체": "total",
    "교양": "general",
    "전공": "major",
    "일반선택": "free_elective",
    "자유선택": "free_elective",
}
_TEST_PATTERNS = {
    "TOEIC": re.compile(
        r"(?:TOEIC(?!\s*SPEAKING)|토익(?!\s*스피킹))\s*"
        r"(?:은|이|점수는|점수)?\s*(\d{2,3})\s*점?",
        re.IGNORECASE,
    ),
    "TOEIC_SPEAKING": re.compile(r"(?:TOEIC\s*SPEAKING|토익\s*스피킹)\s*(?:LEVEL\s*)?(\d{2,3})", re.IGNORECASE),
    "OPIC": re.compile(r"(?:OPIC|오픽)\s*([A-Z]{1,3}\d?)", re.IGNORECASE),
}


@dataclass(frozen=True, slots=True)
class ProfileExtraction:
    profile: UserProfile
    changed_fields: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


class ProfileExtractor:
    def __init__(self, course_resolver: CourseNameResolver):
        self.course_resolver = course_resolver

    def extract(self, question: str, current: UserProfile) -> ProfileExtraction:
        data = current.to_dict()
        changed: set[str] = set()
        conflicts: set[str] = set()

        student = re.search(r"(?<!\d)((?:19|20)\d{2})\s*학번", question)
        if student:
            data["admission_year"] = int(student.group(1))
            data["curriculum_year"] = int(student.group(1))
            changed.update({"admission_year", "curriculum_year"})
        if re.search(r"(?:컴퓨터공학과|컴공)(?:야|과|\s*학생|입니다|이야)?", question):
            data["department_id"] = "CSE"
            changed.add("department_id")
        if "편입" in question:
            data["admission_type"] = "TRANSFER"
            changed.add("admission_type")
        if "전과" in question:
            data["admission_type"] = "CHANGE_MAJOR"
            changed.add("admission_type")
        grade = re.search(r"(?<!\d)([1-6])\s*학년", question)
        if grade:
            data["current_grade_year"] = int(grade.group(1))
            changed.add("current_grade_year")

        credits = dict(data.get("credits") or {})
        general_total = re.search(
            r"교양\s*총\s*학점(?:은|이)?\s*(\d+(?:\.\d+)?)\s*학점",
            question,
        )
        if general_total:
            credits["general"] = float(general_total.group(1))
            changed.add("credits.general")
        # Explicit correction wins over the earlier value in the same utterance.
        correction = re.search(
            r"(총|전체|교양|전공|일반선택|자유선택)\s*학점(?:은|이)?\s*"
            r"(\d+(?:\.\d+)?)\s*(?:가|이)?\s*아니라\s*(\d+(?:\.\d+)?)\s*학점",
            question,
        )
        corrected_span = correction.span() if correction else None
        if correction:
            category = _CREDIT_CATEGORIES[correction.group(1)]
            credits[category] = float(correction.group(3))
            changed.add(f"credits.{category}")
        observations: dict[str, set[float]] = {}
        for match in re.finditer(
            r"(?<![가-힣])(총|전체|교양|전공|일반선택|자유선택)\s*(?:을|은|이)?\s*"
            r"(\d+(?:\.\d+)?)\s*학점",
            question,
        ):
            if general_total and general_total.start() <= match.start() < general_total.end():
                continue
            if corrected_span and corrected_span[0] <= match.start() < corrected_span[1]:
                continue
            category = _CREDIT_CATEGORIES[match.group(1)]
            observations.setdefault(category, set()).add(float(match.group(2)))
        for category, values in observations.items():
            if len(values) > 1:
                conflicts.add(f"credits.{category}")
                continue
            credits[category] = next(iter(values))
            changed.add(f"credits.{category}")
        if "total" not in observations:
            total_match = re.search(
                r"(?:지금까지|현재까지)\s*(\d+(?:\.\d+)?)\s*학점(?:을|를)?\s*(?:이수|들)",
                question,
            )
            if total_match:
                credits["total"] = float(total_match.group(1))
                changed.add("credits.total")
        data["credits"] = credits

        course_map = {
            item["course_code"]: item
            for item in data.get("completed_courses", [])
            if isinstance(item, Mapping) and isinstance(item.get("course_code"), str)
        }
        if re.search(r"(?:들었|수강했|이수했|들은\s*과목|이수\s*과목)", question):
            for course in self.course_resolver.find_mentions(question):
                course_map[course.course_code] = {
                    "course_code": course.course_code,
                    "name_ko": course.name_ko,
                }
                changed.add("completed_courses")
        data["completed_courses"] = list(course_map.values())

        credential_map = {
            item["test"]: item
            for item in data.get("english_credentials", [])
            if isinstance(item, Mapping) and isinstance(item.get("test"), str)
        }
        for test, pattern in _TEST_PATTERNS.items():
            match = pattern.search(question)
            if match:
                raw = match.group(1).upper()
                value: int | str = int(raw) if raw.isdigit() else raw
                credential_map[test] = {"test": test, "value": value}
                changed.add("english_credentials")
        data["english_credentials"] = list(credential_map.values())

        goal = re.search(r"([가-힣A-Za-z·\s]{2,24})(?:가|이)?\s*되고\s*싶", question)
        if goal:
            data["career_goal"] = goal.group(1).strip()
            changed.add("career_goal")
        return ProfileExtraction(
            UserProfile.from_payload(data), tuple(sorted(changed)), tuple(sorted(conflicts))
        )


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    status: OutcomeStatus
    message: str
    required_user_fields: tuple[str, ...] = ()
    used_profile_fields: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "message": self.message,
            "required_user_fields": list(self.required_user_fields),
            "used_profile_fields": list(self.used_profile_fields),
            "limitations": list(self.limitations),
        }
