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

from .fact_families import EXTENDED_FAMILIES, SelectionMode
from .fact_index import FactIndex

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
    "RULE_TOPIC",
    "QUESTION_INTENT",
)
# 무엇을 되물을지 정하지 못했을 때, 질문이 어떤 사실을 가리키는지에 따라 되묻는
# 종류를 고른다. 고정 순서로 훑으면 안 된다. 학과는 언제나 후보가 있어 먼저 걸리고,
# 과목 후보는 이수요건 질문에도 약하게 걸려 더 맞는 선택지를 가려 버린다.
MODE_FALLBACK_CODES: Mapping[SelectionMode, str] = {
    SelectionMode.SINGLE_RULE: "RULE_TOPIC",
    SelectionMode.MULTIPLE_RULES: "RULE_TOPIC",
    SelectionMode.SINGLE_COURSE: "COURSE_IDENTITY",
    SelectionMode.COURSE_LIST: "COURSE_IDENTITY",
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

    def for_missing(self, question: str, missing: Sequence[str]) -> tuple[Choice, ...]:
        """Choices for the first missing scope we can actually offer values for.

        한 번에 하나만 묻는다. 여러 개를 동시에 물으면 화면이 복잡해지고, 앞의 답이
        뒤의 후보를 바꾸는 경우(학과가 정해져야 과목 후보가 좁혀진다)를 다룰 수 없다.
        """

        for code in missing:
            choices = self._choices_for(question, str(code))
            if choices:
                return choices[:MAX_OPTIONS]
        # 어떤 부족 코드로도 고를 것을 못 만들었다. 그렇다고 아무것도 주지 않으면
        # 사용자가 다음에 뭘 해야 할지 알 수 없다. 질문이 어떤 사실을 가리키는지 보고
        # 그에 맞는 종류로 되묻고, 그마저 없으면 무엇을 물을 수 있는지로 되돌아간다.
        fallback = self._fallback_code(question)
        if fallback:
            choices = self._choices_for(question, fallback)
            if choices:
                return choices[:MAX_OPTIONS]
        return self._intents(question)[:MAX_OPTIONS]

    def _fallback_code(self, question: str) -> str | None:
        """Which kind of scope to ask about, judged from what the question matches."""

        modes = self._index.leading_modes(question, limit=MAX_OPTIONS * 3)
        if not modes:
            return None
        family = EXTENDED_FAMILIES.get(modes[0])
        if family is not None:
            return "QUESTION_INTENT"
        return MODE_FALLBACK_CODES.get(modes[0])

    def is_offered(self, question: str, filter_name: str, value: Any) -> bool:
        """Whether this exact value was one of the choices we would have offered.

        되묻기 이후 요청은 사용자가 고른 값을 함께 보낸다. 서버가 상태를 들지 않으므로,
        같은 질문으로 선택지를 **다시 만들어** 그 안에 있는 값인지 대조한다. 선택지
        생성이 결정론적이라 무상태로도 검증된다. 여기서 걸러지면 계획에 넣지 않는다.
        """

        for code in MISSING_CODES:
            for choice in self._choices_for(question, code):
                if choice.filter_name == filter_name and choice.value == value:
                    return True
        return False

    def _choices_for(self, question: str, code: str) -> tuple[Choice, ...]:
        if code == "DEPARTMENT":
            return self._departments
        if code == "ACADEMIC_YEAR":
            return self._years
        if code == "COURSE_IDENTITY":
            return self._courses(question)
        if code == "RULE_TOPIC":
            return self._rules(question)
        if code == "QUESTION_INTENT":
            return self._intents(question)
        return ()

    def _courses(self, question: str) -> tuple[Choice, ...]:
        """Course candidates the question's wording actually points at."""

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

    def _rules(self, question: str) -> tuple[Choice, ...]:
        """Requirement candidates shown by their verified source wording.

        규칙은 id 로는 알아볼 수 없으므로 검증된 원문을 그대로 보여 준다. 새로 요약하지
        않는다. 길면 자르되 자른 사실을 말줄임표로 드러낸다.
        """

        choices: list[Choice] = []
        seen: set[str] = set()
        for candidate in self._index.search(
            question, limit=MAX_OPTIONS * 3, labels={"Rule"}
        ):
            rule_id = candidate.identifiers.get("rule_id")
            wording = self._index.wording_for(candidate.fact_id)
            if not rule_id or rule_id in seen or not wording:
                continue
            seen.add(rule_id)
            choices.append(
                Choice("rule_ids", [rule_id], _truncate(wording), detail=wording)
            )
        return tuple(choices)

    def _intents(self, question: str) -> tuple[Choice, ...]:
        """What kind of fact the question may be asking about.

        계획 모델이 혼자 고르던 자리를 사용자에게 넘긴다. 선택지 문구는 명세가 선언한
        라벨 이름과 통제어휘 표기에서 만든다.
        """

        # 질문 표기와 겹치는 사실이 하나도 없을 수 있다(뜻 없는 입력, 데이터에 없는
        # 어휘). 그때도 무엇을 물을 수 있는지는 보여 준다. 이 목록도 지어낸 것이 아니라
        # 선언된 fact family 를 명세의 표기로 옮긴 것이다.
        modes = self._index.leading_modes(question, limit=MAX_OPTIONS * 3) or tuple(
            EXTENDED_FAMILIES
        )
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
