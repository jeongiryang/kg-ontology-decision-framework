"""Retrieve verified facts by wording so the plan can be derived instead of classified.

계획 모델에게 "이 질문은 열여덟 모드 중 무엇인가"를 묻는 구조는 fact family 를 늘릴수록
나빠진다. 선택지가 늘어나는 만큼 오분류가 늘기 때문이다. 이 모듈은 그 방향을 뒤집는다.

    질문 ─▶ 검색 ─▶ 상위 후보 ─┬─ 후보의 라벨   → selection_mode
                                ├─ 후보의 id     → filters
                                └─ family 선언   → requested_fields

분류 대신 대조다. family 를 더 얹어도 선택지가 늘지 않고 색인에 문서가 늘 뿐이다.

검색은 답을 고르지 않는다. 조회 **대상**을 좁힐 뿐이며, 좁혀진 대상은 종전과 똑같이
Cypher 검증 → 실행 → 결과 검증 → Claim 재검증을 거친다. 색인에 올리는 문서도 VERIFIED
사실 중 VERIFIED Evidence 가 직접 붙은 것으로 제한해, 검색이 근거 계약의 구멍이 되지
않게 한다.

한국어 처리는 형태소 분석기 없이 한다. 어절과 함께 **글자 2-gram** 을 색인해, 조사·어미가
붙어 표기가 달라져도(`교양은`/`교양을`/`교양의`) 같은 2-gram 을 공유하도록 만든다.
의존성을 늘리지 않고 교착어의 표기 변이를 흡수하는 방법이며, BM25 의 길이 정규화가
"질문 10자 대 규칙 원문 40자"의 길이 비대칭을 함께 보정한다.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .fact_families import EXTENDED_FAMILIES, SelectionMode

# 어절 분리. 한글·영숫자만 남기고 나머지는 경계로 본다.
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
_HANGUL = re.compile(r"[가-힣]")
# BM25 표준 계수. 짧은 질문과 긴 원문을 함께 다루므로 길이 정규화를 그대로 쓴다.
_BM25_K1 = 1.5
_BM25_B = 0.75
# 이 점수 아래는 우연한 글자 겹침으로 본다. 후보가 없으면 호출자가 종전 경로로 돌아간다.
MIN_SCORE = 0.5
# 최상위 점수에 견줘 이 비율 아래로 떨어지는 후보는 꼬리로 본다. 2-gram 은 `균형교양` 과
# `기초교양` 이 `교양` 을 공유하므로, 무관한 규칙도 얼마간 점수를 받는다. 자리를 채우려고
# 꼬리까지 실으면 묻지 않은 것이 답과 선택지에 섞인다.
SCORE_RATIO = 0.75


def leading_candidates(
    candidates: Sequence[FactCandidate], ratio: float = SCORE_RATIO
) -> tuple[FactCandidate, ...]:
    """Keep only the candidates close to the best score.

    계획기(`_rules_related_to`)에만 있던 자르기를 공용으로 올린 것이다. 종전에는 되묻기
    선택지 생성기가 이 자르기 없이 자리 수만큼 채워, 없앤 줄 알았던 "넓히기"가 선택지
    화면으로 자리를 옮겨 살아남았다(2026-08-15 실측: `균형교양 이수요건은?` 에 기초교양·
    대학영어 규칙이 선택지로 나옴).
    """

    if not candidates:
        return ()
    threshold = candidates[0].score * ratio
    return tuple(
        candidate for candidate in candidates if candidate.score >= threshold
    )

# 어느 라벨의 사실이 어떤 모드로 조회되는지. 기존 두 family 는 선언형이 아니므로
# 여기서 함께 적는다. 확장 family 는 선언에서 읽으므로 family 를 추가해도 이 표를
# 고칠 필요가 없다.
BASE_LABEL_MODES: Mapping[str, tuple[SelectionMode, ...]] = {
    "Rule": (SelectionMode.MULTIPLE_RULES, SelectionMode.SINGLE_RULE),
    "CourseOffering": (SelectionMode.COURSE_LIST, SelectionMode.SINGLE_COURSE),
    "Course": (SelectionMode.SINGLE_COURSE, SelectionMode.COURSE_LIST),
}
# 라벨마다 사람이 부르는 이름으로 쓰이는 속성. 질문에 그대로 나올 가능성이 높은
# 순서로 적는다. 여기 없는 속성은 색인하지 않는다.
_TEXT_PROPERTIES = (
    "name_ko",
    "description_ko",
    "raw_label",
    "course_name_ko",
    "credit_category",
    "area_raw",
    "normalized_name_ko",
    "rule_type",
    "aggregate_type",
    "entry_type",
)
# 검색 결과에서 계획의 필터로 옮길 수 있는 속성. 사실을 가리키는 id 와, 같은 라벨
# 안에서 종류를 가르는 구분자를 함께 담는다. 구분자가 필요한 이유는 한 라벨이
# 종류마다 다른 속성을 채우기 때문이다. 예를 들어 집계는 최소전공학점제면 boolean 만,
# 전공능력별 집계면 과목 수와 학점만 채워져 있다. 종류를 고정하지 않고 조회하면 빈
# 칸이 섞여 결과 검증이 전체를 막는다.
# 사람에게 보여 줄 대표 문장. 앞에 있는 것부터 고른다.
_DISPLAY_PROPERTIES = ("description_ko", "name_ko", "raw_label", "course_name_ko")
_FILTERABLE_PROPERTIES = (
    "rule_id",
    "name_ko",
    "goal_id",
    "competency_id",
    "course_code",
    "aggregate_id",
    "alignment_id",
    "aggregate_type",
    "alignment_type",
    "competency_type",
    "goal_scope",
    "entry_type",
    "credit_category",
)


def vocabulary_labels(spec: Mapping[str, Any]) -> dict[tuple[str, str], str]:
    """Map each ``(property, value)`` pair to the Korean wording the ontology declares.

    열거값 자체는 질문에 나오지 않는다. 사람이 쓰는 말은 명세의 ``description_ko`` 에
    이미 적혀 있으므로 그것을 색인에 함께 싣는다. 새 표현을 짓지 않고 선언된 표기만
    쓰기 때문에, 어휘가 바뀌면 명세를 고치는 것으로 검색도 함께 따라온다.

    키를 값 하나로 두면 안 된다. 같은 값이 서로 다른 어휘에 들어 있어 뜻이 달라지는
    경우가 있다. ``MAJOR`` 는 ``competency_type`` 에서는 "전공능력"이지만
    ``major_type`` 에서는 "전공"이다. 어느 속성의 값인지까지 함께 봐야 옳은 표기가
    나온다. 속성과 어휘의 연결은 명세의 ``controlled_vocabulary`` 선언에서 읽는다.
    """

    vocabularies = spec.get("controlled_vocabularies")
    if not isinstance(vocabularies, Mapping):
        return {}
    wording: dict[str, dict[str, str]] = {}
    for name, vocabulary in vocabularies.items():
        if not isinstance(vocabulary, Mapping):
            continue
        for item in vocabulary.get("values", ()):
            if not isinstance(item, Mapping):
                continue
            value, description = item.get("value"), item.get("description_ko")
            if isinstance(value, str) and isinstance(description, str) and description:
                wording.setdefault(name, {})[value] = description

    labels: dict[tuple[str, str], str] = {}
    for node in spec.get("node_labels", ()):
        for prop in node.get("properties", ()):
            vocabulary_name = prop.get("controlled_vocabulary")
            values = wording.get(vocabulary_name)
            if not values:
                continue
            for value, description in values.items():
                labels[(prop["name"], value)] = description
    return labels


def tokenize(text: str) -> list[str]:
    """Split into word tokens plus character bigrams.

    2-gram 을 함께 내는 이유는 조사·어미 때문이다. `교양은`과 `교양`은 어절로는 다른
    토큰이지만 `교양` 2-gram 을 공유한다. 형태소 분석기를 넣으면 더 정확하지만
    의존성이 늘고, 이 규모(문서 600건 미만)에서는 2-gram 으로 충분하다.
    """

    tokens: list[str] = []
    for word in _WORD.findall(text):
        lowered = word.lower()
        tokens.append(lowered)
        # 2-gram 은 한글 어절에만 낸다. 로마자는 조사가 붙지 않으므로 쪼갤 이유가 없고,
        # 쪼개면 뜻이 없는 글자쌍(`th`, `er`)이 학수번호나 영문 과목명과 우연히 겹쳐
        # 데이터와 무관한 질문에도 후보가 생긴다.
        if len(lowered) > 1 and _HANGUL.search(lowered):
            tokens.extend(
                lowered[index : index + 2] for index in range(len(lowered) - 1)
            )
    return tokens


@dataclass(frozen=True, slots=True)
class FactCandidate:
    """One verified fact the question may be pointing at."""

    fact_id: str
    labels: frozenset[str]
    identifiers: Mapping[str, str]
    score: float = 0.0

    def modes(self) -> tuple[SelectionMode, ...]:
        """Selection modes that can return this fact."""

        modes: list[SelectionMode] = []
        for mode, family in EXTENDED_FAMILIES.items():
            if family.fact_label in self.labels and mode not in modes:
                modes.append(mode)
        for label in sorted(self.labels):
            for mode in BASE_LABEL_MODES.get(label, ()):
                if mode not in modes:
                    modes.append(mode)
        return tuple(modes)


@dataclass(frozen=True, slots=True)
class _Document:
    fact_id: str
    labels: frozenset[str]
    identifiers: Mapping[str, str]
    # 어떤 속성이 실제로 채워져 있는지. 원문의 빈 칸을 0 으로 바꾸지 않는 것이 이
    # 저장소의 계약이라, 한 건만 비어 있어도 결과 검증이 조회 전체를 막는다. 없는
    # 필드를 애초에 요청하지 않으려면 적재 데이터에서 이것을 알아야 한다.
    present_fields: frozenset[str]
    # 되묻기 선택지에 보여 줄 표기. 색인용 텍스트는 근거 원문과 어휘 표기까지 이어
    # 붙인 것이라 화면에 그대로 쓸 수 없다. 사실 자신의 문장만 따로 둔다.
    wording: str
    counts: Mapping[str, int]
    length: int


class FactIndex:
    """BM25 index over verified, evidence-backed facts."""

    def __init__(self, documents: Sequence[_Document]):
        self._documents = tuple(documents)
        self._average_length = (
            sum(document.length for document in self._documents) / len(self._documents)
            if self._documents
            else 0.0
        )
        frequency: Counter[str] = Counter()
        for document in self._documents:
            frequency.update(document.counts.keys())
        total = len(self._documents)
        self._idf = {
            term: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for term, count in frequency.items()
        }

    def __len__(self) -> int:
        return len(self._documents)

    @classmethod
    def from_bundle(
        cls,
        bundle: Mapping[str, Any],
        vocabulary_labels: Mapping[tuple[str, str], str] | None = None,
    ) -> "FactIndex":
        """Index only VERIFIED facts that carry a VERIFIED Evidence of their own.

        색인 대상을 근거 계약과 같은 기준으로 자른다. 답할 수 없는 사실을 검색이
        떠올리면 계획이 그쪽으로 끌려가 결국 거절로 끝난다.

        문서에는 세 가지를 함께 싣는다.

        - 사실 자신의 한국어 속성 (``name_ko``, ``description_ko`` …)
        - 그 사실을 뒷받침하는 Evidence 의 원문. 집계처럼 한국어 속성이 없는 사실은
          원문에만 사람이 쓰는 표현이 남아 있다.
        - 열거값의 한국어 표기. ``MAJOR_COMPETENCY_COURSE_CREDIT`` 같은 코드는 질문에
          절대 나오지 않지만, 온톨로지가 선언한 "전공능력별 과목 수와 학점"은 나온다.

        셋 다 이미 저장소 안에 있는 표기이며 새로 지어내지 않는다.
        """

        labels_for_value = dict(vocabulary_labels or {})
        nodes = {node["id"]: node for node in bundle["nodes"]}
        verified_evidence = {
            node["id"]
            for node in bundle["nodes"]
            if "Evidence" in node["labels"]
            and node["properties"].get("verification_status") == "VERIFIED"
        }
        evidence_text: dict[str, list[str]] = {}
        grounded = set()
        for relationship in bundle["relationships"]:
            if relationship["type"] != "SUPPORTED_BY":
                continue
            if relationship["to_id"] not in verified_evidence:
                continue
            grounded.add(relationship["from_id"])
            raw = nodes[relationship["to_id"]]["properties"].get("raw_text")
            if isinstance(raw, str) and raw:
                evidence_text.setdefault(relationship["from_id"], []).append(raw)
        # 과목은 개설 정보를 통해 답한다. 과목명이 질문에 그대로 나오는 일이 잦으므로
        # 개설과 이어진 과목의 이름을 그 개설 문서에 함께 싣는다.
        course_of_offering = {
            relationship["from_id"]: relationship["to_id"]
            for relationship in bundle["relationships"]
            if relationship["type"] == "OF_COURSE"
        }

        documents: list[_Document] = []
        for node_id in sorted(grounded):
            node = nodes.get(node_id)
            if node is None or node["properties"].get("status") != "VERIFIED":
                continue
            properties = dict(node["properties"])
            course_id = course_of_offering.get(node_id)
            if course_id in nodes:
                for name in ("name_ko", "course_code"):
                    value = nodes[course_id]["properties"].get(name)
                    if isinstance(value, str) and value:
                        properties.setdefault(name, value)
            parts = [
                str(properties[name])
                for name in _TEXT_PROPERTIES
                if isinstance(properties.get(name), str) and properties[name]
            ]
            display = next(
                (
                    properties[name]
                    for name in _DISPLAY_PROPERTIES
                    if isinstance(properties.get(name), str) and properties[name]
                ),
                "",
            )
            parts.extend(
                labels_for_value[(name, value)]
                for name, value in properties.items()
                if isinstance(value, str) and (name, value) in labels_for_value
            )
            parts.extend(evidence_text.get(node_id, ()))
            text = " ".join(parts)
            if not text:
                continue
            counts = Counter(tokenize(text))
            documents.append(
                _Document(
                    fact_id=node_id,
                    labels=frozenset(
                        label for label in node["labels"] if label != "Evidence"
                    ),
                    identifiers={
                        name: properties[name]
                        for name in _FILTERABLE_PROPERTIES
                        if isinstance(properties.get(name), str) and properties[name]
                    },
                    present_fields=frozenset(
                        name
                        for name, value in node["properties"].items()
                        if value is not None
                    ),
                    wording=display,
                    counts=counts,
                    length=sum(counts.values()),
                )
            )
        return cls(documents)

    def wording_for(self, fact_id: str) -> str | None:
        """Readable source wording of one indexed fact, for user-facing choices."""

        for document in self._documents:
            if document.fact_id == fact_id:
                return document.wording
        return None

    def fields_always_present(
        self, label: str, discriminators: Mapping[str, Any]
    ) -> frozenset[str] | None:
        """Fields that every fact matching these equality filters actually carries.

        ``_fields_every_rule_has`` 가 Rule 에 하던 일을 라벨 일반으로 넓힌 것이다. 한
        라벨 안에서도 종류마다 채워진 속성이 다르다. 집계가 대표적이다. 최소전공학점제
        행은 boolean 만, 전공능력별 집계 행은 과목 수와 학점만 채워져 있다. 종류를
        고정한 뒤에도 그 종류가 갖지 않는 필드를 요청하면 결과 검증이 조회 전체를
        막으므로, 요청 전에 여기서 걸러 낸다.

        해당하는 사실이 없으면 ``None`` 을 돌려주어 호출자가 손대지 않도록 한다.
        """

        matched = [
            document
            for document in self._documents
            if label in document.labels
            and all(
                document.identifiers.get(name) == value
                for name, value in discriminators.items()
            )
        ]
        if not matched:
            return None
        common = set(matched[0].present_fields)
        for document in matched[1:]:
            common &= document.present_fields
        return frozenset(common)

    def search(
        self,
        question: str,
        *,
        limit: int = 10,
        labels: Iterable[str] | None = None,
    ) -> list[FactCandidate]:
        """Rank verified facts by how well their wording matches the question."""

        query = Counter(tokenize(question))
        if not query or not self._documents:
            return []
        wanted = frozenset(labels) if labels is not None else None
        scored: list[FactCandidate] = []
        for document in self._documents:
            if wanted is not None and not document.labels & wanted:
                continue
            score = 0.0
            for term in query:
                count = document.counts.get(term)
                if not count:
                    continue
                normalized = count * (_BM25_K1 + 1) / (
                    count
                    + _BM25_K1
                    * (
                        1
                        - _BM25_B
                        + _BM25_B * document.length / (self._average_length or 1.0)
                    )
                )
                score += self._idf.get(term, 0.0) * normalized
            if score > MIN_SCORE:
                scored.append(
                    FactCandidate(
                        document.fact_id, document.labels, document.identifiers, score
                    )
                )
        # 점수가 같으면 fact_id 순으로 고정한다. 같은 질문이 늘 같은 계획을 만들어야
        # 측정이 의미를 갖는다.
        scored.sort(key=lambda candidate: (-candidate.score, candidate.fact_id))
        return scored[:limit]

    def leading_modes(
        self, question: str, *, limit: int = 10, ratio: float = 0.6
    ) -> tuple[SelectionMode, ...]:
        """Derive the selection modes the top candidates point at, strongest first.

        최상위 점수의 ``ratio`` 이상을 받은 후보만 본다. 꼬리까지 세면 어떤 질문이든
        모든 모드를 가리키게 되어 판단에 쓸 수 없다.

        순위는 후보 하나가 아니라 **모드가 모은 점수의 합**으로 매긴다. 한 건만 우연히
        위로 올라온 모드보다, 여러 사실이 고르게 걸린 모드가 질문이 가리키는 대상일
        가능성이 높다. 짧은 통제어휘 표기가 우연히 1위를 차지해도 그것만으로 모드가
        뒤집히지 않는다.
        """

        candidates = self.search(question, limit=limit)
        if not candidates:
            return ()
        threshold = candidates[0].score * ratio
        totals: Counter[SelectionMode] = Counter()
        for candidate in candidates:
            if candidate.score < threshold:
                break
            for mode in candidate.modes():
                totals[mode] += candidate.score
        # 합이 같으면 모드 이름으로 고정해 같은 질문이 늘 같은 계획을 만들게 한다.
        return tuple(
            mode
            for mode, _ in sorted(totals.items(), key=lambda item: (-item[1], item[0].value))
        )
