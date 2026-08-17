"""Build the choices a user can pick to complete a query scope.

되묻기를 자유 입력으로 받으면 사용자의 답("컴공요")을 다시 해석해야 하고, 그러면 계획
모델의 추측이 되돌아온다. 그래서 **서버가 데이터에서 만든 선택지**만 제시하고, 사용자가
고른 값만 계획에 넣는다. 이렇게 하면 세 가지가 함께 지켜진다.

- 사용자가 적재되지 않은 값을 고를 수 없다. 선택지가 적재된 값뿐이다.
- 고른 값을 다시 해석할 필요가 없다. LLM 재호출이 없으므로 오해할 자리가 없다.
- 사용자가 고른 값은 계획을 정당화하는 가장 신뢰도 높은 출처가 된다.

값(``value``)도 표기(``label``)도 코드에 적지 않는다. 값은 적재 bundle 에서, 사람이 읽는
표기는 온톨로지 명세의 ``name_ko``·``description_ko`` 에서 읽는다. 어휘가 바뀌면 명세를
고치는 것으로 화면도 함께 따라온다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .fact_families import EXTENDED_FAMILIES, SelectionMode, allowed_fields_for_mode
from .fact_index import (
    BASE_LABEL_MODES,
    FactIndex,
    leading_candidates,
    vocabulary_labels,
)

# 영역 id 는 `area:general:balanced:humanities-arts` 처럼 조상을 접두어로 담는다.
# 상위 영역을 별도 표로 적지 않고 id 에서 그대로 읽는다.
_AREA_SEPARATOR = ":"
# 규칙 라벨의 조각을 잇는 구분자.
_LABEL_JOIN = " · "
# 적용 대상을 사람이 읽는 말로 옮길 때 보는 속성. 앞의 것부터 붙인다.
_SCOPE_LABEL_PROPERTIES = (
    "student_type",
    "college_category",
    "major_type",
    "admission_type",
)


# 속성 이름으로 인정할 최소 글자 수. 한 글자는 우연히 겹친다.
_MIN_ASPECT_TERM = 2


# 선택지 수 상한을 적용하지 않는 부족 코드. 상한은 검색 꼬리가 섞여 드는 것을 막으려고
# 둔 것인데, 이 코드들의 후보는 검색이 아니라 **스키마가 정한 닫힌 집합**이라 꼬리가 없다.
# 잘라 내면 오히려 물을 수 있는 것이 화면에서 사라진다(`이수구분` 이 그랬다).
_CLOSED_SET_CODES = frozenset({"COURSE_ASPECT"})


def _capped(code: str, choices: tuple[Any, ...]) -> tuple[Any, ...]:
    """Trim search-derived choices; leave closed schema-derived sets whole."""

    return choices if code in _CLOSED_SET_CODES else choices[:MAX_OPTIONS]


def _area_ancestors(area_id: str) -> tuple[str, ...]:
    """The area itself and every area that contains it."""

    parts = area_id.split(_AREA_SEPARATOR)
    return tuple(
        _AREA_SEPARATOR.join(parts[: index + 1]) for index in range(1, len(parts))
    )

# 한 번에 보여 줄 선택지 수. 더 늘리면 고르기 어려워지고, 검색 꼬리가 섞여 들어온다.
MAX_OPTIONS = 5
# 되묻기 횟수 상한. 이만큼 채우고도 계획이 서지 않으면 더 묻지 않고 끝낸다.
MAX_ROUNDS = 3
# 선택지를 만들 수 있는 부족 코드. 계획 계층의 MissingScope 와 같은 값이며, 여기서
# 다시 나열하는 이유는 값 검증이 모든 코드를 훑어야 하기 때문이다.
# 사용자가 고른 값이 어떤 조회를 뜻하는지. 과목을 골랐다면 과목 조회이고, 이수요건을
# 골랐다면 규칙 조회다. 계획 모델이 다른 모드를 고집해도 사용자의 선택이 이긴다.
# 선택지를 만든 쪽이 그 뜻을 함께 선언하므로 값과 모드가 어긋나지 않는다.
FILTER_IMPLIED_MODE: Mapping[str, SelectionMode] = {
    "course_code": SelectionMode.SINGLE_COURSE,
    "name_ko": SelectionMode.SINGLE_COURSE,
    "rule_ids": SelectionMode.SINGLE_RULE,
}
MISSING_CODES = (
    "DEPARTMENT",
    "ACADEMIC_YEAR",
    "COURSE_IDENTITY",
    "COURSE_ASPECT",
    "RULE_TOPIC",
    "QUESTION_INTENT",
)
# 되묻기가 채우는 것이 필터가 아니라 **요청 필드**인 경우. 어느 과목인지가 아니라 그
# 과목의 무엇을 묻는지를 고르게 한다. 계획에서도 필터가 아니라 requested_fields 로 간다.
REQUESTED_FIELDS = "requested_fields"
# 과목 조회에서 되물을 수 있는 속성. 과목을 가리키는 식별자는 답이 아니라 주어이므로 뺀다.
_COURSE_IDENTITY_FIELDS = frozenset({"name_ko", "course_code"})
# 무엇을 되물을지 정하지 못했을 때, 질문이 어떤 사실을 가리키는지에 따라 되묻는
# 종류를 고른다. 고정 순서로 훑으면 안 된다. 학과는 언제나 후보가 있어 먼저 걸리고,
# 과목 후보는 이수요건 질문에도 약하게 걸려 더 맞는 선택지를 가려 버린다.
# 기존 두 family 의 모드가 다루는 라벨. 확장 family 는 선언에서 읽으므로 여기 없다.
BASE_MODE_LABELS: Mapping[SelectionMode, frozenset[str]] = {
    mode: frozenset(
        label for label, modes in BASE_LABEL_MODES.items() if mode in modes
    )
    for modes in BASE_LABEL_MODES.values()
    for mode in modes
}
MODE_FALLBACK_CODES: Mapping[SelectionMode, str] = {
    SelectionMode.SINGLE_RULE: "RULE_TOPIC",
    SelectionMode.MULTIPLE_RULES: "RULE_TOPIC",
    SelectionMode.SINGLE_COURSE: "COURSE_IDENTITY",
    SelectionMode.COURSE_LIST: "COURSE_IDENTITY",
}
# 어떤 필터가 채워지면 그 부족 코드가 해소되는지. 이미 채워진 것을 다시 물으면
# 왕복이 끝나지 않으므로, 되묻기 전에 이 표로 확인한다.
SCOPE_FILTERS: Mapping[str, tuple[str, ...]] = {
    "DEPARTMENT": ("department_id",),
    "ACADEMIC_YEAR": ("academic_year",),
    "COURSE_IDENTITY": ("course_code", "name_ko"),
    "COURSE_ASPECT": (REQUESTED_FIELDS,),
    "RULE_TOPIC": ("rule_ids", "rule_id"),
    "QUESTION_INTENT": ("selection_mode",),
}
# 규칙 원문을 선택지로 보여 줄 때의 길이 한계. 어느 요건인지 알아볼 정도만 남긴다.
RULE_LABEL_MAX_CHARS = 60


@dataclass(frozen=True, slots=True)
class Choice:
    """One data-derived value the user may pick for a missing scope."""

    filter_name: str
    value: Any
    label: str
    detail: str | None = None


def _truncate(text: str, limit: int = RULE_LABEL_MAX_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


class ClarificationChoices:
    """Turn a missing-scope code into concrete choices taken from the loaded data."""

    def __init__(
        self,
        bundle: Mapping[str, Any],
        fact_index: FactIndex,
        spec: Mapping[str, Any] | None = None,
    ):
        self._index = fact_index
        nodes = bundle.get("nodes", ())
        self._departments = tuple(
            Choice("department_id", node["properties"]["department_id"], name)
            for node in nodes
            if "Department" in node["labels"]
            and isinstance(node["properties"].get("department_id"), str)
            and isinstance(name := node["properties"].get("name_ko"), str)
        )
        years = sorted(
            {
                node["properties"]["academic_year"]
                for node in nodes
                if "CurriculumVersion" in node["labels"]
                and isinstance(node["properties"].get("academic_year"), int)
            }
        )
        self._years = tuple(
            Choice("academic_year", year, f"{year}학년도") for year in years
        )
        self._mode_labels = _mode_labels(spec or {})
        # 영역 이름과 id. 코드에 영역 이름을 적지 않고 적재된 EducationArea 에서 읽는다.
        #
        # 전공 영역은 `컴퓨터공학 전공필수` 처럼 학과 이름을 앞에 달고 있다. 사용자는
        # `전공필수 이수요건은?` 이라고 학과를 빼고 묻는 일이 잦으므로, 공백으로 나뉜
        # 마지막 조각도 함께 대조한다. 교양 영역은 공백이 없어 종전과 같이 동작한다.
        area_names: list[tuple[str, str]] = []
        for node in nodes:
            if "EducationArea" not in node["labels"]:
                continue
            name = node["properties"].get("name_ko")
            area_id = node["properties"].get("area_id")
            if not isinstance(name, str) or not name or not isinstance(area_id, str):
                continue
            area_names.append((name, area_id))
            tail = name.split()[-1] if len(name.split()) > 1 else ""
            if tail:
                area_names.append((tail, area_id))
        self._area_names = tuple(area_names)
        self._area_id_names: Mapping[str, str] = {
            node["properties"]["area_id"]: node["properties"]["name_ko"]
            for node in nodes
            if "EducationArea" in node["labels"]
            and isinstance(node["properties"].get("area_id"), str)
            and isinstance(node["properties"].get("name_ko"), str)
        }
        # 어느 규칙이 어느 영역을 향하는지. 검색 색인은 속성만 담고 관계를 버리므로
        # 여기서 그래프를 그대로 읽는다.
        area_ids = {area_id for _, area_id in self._area_names}
        rule_areas: dict[str, set[str]] = {}
        for relationship in bundle.get("relationships", ()):
            if relationship.get("type") != "TARGETS":
                continue
            target = relationship.get("to_id")
            if target in area_ids:
                rule_areas.setdefault(relationship["from_id"], set()).add(target)
        self._rule_areas: Mapping[str, frozenset[str]] = {
            rule_id: frozenset(areas) for rule_id, areas in rule_areas.items()
        }
        # 과목의 어떤 속성을 물을 수 있는지. 모드가 돌려줄 수 있는 필드와 명세가 선언한
        # 한국어 표기를 맞춰 만든다. `학점. 0과 빈 값은 구분` 처럼 설명이 이어지는 표기는
        # 첫 마디만 쓴다. 라벨이지 설명이 아니기 때문이다.
        returnable = allowed_fields_for_mode(SelectionMode.SINGLE_COURSE.value) or ()
        self._aspect_labels: dict[str, str] = {}
        for label_spec in (spec or {}).get("node_labels", ()):
            if label_spec.get("name") != "CourseOffering":
                continue
            for prop in label_spec.get("properties", ()):
                name = prop.get("name")
                described = prop.get("description_ko")
                if (
                    name in returnable
                    and name not in _COURSE_IDENTITY_FIELDS
                    and isinstance(described, str)
                    and described
                ):
                    self._aspect_labels[name] = described.split(".")[0].strip()
        # 순서는 명세가 선언한 차례를 그대로 따른다. 값의 가짓수로 정렬해 봤으나
        # `실기·실습 시수` 가 위로 올라오고 `이수구분` 이 밀려, 지표가 유용함을 대신하지
        # 못했다(2026-08-15 실측). 임의로 순위를 매기느니 명세 순서를 쓴다.
        # 질문이 이미 속성을 말했는지 볼 때 쓰는 낱말. 라벨을 낱말로 쪼개되 **여러 라벨이
        # 함께 쓰는 낱말은 뺀다**(`개설`, `시수`). 그런 낱말은 어느 속성을 가리키는지
        # 정하지 못한다. 불용어 목록을 코드에 적지 않고 라벨끼리 대조해 얻는다.
        shared: dict[str, int] = {}
        for label in self._aspect_labels.values():
            for word in set(label.split()):
                shared[word] = shared.get(word, 0) + 1
        self._aspect_terms: Mapping[str, frozenset[str]] = {
            name: frozenset(
                word
                for word in label.split()
                if shared.get(word) == 1 and len(word) >= _MIN_ASPECT_TERM
            )
            for name, label in self._aspect_labels.items()
        }
        # 규칙 선택지 라벨의 재료. 셋 다 선언된 표기이며 새로 요약하지 않는다.
        #   적용 대상 ← ApplicabilityScope 의 사람이 읽는 속성 + 통제어휘 표기
        #   영역      ← EducationArea.name_ko
        #   기준 종류 ← rule_type 통제어휘의 description_ko
        value_labels = vocabulary_labels(spec or {})
        self._rule_type_labels = {
            entry["value"]: entry["description_ko"]
            for entry in (
                ((spec or {}).get("controlled_vocabularies", {}).get("rule_type") or {})
                .get("values", ())
            )
            if isinstance(entry, Mapping)
            and isinstance(entry.get("value"), str)
            and isinstance(entry.get("description_ko"), str)
        }
        # 적용 대상은 속성별로 나눠 둔다. 한 규칙이 여러 대상에 걸릴 때 통째로 이어
        # 붙이면 `컴퓨터공학과 적용 학생 단일전공, 컴퓨터공학과 적용 학생 부전공` 처럼
        # 같은 말이 반복된다. 속성별로 모아 합치면 `컴퓨터공학과 적용 학생 단일전공, 부전공`
        # 이 된다.
        self._scope_facets: dict[str, dict[str, str]] = {}
        for node in nodes:
            if "ApplicabilityScope" not in node["labels"]:
                continue
            properties = node["properties"]
            scope_id = properties.get("scope_id")
            if not isinstance(scope_id, str):
                continue
            facets: dict[str, str] = {}
            for name in _SCOPE_LABEL_PROPERTIES:
                value = properties.get(name)
                if not isinstance(value, str) or not value:
                    continue
                # 통제어휘 값(`SINGLE_MAJOR`)은 명세의 표기로 옮기고, 원문 그대로인
                # 값(`야간학과`)은 그대로 쓴다.
                facets[name] = value_labels.get((name, value), value)
            if facets:
                self._scope_facets[scope_id] = facets
        # 규칙의 기준 종류. 검색 색인은 이 값을 식별자로 싣지 않으므로 그래프에서 읽는다.
        self._rule_kinds: Mapping[str, str] = {
            node["id"]: node["properties"]["rule_type"]
            for node in nodes
            if "Rule" in node["labels"]
            and isinstance(node["properties"].get("rule_type"), str)
        }
        rule_scopes: dict[str, set[str]] = {}
        for relationship in bundle.get("relationships", ()):
            if relationship.get("type") != "APPLIES_TO":
                continue
            if relationship.get("to_id") in self._scope_facets:
                rule_scopes.setdefault(relationship["from_id"], set()).add(
                    relationship["to_id"]
                )
        self._rule_scopes: Mapping[str, frozenset[str]] = {
            rule_id: frozenset(scope_ids) for rule_id, scope_ids in rule_scopes.items()
        }
        # 과목명과 학수번호. 질문이 과목명을 그대로 말했는지 대조하는 데 쓴다.
        self._course_names = tuple(
            (name, node["properties"]["course_code"])
            for node in nodes
            if "Course" in node["labels"]
            and isinstance(name := node["properties"].get("name_ko"), str)
            and name
            and isinstance(node["properties"].get("course_code"), str)
        )

    def _in_named_area(
        self, question: str, candidates: Sequence[Any]
    ) -> tuple[Any, ...]:
        """Keep only the rules that sit in the education area the question names.

        `균형교양 이수요건은?` 에 기초교양 규칙이 함께 나오는 것은 2-gram 검색이
        `교양` 과 `이수` 를 공유하기 때문이다. 어느 규칙이 어느 영역에 속하는지는 추측할
        일이 아니라 그래프가 이미 알고 있다.

        영역을 두 곳에서 읽는다. 둘 다 적재 데이터이며 코드에 영역 이름을 적지 않는다.

        - `Rule -[:TARGETS]-> EducationArea` 관계. 이것이 정본이다.
        - 규칙의 검증된 원문에 영역 이름이 그대로 적힌 경우. 기준 데이터의 규칙 27건 중
          12건은 `TARGETS` 가 없어(2026-08-15 확인) 관계만으로는 판단할 수 없다.
          `기초교양 글로벌의사소통 영역에서…` 처럼 원문이 스스로 영역을 말한다.

        질문이 영역을 말하지 않았으면 아무것도 거르지 않는다.
        """

        wanted = self._named_area(question)
        if wanted is None:
            return tuple(candidates)
        kept = tuple(
            candidate
            for candidate in candidates
            if wanted in self._areas_of(candidate)
        )
        # 영역을 아는 규칙이 하나도 없으면 거르지 않는다. 적재가 아직 영역을 달지 않은
        # 자리에서 답할 수 있는 것까지 없애지 않기 위해서다.
        return kept or tuple(candidates)

    def _named_area(self, text: str) -> str | None:
        """The most specific education area this text names verbatim.

        `균형교양` 은 `교양` 을 품는다. 가장 긴 이름을 골라야 `균형교양` 질문이 교양 전체로
        넓어지지 않는다. 과목명에서 `고급자료구조` 와 `자료구조` 를 가르는 것과 같은 규칙이다.
        """

        matched = [
            (name, area_id) for name, area_id in self._area_names if name in text
        ]
        if not matched:
            return None
        return max(matched, key=lambda pair: len(pair[0]))[1]

    def _areas_of(self, candidate: Any) -> frozenset[str]:
        """Every area this rule belongs to, including the ancestors of each."""

        found: set[str] = set(self._rule_areas.get(candidate.fact_id, ()))
        wording = self._index.wording_for(candidate.fact_id)
        if wording:
            named = self._named_area(wording)
            if named is not None:
                found.add(named)
        return frozenset(
            ancestor for area_id in found for ancestor in _area_ancestors(area_id)
        )

    def _named_courses(self, question: str) -> tuple[Choice, ...]:
        """Courses the question names verbatim, keeping only the most specific one.

        `자료구조` 는 `고급자료구조` 의 부분 문자열이다. 질문에 그대로 등장한 이름 중
        **가장 긴 것**만 남겨야 `고급자료구조는?` 이 두 과목을 함께 내놓지 않는다. 동명
        과목은 학수번호가 달라 그대로 여럿 남고, 그때는 되묻는 것이 맞다.
        """

        matched = [
            (name, code) for name, code in self._course_names if name in question
        ]
        if not matched:
            return ()
        longest = max(len(name) for name, _ in matched)
        return tuple(
            Choice("course_code", code, f"{name}({code})")
            for name, code in matched
            if len(name) == longest
        )[:MAX_OPTIONS]

    def for_missing(
        self,
        question: str,
        missing: Sequence[str],
        resolved: Mapping[str, Any] | None = None,
    ) -> tuple[Choice, ...]:
        """Choices for the next scope the user can settle, given what is already settled.

        한 번에 하나만 묻는다. 여러 개를 동시에 물으면 화면이 복잡해지고, 앞의 답이 뒤의
        후보를 바꾸는 경우(조회 종류가 정해져야 그 종류의 개체가 좁혀진다)를 다룰 수 없다.

        **앞서 고른 값은 다음 선택지를 좁힌다.** 조회 종류를 이미 골랐으면 그 종류가 다루는
        사실만 후보가 된다. 그래서 되묻기를 이어 가면 후보가 계속 줄고, 고를 것이 남지 않는
        순간이 곧 답할 수 있는 상태다.
        """

        settled = dict(resolved or {})
        # 이미 고른 것은 다시 묻지 않는다. 같은 것을 되물으면 왕복이 끝나지 않는다.
        codes = [code for code in missing if not self._already_settled(str(code), settled)]
        for code in codes:
            choices = self._choices_for(question, str(code), settled)
            if choices:
                return _capped(str(code), choices)
        # 부족 코드로 고를 것을 못 만들었다. 질문이 어떤 사실을 가리키는지 보고 그에 맞는
        # 종류로 되묻고, 그마저 없으면 무엇을 물을 수 있는지로 되돌아간다.
        fallback = self._fallback_code(question, settled)
        if fallback and not self._already_settled(fallback, settled):
            choices = self._choices_for(question, fallback, settled)
            if choices:
                return _capped(fallback, choices)
        if self._already_settled("QUESTION_INTENT", settled):
            return ()
        return self._intents(question, settled)[:MAX_OPTIONS]

    @staticmethod
    def _already_settled(code: str, resolved: Mapping[str, Any]) -> bool:
        """Whether the user has already picked a value that answers this scope.

        고른 값이 조회 종류를 **함의**하는 경우도 해결된 것으로 본다. 이수요건 하나를
        골랐으면 그건 이수요건 조회이므로, 종류를 다시 묻는 것은 아는 것을 되묻는 셈이다.
        """

        if any(name in resolved for name in SCOPE_FILTERS.get(code, ())):
            return True
        if code == "QUESTION_INTENT":
            return any(name in FILTER_IMPLIED_MODE for name in resolved)
        return False

    def _fallback_code(
        self, question: str, resolved: Mapping[str, Any] | None = None
    ) -> str | None:
        """Which kind of scope to ask about next.

        순서가 중요하다. **조회 종류가 먼저고 그 종류 안의 개체가 나중이다.** 개체를 먼저
        물으면 사용자가 고른 개체를 답할 수 없는 종류로 조회하게 된다.

        다만 질문이 이미 한 종류만 가리키면 종류를 묻지 않는다. 아는 것을 되묻는 셈이라
        왕복만 늘어난다. 여러 종류에 걸쳐 있을 때만 종류를 먼저 묻는다.
        """

        settled = dict(resolved or {})
        chosen = settled.get("selection_mode")
        if isinstance(chosen, str):
            try:
                mode = SelectionMode(chosen)
            except ValueError:
                return None
            # 확장 family 는 학년도·학과만 정해지면 답할 수 있다. 더 물을 것이 없다.
            if mode in EXTENDED_FAMILIES:
                return None
            return MODE_FALLBACK_CODES.get(mode)

        modes = self._index.leading_modes(question, limit=MAX_OPTIONS * 3)
        if not modes:
            return None
        kinds = {
            MODE_FALLBACK_CODES.get(mode) or "QUESTION_INTENT" for mode in modes
        }
        if len(kinds) > 1:
            return "QUESTION_INTENT"
        return next(iter(kinds))

    def is_offered(
        self,
        question: str,
        filter_name: str,
        value: Any,
        resolved: Mapping[str, Any] | None = None,
    ) -> bool:
        """Whether this exact value was one of the choices we would have offered.

        되묻기 이후 요청은 사용자가 고른 값을 함께 보낸다. 서버가 상태를 들지 않으므로,
        같은 질문과 **그때까지 고른 값**으로 선택지를 다시 만들어 대조한다. 선택지 생성이
        결정론적이라 무상태로도 검증된다. 여기서 걸러지면 계획에 넣지 않는다.

        대조할 때는 검사 대상 값 자신을 뺀 나머지를 기준으로 삼는다. 그 값을 고르던
        시점의 상태가 기준이어야 하기 때문이다.
        """

        settled = {
            name: item
            for name, item in (resolved or {}).items()
            if name != filter_name
        }
        for code in MISSING_CODES:
            for choice in self._choices_for(question, code, settled):
                if choice.filter_name == filter_name and choice.value == value:
                    return True
        return False

    def _choices_for(
        self, question: str, code: str, resolved: Mapping[str, Any] | None = None
    ) -> tuple[Choice, ...]:
        settled = dict(resolved or {})
        # 후보가 하나뿐인 범위는 되묻지 않는다. 계획 단계가 이미 그 값으로 채우므로
        # (`_complete_scope`) 물어봐야 사용자에게 의미 없는 클릭만 시킨다.
        if code == "DEPARTMENT":
            return self._departments if len(self._departments) > 1 else ()
        if code == "ACADEMIC_YEAR":
            return self._years if len(self._years) > 1 else ()
        if code == "COURSE_IDENTITY":
            return self._courses(question, settled)
        if code == "COURSE_ASPECT":
            return self._course_aspects(question, settled)
        if code == "RULE_TOPIC":
            return self._rules(question, settled)
        if code == "QUESTION_INTENT":
            return self._intents(question, settled)
        return ()

    def _courses(
        self, question: str, resolved: Mapping[str, Any] | None = None
    ) -> tuple[Choice, ...]:
        """Course candidates the question's wording actually points at.

        조회 종류가 과목이 아닌 것으로 이미 정해졌으면 과목을 묻지 않는다. 골라 봐야
        그 종류로는 답할 수 없는 막다른 선택지가 되기 때문이다.
        """

        if not self._mode_allows(resolved, {"CourseOffering", "Course"}):
            return ()
        # 질문이 적재된 과목명을 그대로 말했으면 그 과목이 답이다. 검색 상위 다섯 개를
        # 늘어놓으면, 이미 정해진 것을 사용자에게 다시 고르게 시키는 셈이고 `자료구조`
        # 질문에 `고급자료구조` 가 함께 뜬다.
        named = self._named_courses(question)
        if named:
            return named
        seen: dict[str, Choice] = {}
        for candidate in self._index.search(
            question, limit=MAX_OPTIONS * 3, labels={"CourseOffering", "Course"}
        ):
            code = candidate.identifiers.get("course_code")
            name = candidate.identifiers.get("name_ko")
            if not code or code in seen:
                continue
            seen[code] = Choice(
                "course_code", code, f"{name}({code})" if name else code
            )
        return tuple(seen.values())

    def _course_aspects(
        self, question: str, resolved: Mapping[str, Any] | None = None
    ) -> tuple[Choice, ...]:
        """What can be asked about a course, so the label is never the answer.

        `자료구조?` 처럼 과목만 말한 질문은 그 과목의 **무엇을** 묻는지가 비어 있다.
        종전에는 개설 정보를 통째로 답했다. 대신 여기서 되물으면 라벨은 `학점` 이고
        답은 `3학점` 이라 서로 다르다. 고르는 행위가 실제로 질문을 좁힌다.

        고를 수 있는 것은 그 모드가 돌려줄 수 있는 필드 중, 명세가 한국어 표기를 선언한
        것뿐이다. 과목을 가리키는 식별자는 답이 아니라 주어이므로 뺀다.
        """

        if not self._mode_allows(resolved, {"CourseOffering", "Course"}):
            return ()
        return tuple(
            Choice(REQUESTED_FIELDS, [name], label)
            for name, label in self._aspect_labels.items()
        )

    def names_an_aspect(self, question: str) -> bool:
        """Whether the question already says which attribute it asks about.

        `몇 학년 몇 학기에 개설되나?` 는 이미 무엇을 묻는지 말했으므로 되묻지 않는다.
        대조 대상은 명세가 선언한 표기이며 코드에 낱말을 적지 않는다.
        """

        return any(
            term in question
            for terms in self._aspect_terms.values()
            for term in terms
        )

    def _rules(
        self, question: str, resolved: Mapping[str, Any] | None = None
    ) -> tuple[Choice, ...]:
        """Requirement candidates shown by what they are about, never by the answer.

        종전에는 규칙의 검증된 원문(`description_ko`)을 그대로 라벨로 썼다. 그런데 규칙
        답변도 같은 `description_ko` 를 그대로 내보낸다. 라벨과 답이 **글자까지 같아져**,
        사용자는 답을 읽고 그것을 눌러 같은 문장을 다시 보게 됐다. 고르는 행위가 정보를
        주지 못하는 상태였다(2026-08-15 담당자 지적).

        그래서 라벨을 "무엇에 대한 기준인가"로 바꾼다. 세 조각 모두 선언된 표기이며 새로
        요약하지 않는다.

            적용 대상 · 영역 · 기준 종류
            예) `일반 적용 대상 · 균형교양 · 학점 요건`

        라벨이 같아지는 규칙은 **하나의 선택지로 묶는다.** 그 라벨로는 서로 구분되지
        않으므로 갈라 놓아도 고를 수가 없고, 묶으면 고른 뒤 둘 다 답하게 된다. 값이
        처음부터 목록(`rule_ids`)이라 그릇을 바꾸지 않아도 된다.

        조회 종류가 이수요건이 아닌 것으로 정해졌으면 묻지 않는다.
        """

        if not self._mode_allows(resolved, {"Rule"}):
            return ()
        found = self._index.search(question, limit=MAX_OPTIONS * 3, labels={"Rule"})
        # 질문이 영역을 말했으면 그 영역의 규칙만 남긴다. 그러고 나서 점수 꼬리를 자른다.
        # 순서가 중요하다. 먼저 자르면 영역이 맞는 규칙이 꼬리에 있을 때 함께 사라진다.
        found = self._in_named_area(question, found)
        grouped: dict[str, list[str]] = {}
        for candidate in leading_candidates(found):
            rule_id = candidate.identifiers.get("rule_id")
            if not rule_id:
                continue
            label = self._rule_label(candidate)
            if not label:
                continue
            bucket = grouped.setdefault(label, [])
            if rule_id not in bucket:
                bucket.append(rule_id)
        return tuple(
            Choice("rule_ids", sorted(rule_ids), _truncate(label))
            for label, rule_ids in grouped.items()
        )

    def _rule_label(self, candidate: Any) -> str:
        """Name one rule by what it is about, using only declared wording."""

        parts: list[str] = []
        scope = self._scope_label(candidate.fact_id)
        if scope:
            parts.append(scope)
        areas = sorted(self._specific_areas(candidate))
        if areas:
            parts.append(", ".join(areas))
        kind = self._rule_type_labels.get(self._rule_kinds.get(candidate.fact_id))
        if kind:
            parts.append(kind)
        return _LABEL_JOIN.join(parts)

    def _scope_label(self, fact_id: str) -> str:
        """Who this rule applies to, with each facet stated once."""

        gathered: dict[str, list[str]] = {}
        # 같은 말이 두 속성에 걸쳐 있을 수 있다(편입생은 `student_type` 이면서
        # `admission_type=TRANSFER` 다). 속성 안에서도, 속성 사이에서도 한 번만 적는다.
        seen: set[str] = set()
        for scope_id in sorted(self._rule_scopes.get(fact_id, ())):
            for name, value in self._scope_facets.get(scope_id, {}).items():
                if value in seen:
                    continue
                seen.add(value)
                gathered.setdefault(name, []).append(value)
        return " ".join(
            ", ".join(sorted(gathered[name]))
            for name in _SCOPE_LABEL_PROPERTIES
            if gathered.get(name)
        )

    def _specific_areas(self, candidate: Any) -> set[str]:
        """Korean names of the areas this rule targets, without their ancestors.

        라벨에는 가장 구체적인 영역만 쓴다. 조상까지 붙이면 `교양 · 기초교양 · 미래설계`
        처럼 길어지기만 하고 구분에는 보태지 않는다.
        """

        names = {
            self._area_id_names[area_id]
            for area_id in self._rule_areas.get(candidate.fact_id, ())
            if area_id in self._area_id_names
        }
        if names:
            return names
        # `TARGETS` 가 없는 규칙이 기준 데이터 27건 중 12건이다. 그런 규칙은 검증된
        # 원문이 스스로 영역을 말한다(`기초교양 글로벌의사소통 영역에서…`).
        wording = self._index.wording_for(candidate.fact_id)
        named = self._named_area(wording) if wording else None
        if named is not None and named in self._area_id_names:
            return {self._area_id_names[named]}
        return set()

    def _mode_allows(
        self, resolved: Mapping[str, Any] | None, labels: set[str]
    ) -> bool:
        """Whether the already-chosen query kind can return facts of these labels."""

        chosen = (resolved or {}).get("selection_mode")
        if not isinstance(chosen, str):
            return True
        try:
            mode = SelectionMode(chosen)
        except ValueError:
            return False
        family = EXTENDED_FAMILIES.get(mode)
        if family is not None:
            return family.fact_label in labels
        return bool(BASE_MODE_LABELS.get(mode, frozenset()) & labels)

    def _intents(
        self, question: str, resolved: Mapping[str, Any] | None = None
    ) -> tuple[Choice, ...]:
        """What kind of fact the question may be asking about.

        계획 모델이 혼자 고르던 자리를 사용자에게 넘긴다. 선택지 문구는 명세가 선언한
        라벨 이름과 통제어휘 표기에서 만든다.
        """

        # 조회 종류를 이미 골랐으면 다시 묻지 않는다. 같은 것을 되물으면 왕복이
        # 끝나지 않는다.
        if isinstance((resolved or {}).get("selection_mode"), str):
            return ()
        # 질문 표기와 겹치는 사실이 하나도 없으면 **아무것도 제시하지 않는다.**
        #
        # 종전에는 그때 선언된 fact family 전체를 목록으로 되돌려 주었다. "고를 것이
        # 언제나 하나는 나와야 한다"는 뜻이었는데, 그 자리가 뜻 없는 입력의 통로가 됐다.
        # `ㅇ런아러` 같은 입력이 열여덟 종류 메뉴를 받고, 하나를 고르면 근거가 붙은
        # `검증된 답변` 까지 갔다(2026-08-15 실측). 답이 거짓이어서가 아니라 **아무도
        # 그것을 묻지 않았기 때문에** 틀린 답이다.
        #
        # 되물을 자격은 "계획이 서지 않았다"가 아니라 "질문이 적재된 사실을 가리킨다"에서
        # 나온다. 가리키는 것이 없으면 되묻는 대신 답할 수 없다고 하는 것이 맞다.
        modes = self._index.leading_modes(question, limit=MAX_OPTIONS * 3)
        if not modes:
            return ()
        choices: list[Choice] = []
        seen: set[str] = set()
        for mode in modes:
            label = self._mode_labels.get(mode)
            if not label or label in seen:
                continue
            seen.add(label)
            choices.append(Choice("selection_mode", mode.value, label))
        return tuple(choices)


def _mode_labels(spec: Mapping[str, Any]) -> dict[SelectionMode, str]:
    """Name each selection mode with wording the ontology already declares.

    라벨 이름만으로는 모드가 겹친다. 교육목표·역량·연계성은 소유자나 종류에 따라 여러
    모드로 갈리기 때문이다. family 가 선언한 기본 필터의 통제어휘 표기를 우선 쓰고,
    없을 때만 라벨 이름으로 되돌아간다.
    """

    label_names = {
        node["name"]: node.get("name_ko")
        for node in spec.get("node_labels", ())
        if isinstance(node, Mapping)
    }
    wording: dict[str, dict[str, str]] = {}
    for name, vocabulary in (spec.get("controlled_vocabularies") or {}).items():
        if not isinstance(vocabulary, Mapping):
            continue
        for item in vocabulary.get("values", ()):
            if isinstance(item, Mapping) and isinstance(item.get("value"), str):
                description = item.get("description_ko")
                if isinstance(description, str) and description:
                    wording.setdefault(name, {})[item["value"]] = description

    labels: dict[SelectionMode, str] = {}
    for mode, family in EXTENDED_FAMILIES.items():
        chosen: str | None = None
        for filter_name, value in family.default_filters.items():
            if isinstance(value, str):
                chosen = wording.get(filter_name, {}).get(value)
                if chosen:
                    break
        labels[mode] = chosen or label_names.get(family.fact_label) or family.fact_label
    for mode, label in (
        (SelectionMode.SINGLE_RULE, label_names.get("Rule")),
        (SelectionMode.MULTIPLE_RULES, label_names.get("Rule")),
        (SelectionMode.SINGLE_COURSE, label_names.get("CourseOffering")),
        (SelectionMode.COURSE_LIST, label_names.get("CourseOffering")),
    ):
        if label:
            labels[mode] = label
    return labels
