"""Deterministic query service over the verified Neo4j curriculum graph."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Protocol

from .cypher_queries import QUERY_SPECS, ensure_read_only
from .query_contracts import Answerability, EvidenceDTO, Intent, QueryRequest, QueryResponse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNRESOLVED_PATH = ROOT / "data/verified/2026/2026_curriculum_unresolved.json"
SUPPORTED_YEAR = 2026
DEPARTMENT_ALIASES = {
    "컴퓨터공학과": "department:cwnu:cse",
    "CSE": "department:cwnu:cse",
    "cse": "department:cwnu:cse",
    "department:cwnu:cse": "department:cwnu:cse",
}


class ReadExecutor(Protocol):
    def execute(self, cypher: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]: ...


class Neo4jReadExecutor:
    def __init__(self, driver: Any, database: str):
        self.driver = driver
        self.database = database

    def execute(self, cypher: str, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
        ensure_read_only(cypher)

        def run(tx: Any) -> list[dict[str, Any]]:
            return [record.data() for record in tx.run(cypher, dict(parameters))]

        with self.driver.session(database=self.database) as session:
            return session.execute_read(run)


class UnresolvedCatalog:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []

    @classmethod
    def load(cls, path: Path = DEFAULT_UNRESOLVED_PATH) -> "UnresolvedCatalog":
        source = json.loads(path.read_text(encoding="utf-8"))
        rows: list[dict[str, Any]] = []
        for key in ("unsupported_or_identity_conflict_general_rows", "omitted_cse_offering_rows"):
            for row in source.get(key, []):
                rows.append({"source_group": key, **row})
        return cls(rows)

    def course_matches(self, parameters: Mapping[str, Any]) -> list[dict[str, Any]]:
        code = parameters.get("course_code")
        name = parameters.get("course_name")
        return [
            row
            for row in self.rows
            if (code is not None and row.get("course_code") == code)
            or (name is not None and row.get("name_ko") == name)
        ]


class QueryService:
    def __init__(self, executor: ReadExecutor, unresolved: UnresolvedCatalog | None = None):
        self.executor = executor
        self.unresolved = unresolved or UnresolvedCatalog.load()

    def execute(self, request: QueryRequest) -> QueryResponse:
        scope_error = self._scope_error(request)
        if scope_error:
            return scope_error
        db_parameters = self._db_parameters(request)
        rows = self.executor.execute(QUERY_SPECS[request.intent].cypher, db_parameters)
        if request.intent in {
            Intent.GET_GENERAL_EDUCATION_MIN_CREDITS,
            Intent.GET_BALANCED_GENERAL_REQUIREMENT,
            Intent.GET_TRANSFER_GENERAL_EXEMPTION,
        }:
            return self._rule_response(request, rows)
        if request.intent is Intent.GET_MAJOR_REQUIRED_COURSES:
            return self._major_required_response(request, rows)
        return self._course_response(request, rows)

    def _scope_error(self, request: QueryRequest) -> QueryResponse | None:
        if request.parameters["academic_year"] != SUPPORTED_YEAR:
            return self._empty(
                request, Answerability.OUT_OF_SCOPE, "현재 Verified KG는 2026학년도만 지원합니다."
            )
        department = request.parameters.get("department")
        if department is not None and department not in DEPARTMENT_ALIASES:
            return self._empty(
                request,
                Answerability.OUT_OF_SCOPE,
                "현재 학과별 조회 범위는 컴퓨터공학과로 제한됩니다.",
            )
        return None

    def _db_parameters(self, request: QueryRequest) -> dict[str, Any]:
        parameters = {
            "academic_year": request.parameters["academic_year"],
            "department_id": DEPARTMENT_ALIASES.get(request.parameters.get("department", "")),
            "course_code": request.parameters.get("course_code"),
            "course_name": request.parameters.get("course_name"),
            "admission_type": request.parameters.get("admission_type"),
        }
        return parameters

    def _rule_response(
        self, request: QueryRequest, rows: list[dict[str, Any]]
    ) -> QueryResponse:
        if not rows:
            return self._empty(request, Answerability.NOT_FOUND, "VERIFIED 규칙을 찾지 못했습니다.")
        evidence = self._evidence(rows)
        if not evidence:
            return self._empty(
                request, Answerability.UNRESOLVED, "규칙에 VERIFIED Evidence가 없습니다."
            )
        rules: dict[str, dict[str, Any]] = {}
        for row in rows:
            rules[row["rule_id"]] = {
                key: row.get(key)
                for key in ("rule_id", "rule_type", "operator", "value", "unit", "description_ko")
                if row.get(key) is not None
            }
            if row.get("areas"):
                rules[row["rule_id"]]["areas"] = [area for area in row["areas"] if area["area_id"]]
        answer: dict[str, Any]
        if request.intent is Intent.GET_GENERAL_EDUCATION_MIN_CREDITS:
            answer = {"requirement": next(iter(rules.values()))}
        elif request.intent is Intent.GET_TRANSFER_GENERAL_EXEMPTION:
            answer = {"exempt": True, "rule": next(iter(rules.values()))}
        else:
            answer = {"requirements": list(rules.values())}
        warnings: list[str] = []
        if request.intent is Intent.GET_GENERAL_EDUCATION_MIN_CREDITS and any(
            key in request.parameters for key in ("department", "major_type", "admission_type")
        ):
            warnings.append(
                "선택 조건은 공통 기본 규칙의 범위를 바꾸지 않습니다. 편입생 면제는 별도 Intent로 조회합니다."
            )
        return QueryResponse(
            request.intent,
            Answerability.ANSWERABLE,
            answer,
            self._scope(request),
            evidence,
            tuple(warnings),
        )

    def _course_response(
        self, request: QueryRequest, rows: list[dict[str, Any]]
    ) -> QueryResponse:
        if not rows:
            unresolved = self.unresolved.course_matches(request.parameters)
            if unresolved:
                candidates = [
                    {
                        "course_code": row.get("course_code"),
                        "course_name": row.get("name_ko"),
                        "reason": row.get("note"),
                    }
                    for row in unresolved
                ]
                return QueryResponse(
                    request.intent,
                    Answerability.UNRESOLVED,
                    {"candidates": candidates},
                    self._scope(request),
                    warnings=("해당 과목 편성은 unresolved 데이터에 보류되어 있습니다.",),
                )
            return self._empty(request, Answerability.NOT_FOUND, "일치하는 VERIFIED 과목 편성이 없습니다.")

        courses: dict[str, dict[str, Any]] = {}
        for row in rows:
            courses[row["course_id"]] = {
                "course_id": row["course_id"],
                "course_code": row["course_code"],
                "course_name": row["course_name"],
            }
        if len(courses) > 1:
            return QueryResponse(
                request.intent,
                Answerability.CLARIFICATION_REQUIRED,
                {"candidates": list(courses.values())},
                self._scope(request),
                warnings=("동일한 과목명에 여러 학수번호가 대응합니다. 학수번호를 지정하세요.",),
            )
        evidence = self._evidence(rows)
        if not evidence:
            return self._empty(
                request, Answerability.UNRESOLVED, "편성에 VERIFIED Evidence가 없습니다."
            )
        course = next(iter(courses.values()))
        offerings: dict[str, dict[str, Any]] = {}
        for row in rows:
            offering = {"offering_id": row["offering_id"]}
            for key in (
                "grade_year",
                "semester",
                "credits",
                "lecture_hours",
                "practice_hours",
                "completion_type",
                "curriculum_id",
            ):
                if key in row:
                    offering[key] = row.get(key)
            offerings[row["offering_id"]] = offering
        key = "completion_types" if request.intent is Intent.GET_COURSE_COMPLETION_TYPE else "offerings"
        values = (
            sorted({item["completion_type"] for item in offerings.values()})
            if key == "completion_types"
            else list(offerings.values())
        )
        return QueryResponse(
            request.intent,
            Answerability.ANSWERABLE,
            {"course": course, key: values},
            self._scope(request),
            evidence,
        )

    def _major_required_response(
        self, request: QueryRequest, rows: list[dict[str, Any]]
    ) -> QueryResponse:
        if not rows:
            return self._empty(
                request, Answerability.NOT_FOUND, "VERIFIED 전공필수 편성을 찾지 못했습니다."
            )
        evidence = self._evidence(rows)
        if not evidence:
            return self._empty(
                request, Answerability.UNRESOLVED, "전공필수 편성에 VERIFIED Evidence가 없습니다."
            )
        offerings: dict[str, dict[str, Any]] = {}
        for row in rows:
            offerings[row["offering_id"]] = {
                key: row.get(key)
                for key in (
                    "offering_id",
                    "course_id",
                    "course_code",
                    "course_name",
                    "grade_year",
                    "semester",
                    "credits",
                    "completion_type",
                )
            }
        items = list(offerings.values())
        return QueryResponse(
            request.intent,
            Answerability.ANSWERABLE,
            {
                "courses": items,
                "course_count": len(items),
                "total_credits": sum(item["credits"] for item in items),
            },
            self._scope(request),
            evidence,
            (
                "major_type은 현재 편성 목록을 필터링하지 않으며 적용 범위 메타데이터로만 반환합니다.",
            )
            if "major_type" in request.parameters
            else (),
        )

    @staticmethod
    def _evidence(rows: list[dict[str, Any]]) -> tuple[EvidenceDTO, ...]:
        found: dict[str, EvidenceDTO] = {}
        for row in rows:
            item = EvidenceDTO.from_record(row)
            if item is not None:
                found[item.evidence_id] = item
        return tuple(found.values())

    @staticmethod
    def _scope(request: QueryRequest) -> dict[str, Any]:
        return dict(request.parameters)

    def _empty(
        self, request: QueryRequest, answerability: Answerability, warning: str
    ) -> QueryResponse:
        return QueryResponse(
            request.intent,
            answerability,
            {},
            self._scope(request),
            warnings=(warning,),
        )
