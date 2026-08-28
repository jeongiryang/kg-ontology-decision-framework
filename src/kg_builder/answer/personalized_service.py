"""Personalized presentation over the sealed evidence-backed chat response.

The underlying ``ChatResponse`` remains the only factual answer contract.  This layer
adds a versioned five-state decision outcome and a validated browser profile without
changing the response's eight wire fields or persisting student data.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from kg_builder.personalization import (
    DecisionOutcome,
    OutcomeStatus,
    ProfileExtraction,
    ProfileExtractor,
    UserProfile,
)
from kg_builder.query.course_names import CourseNameResolver
from kg_builder.query.progress import ProgressCallback

from .contracts import ChatResponse, ChatStatus


DEFAULT_BUNDLE = Path("data/verified/2026/2026_curriculum_kg_data.json")


class BaseChatService(Protocol):
    def ask(
        self,
        question: str,
        *,
        resolved: Mapping[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ChatResponse: ...


@dataclass(frozen=True, slots=True)
class PersonalizedChatResult:
    response: ChatResponse
    outcome: DecisionOutcome
    profile: UserProfile
    changed_profile_fields: tuple[str, ...] = ()


class PersonalizedCurriculumChatService:
    """Combine user assertions with verified facts without merging their provenance."""

    def __init__(self, service: BaseChatService, *, bundle_path: Path = DEFAULT_BUNDLE):
        self.service = service
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        self.nodes = {node["id"]: node for node in bundle["nodes"]}
        self.offering_course_names = {
            relationship["from_id"]: self.nodes.get(relationship["to_id"], {})
            .get("properties", {})
            .get("name_ko")
            for relationship in bundle["relationships"]
            if relationship["type"] == "OF_COURSE"
        }
        self.course_resolver = CourseNameResolver.from_bundle(bundle)
        self.extractor = ProfileExtractor(self.course_resolver)

    def ask(
        self,
        question: str,
        *,
        profile: UserProfile | None = None,
        resolved: Mapping[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> PersonalizedChatResult:
        current = profile or UserProfile()
        extraction = self.extractor.extract(question, current)
        preliminary = self._preflight(question, extraction)
        if preliminary is not None:
            request_id = str(uuid.uuid4())
            if preliminary.status is OutcomeStatus.OUT_OF_SCOPE:
                response = ChatResponse.out_of_scope(request_id)
            elif preliminary.status is OutcomeStatus.INSUFFICIENT_EVIDENCE:
                response = ChatResponse.unresolved(request_id)
            else:
                response = ChatResponse.clarification_required(
                    request_id, preliminary.message
                )
            return PersonalizedChatResult(
                response,
                preliminary,
                extraction.profile,
                extraction.changed_fields,
            )

        query_question = self._query_question(question, extraction.profile)
        response = self.service.ask(
            query_question,
            resolved=resolved,
            progress_callback=progress_callback,
        )
        outcome = self._outcome(question, extraction.profile, response)
        return PersonalizedChatResult(
            response, outcome, extraction.profile, extraction.changed_fields
        )

    def _preflight(
        self, question: str, extraction: ProfileExtraction
    ) -> DecisionOutcome | None:
        if extraction.conflicts:
            labels = ", ".join(self._profile_label(name) for name in extraction.conflicts)
            return DecisionOutcome(
                OutcomeStatus.NEEDS_USER_INFO,
                f"서로 다른 값이 함께 입력됐습니다. {labels} 값을 하나로 정정해 주세요.",
                extraction.conflicts,
            )
        explicit_years = {
            int(value)
            for value in re.findall(
                r"(?<!\d)((?:19|20)\d{2})(?:학년도|학번|\s*년에\s*입학)",
                question,
            )
        }
        if any(year != 2026 for year in explicit_years):
            return DecisionOutcome(
                OutcomeStatus.OUT_OF_SCOPE,
                "현재는 2026학년도 공통 교양과 컴퓨터공학과 교육과정만 확인할 수 있습니다.",
            )
        if re.search(r"(?:재수강|F학점|성적에\s*포함)", question):
            return DecisionOutcome(
                OutcomeStatus.INSUFFICIENT_EVIDENCE,
                "현재 PDF와 Verified KG에는 성적·재수강에 따른 개인별 졸업요건 적용 규정이 없습니다.",
                limitations=("성적·재수강 적용 규정 근거 없음",),
            )

        missing = self._required_profile_fields(question, extraction.profile)
        if missing:
            labels = ", ".join(self._profile_label(name) for name in missing)
            return DecisionOutcome(
                OutcomeStatus.NEEDS_USER_INFO,
                f"개인별 계산에는 {labels} 정보가 필요합니다. 확인 가능한 값부터 알려 주세요.",
                tuple(missing),
            )
        return None

    @staticmethod
    def _required_profile_fields(question: str, profile: UserProfile) -> list[str]:
        profile_course_reference = re.search(
            r"(?:내가\s*들은\s*과목|앞서\s*(?:말한|알려\s*준)\s*과목|"
            r"방금\s*(?:말한|알려\s*준)\s*과목)",
            question,
        )
        if profile_course_reference and not profile.completed_courses:
            return ["completed_courses"]
        personal_history = re.search(
            r"(?:내가|나는|현재|지금까지|들었|이수했|수강한|내\s*학점|졸업까지|남은\s*요건|"
            r"무엇을\s*더|뭐\s*더|영역별로\s*몇\s*학점)",
            question,
        )
        calculation = re.search(
            r"(?:남았|남은|계산|분석|절반|졸업할\s*수|무엇을\s*더|뭐\s*더|"
            r"영역별로\s*몇\s*학점)",
            question,
        )
        if not (personal_history and calculation):
            return []
        if re.search(r"(?:필수.{0,16}(?:빠뜨|다른)|빠뜨렸)", question):
            return []
        # A total-only progress ratio needs just the stated total.  It does not claim
        # that category or mandatory-course requirements are satisfied.
        if "절반" in question and "total" in profile.credits_by_category:
            return []
        required: list[str] = []
        # 2026/CSE is the declared PoC query scope, not a student assertion.  Ask for
        # admission scope only when the question is specifically about which
        # curriculum applies; ordinary progress arithmetic can use the PoC scope.
        if re.search(
            r"(?:적용되는\s*교육과정|어느\s*교육과정|학번별\s*기준|학번.{0,20}(?:판단|필요|말해야))",
            question,
        ):
            if profile.admission_year is None:
                required.append("admission_year")
            if profile.department_id is None:
                required.append("department_id")
        if re.search(r"(?:과목|무엇을\s*더|뭐\s*더|졸업할\s*수)", question) and not profile.completed_courses:
            required.append("completed_courses")
        if re.search(r"(?:학점|졸업까지|영역별|무엇을\s*더|뭐\s*더)", question):
            credits = profile.credits_by_category
            if not credits:
                required.append("credits")
            elif re.search(r"(?:영역별|앞으로\s*무엇|무엇을\s*들)", question):
                categories = []
                if "교양" in question:
                    categories.append("general")
                if "전공" in question:
                    categories.append("major")
                for field in categories or ["general", "major"]:
                    if field not in credits:
                        required.append(f"credits.{field}")
        return list(dict.fromkeys(required))

    def _query_question(self, question: str, profile: UserProfile) -> str:
        """Resolve a follow-up's course reference without exposing student records.

        Only stable course codes already validated against the browser profile are
        inserted.  Credit totals, admission data and free-form notes never enter the
        model question.  The codes still go through normal planning, canonical Cypher,
        SafetyPipeline and Evidence validation.
        """

        if self.course_resolver.find_mentions(question) or not profile.completed_courses:
            return question
        if not re.search(
            r"(?:내가\s*들은\s*과목|앞서\s*(?:말한|알려\s*준)\s*과목|"
            r"방금\s*(?:말한|알려\s*준)\s*과목)",
            question,
        ):
            return question
        requested = "이수구분"
        if re.search(r"(?:학점|몇\s*학점)", question):
            requested = "학점과 이수구분"
        codes = " ".join(item.course_code for item in profile.completed_courses)
        return f"{codes} 과목의 {requested}을 조회해 주세요."

    def _outcome(
        self, question: str, profile: UserProfile, response: ChatResponse
    ) -> DecisionOutcome:
        if response.status is ChatStatus.OUT_OF_SCOPE:
            return DecisionOutcome(
                OutcomeStatus.OUT_OF_SCOPE,
                "현재는 2026학년도 공통 교양과 컴퓨터공학과 교육과정만 확인할 수 있습니다.",
            )
        advisory = self._is_advisory(question)
        if advisory and response.status is not ChatStatus.ANSWERABLE:
            return DecisionOutcome(
                OutcomeStatus.ADVISORY,
                self._advisory_without_grounded_answer(question, profile),
                used_profile_fields=self._used_profile_fields(profile),
                limitations=("검증된 교과목 사실을 조회하지 못해 조건부 원칙만 안내",),
            )
        if response.status in {
            ChatStatus.UNRESOLVED,
            ChatStatus.NOT_FOUND,
            ChatStatus.SAFE_FAILURE,
            ChatStatus.UNSUPPORTED,
        }:
            return DecisionOutcome(
                OutcomeStatus.INSUFFICIENT_EVIDENCE,
                self._insufficient_message(question),
                limitations=("현재 Verified KG에서 확정 근거를 찾지 못했습니다.",),
            )
        if response.status is ChatStatus.CLARIFICATION_REQUIRED:
            if re.search(
                r"(?:자리|잔여석|증원|시간대|수강\s*신청|신청할\s*수)",
                question,
            ):
                return DecisionOutcome(
                    OutcomeStatus.INSUFFICIENT_EVIDENCE,
                    "과목의 교육과정상 정보는 확인할 수 있지만 실시간 수강신청 가능 여부, "
                    "잔여석, 증원과 시간표는 현재 PDF와 Verified KG에 없습니다.",
                    limitations=("실시간 수강신청 정보 없음",),
                )
            return DecisionOutcome(
                OutcomeStatus.NEEDS_USER_INFO,
                response.clarification or "답변에 필요한 정보를 조금 더 알려 주세요.",
            )

        message = self._grounded_message(question, profile, response)
        if advisory:
            sequence = self._curriculum_sequence_advice(question, response)
            if sequence:
                message = f"{message}\n\n{sequence}"
            message = (
                f"{message}\n\n"
                "추천은 확인된 과목 정보에 기반한 조건부 안내입니다. 실제 개설 여부, "
                "시간표, 잔여석과 선수과목은 현재 데이터에서 확인되지 않습니다."
            )
            return DecisionOutcome(
                OutcomeStatus.ADVISORY,
                message,
                used_profile_fields=self._used_profile_fields(profile),
                limitations=("실시간 개설·잔여석·시간표 정보 없음",),
            )
        limitation = self._grounding_limitation(question, response)
        if limitation is not None:
            return DecisionOutcome(
                OutcomeStatus.INSUFFICIENT_EVIDENCE,
                f"{message}\n\n{limitation}",
                used_profile_fields=self._used_profile_fields(profile),
                limitations=(limitation,),
            )
        return DecisionOutcome(
            OutcomeStatus.ANSWERED,
            message,
            used_profile_fields=self._used_profile_fields(profile),
        )

    def _grounded_message(
        self, question: str, profile: UserProfile, response: ChatResponse
    ) -> str:
        facts = [self.nodes.get(fact_id) for fact_id in response.used_fact_ids]
        facts = [item for item in facts if isinstance(item, Mapping)]
        credits = profile.credits_by_category
        if "대체" in question and any(
            "필수로 이수" in str(node.get("properties", {}).get("description_ko", ""))
            for node in facts
        ):
            return (
                f"{response.answer_text} 따라서 다른 과목의 학점으로 지정된 필수 과목 "
                "이수 자체를 대체할 수 있다고 확인된 근거는 없습니다."
            )
        if "균형교양" in question:
            descriptions = " ".join(
                str(node.get("properties", {}).get("description_ko", "")) for node in facts
            )
            if "4개 영역" in descriptions and re.search(
                r"모두\s*[^\d\s,]{2,30}\s*영역", question
            ):
                return (
                    f"{response.answer_text} 같은 영역의 과목만으로는 4개 영역에서 "
                    "영역별 1과목 이상이라는 조건을 충족하지 못합니다."
                )
            total = re.search(r"총\s*(\d+(?:\.\d+)?)\s*학점", question)
            threshold = next(
                (
                    node.get("properties", {}).get("value")
                    for node in facts
                    if "Rule" in node.get("labels", ())
                    and "균형교양을 최소" in str(
                        node.get("properties", {}).get("description_ko", "")
                    )
                ),
                None,
            )
            if total and isinstance(threshold, (int, float)):
                observed = float(total.group(1))
                if observed < threshold:
                    return (
                        f"{response.answer_text} 사용자 진술 {observed:g}학점은 검증된 "
                        f"최소 {threshold:g}학점에 {threshold - observed:g}학점 부족합니다."
                    )
        if re.search(r"(?:전공필수).{0,30}(?:빠뜨|다른\s*전공)", question):
            return (
                f"{response.answer_text} 사용자 진술처럼 지정된 전공필수 과목을 "
                "누락했다면 다른 전공과목의 학점만으로 과목 누락이 자동 해소됐다고 "
                "판정할 수 없습니다."
            )
        if "권장" in question and re.search(r"(?:반드시|필수)", question):
            return (
                f"{response.answer_text} 이 항목들은 Verified KG에서 학과 권장 과목으로 "
                "확인되며, 권장이라는 사실만으로 졸업 필수 과목이라고 단정할 수는 없습니다."
            )
        if re.search(r"(?:둘\s*중\s*하나|하나만\s*골)", question):
            completion_types = {
                node.get("properties", {}).get("completion_type")
                for node in facts
                if "CourseOffering" in node.get("labels", ())
            }
            if completion_types == {"MAJOR_REQUIRED"} and len(facts) >= 2:
                return (
                    f"{response.answer_text} 조회된 과목은 모두 전공필수로 검증됐으므로 "
                    "둘 중 하나만 선택하면 된다는 근거는 없습니다."
                )
        if re.search(r"(?:0\s*학점|학점에\s*포함.{0,10}않)", question):
            zero_required = [
                node
                for node in facts
                if "CourseOffering" in node.get("labels", ())
                and node.get("properties", {}).get("credits") == 0
                and node.get("properties", {}).get("completion_type") == "MAJOR_REQUIRED"
            ]
            if zero_required:
                return (
                    f"{response.answer_text} 0학점은 학점 합계에 더해지지 않지만 "
                    "조회된 이수구분은 전공필수이므로 학점이 0이라는 이유만으로 "
                    "이수 의무가 없어지지는 않습니다."
                )
        if re.search(r"(?:서로\s*다른\s*과목|과목명이\s*다르게|어떤\s*것으로\s*검색)", question):
            identities = self.course_resolver.find_mentions(question)
            if len(identities) == 1:
                identity = identities[0]
                return (
                    f"Verified KG에서는 질문의 표기를 하나의 과목 identity로 해석합니다. "
                    f"등록된 표기는 {identity.name_ko}, 학수번호는 {identity.course_code}입니다. "
                    f"{response.answer_text}"
                )
        if "절반" in question and "total" in credits:
            target = next(
                (
                    node["properties"].get("value")
                    for node in facts
                    if "Rule" in node.get("labels", ())
                    and "졸업학점 기준" in str(node["properties"].get("description_ko", ""))
                ),
                None,
            )
            if isinstance(target, (int, float)) and target > 0:
                completed = credits["total"]
                ratio = completed / target * 100
                return (
                    f"사용자가 제공한 총 이수학점은 {completed:g}학점이고, 검증된 "
                    f"졸업학점 기준은 {target:g}학점이므로 학점 수 기준으로 "
                    f"약 {ratio:.1f}%입니다. 과목별 필수요건 충족 여부는 별도입니다."
                )
        if credits and re.search(
            r"(?:영역별|남은\s*\d*\s*학점|몇\s*학점이\s*더|몇\s*학점\s*남)",
            question,
        ):
            thresholds: dict[str, float] = {}
            for node in facts:
                props = node.get("properties", {})
                description = str(props.get("description_ko", ""))
                value = props.get("value")
                if not isinstance(value, (int, float)):
                    continue
                if "교양과목을 최소" in description:
                    thresholds["general"] = float(value)
                elif "전공 합계 기준" in description:
                    thresholds["major"] = float(value)
                elif "졸업학점 기준" in description:
                    thresholds["total"] = float(value)
            observed = dict(credits)
            if "total" not in observed and observed:
                observed["total"] = sum(observed.values())
            labels = {"general": "교양", "major": "전공", "total": "총 이수학점"}
            calculations: list[str] = []
            for category in ("general", "major", "total"):
                if category not in observed or category not in thresholds:
                    continue
                deficit = max(thresholds[category] - observed[category], 0)
                calculations.append(
                    f"{labels[category]}은 사용자 진술 {observed[category]:g}학점, "
                    f"검증된 기준 {thresholds[category]:g}학점으로 학점 수 기준 "
                    f"{deficit:g}학점이 남습니다."
                )
            if calculations:
                return " ".join(calculations) + " 과목별·영역별 필수요건은 별도입니다."
        credentials = {item.test: item.value for item in profile.english_credentials}
        if credentials:
            comparisons: list[str] = []
            for node in facts:
                props = node.get("properties", {})
                subject = props.get("subject_field") or props.get("description_ko")
                threshold = props.get("value")
                if not isinstance(subject, str) or threshold is None:
                    continue
                test = self._credential_test_for_rule(subject)
                observed = credentials.get(test) if test else None
                if isinstance(observed, (int, float)) and isinstance(threshold, (int, float)):
                    result = "충족합니다" if observed >= threshold else "충족하지 못합니다"
                    comparisons.append(
                        f"사용자 진술 {test} {observed:g}점은 검증된 최소 "
                        f"{threshold:g}점 기준을 {result}."
                    )
            if comparisons:
                return " ".join(comparisons) + " " + response.answer_text
        return response.answer_text

    @staticmethod
    def _credential_test_for_rule(description: str) -> str | None:
        """Return one controlled credential name for a verified rule description."""

        normalized = description.upper().replace("-", " ")
        aliases = (
            ("TOEIC_SPEAKING", ("TOEIC SPEAKING",)),
            ("TOEFL_IBT", ("TOEFL",)),
            ("NEW_TEPS", ("NEW TEPS",)),
            ("GTELP_LEVEL_2", ("G TELP LEVEL 2",)),
            ("GTELP_LEVEL_3", ("G TELP LEVEL 3",)),
            ("TOEIC", ("TOEIC",)),
            ("TEPS", ("TEPS",)),
            ("OPIC", ("OPIC",)),
            ("FLEX", ("FLEX",)),
        )
        return next(
            (name for name, values in aliases if any(value in normalized for value in values)),
            None,
        )

    def _grounding_limitation(
        self, question: str, response: ChatResponse
    ) -> str | None:
        descriptions = " ".join(
            str(self.nodes.get(fact_id, {}).get("properties", {}).get("description_ko", ""))
            for fact_id in response.used_fact_ids
        )
        checks = (
            (
                r"(?:대신\s*(?:채워|들)|대체할|상쇄|자동\s*대체|으로\s*채워|전공으로\s*채|일반선택으로\s*들)",
                ("대체", "인정", "산입"),
            ),
            (
                r"(?:상위|고급).{0,18}(?:이수한\s*것으로\s*인정|자동\s*인정)",
                ("선수", "대체", "인정"),
            ),
            (r"(?:면제\s*신청|신청.{0,8}해야|신청\s*여부)", ("신청",)),
            (r"(?:재수강|성적에|F학점)", ("재수강", "성적")),
            (r"(?:별도\s*표시|사실상\s*필수)", ("별도", "사실상")),
            (r"(?:다음\s*해|내년|실제\s*개설|자리|증원|시간대)", ("실제 개설", "시간표")),
        )
        for pattern, required_terms in checks:
            if re.search(pattern, question) and not any(
                term in descriptions for term in required_terms
            ):
                return (
                    "질문의 적용·대체·신청 여부까지 확정하는 직접 VERIFIED 근거는 "
                    "현재 PDF와 KG에 없습니다. 확인된 사실과 해당 판단은 구분해야 합니다."
                )
        if re.search(r"(?:남은.{0,20}영역|어느.{0,8}영역|영역을\s*더)", question):
            if not any(term in descriptions for term in ("잔여", "선택", "확대교양")):
                return (
                    "필요한 총학점은 계산할 수 있지만 어느 교양 영역을 더 이수해야 "
                    "하는지는 현재 조회 근거만으로 확정할 수 없습니다."
                )
        return None

    @staticmethod
    def _is_advisory(question: str) -> bool:
        return bool(
            re.search(
                r"(?:추천|어떤\s*순서|우선|충분|무엇을\s*먼저|뭘\s*먼저|"
                r"4학년\s*때|진로|되고\s*싶|먼저\s*듣지)",
                question,
            )
        )

    def _curriculum_sequence_advice(
        self, question: str, response: ChatResponse
    ) -> str | None:
        if not re.search(r"(?:어떤\s*순서|순서로|먼저)", question):
            return None
        offerings = []
        for fact_id in response.used_fact_ids:
            node = self.nodes.get(fact_id, {})
            props = node.get("properties", {})
            if "CourseOffering" not in node.get("labels", ()):
                continue
            name = self.offering_course_names.get(fact_id)
            grades = props.get("grade_year")
            semester = props.get("semester")
            if isinstance(name, str) and isinstance(grades, list) and grades and isinstance(semester, str):
                offerings.append((min(grades), semester, name))
        if not offerings:
            return None
        semester_order = {"FIRST": 1, "SECOND": 2}
        ordered = sorted(
            offerings,
            key=lambda item: (item[0], semester_order.get(item[1], 99), item[2]),
        )
        grouped: dict[tuple[int, str], list[str]] = {}
        for grade, semester, name in ordered:
            grouped.setdefault((grade, semester), []).append(name)
        semester_label = {"FIRST": "1학기", "SECOND": "2학기", "BOTH": "1·2학기"}
        stages = [
            f"{grade}학년 {semester_label.get(semester, semester)} "
            + "·".join(names)
            for (grade, semester), names in grouped.items()
        ]
        return (
            f"교육과정의 편성 학년·학기만 기준으로 보면 {' → '.join(stages)} 순서로 검토할 수 "
            "있습니다. 이는 선수과목이나 개인 실력을 판정한 순서가 아닙니다."
        )

    @staticmethod
    def _advisory_without_grounded_answer(question: str, profile: UserProfile) -> str:
        goal = profile.career_goal
        prefix = f"사용자가 제공한 진로 목표({goal})를 고려하면 " if goal else ""
        return (
            f"{prefix}현재 Verified KG에서 확인되는 과목의 학년·학기·이수구분을 "
            "바탕으로 수강 순서를 검토할 수 있습니다. 다만 이번 조회에서는 추천을 "
            "뒷받침할 확정 과목 사실을 찾지 못했으므로 특정 과목을 단정해 추천하지 "
            "않습니다. 목표 분야나 후보 과목을 알려 주면 조건부로 비교할 수 있습니다."
        )

    @staticmethod
    def _insufficient_message(question: str) -> str:
        if re.search(r"(?:성적|재수강|자리|증원|시간대|실제\s*개설|휴학|복학|전과|인정)", question):
            return (
                "질문은 현재 교육과정과 관련 있지만, 적용·수강 가능 여부를 확정할 "
                "VERIFIED 근거가 현재 PDF와 KG에 없습니다. 학사 담당 부서 확인이 필요합니다."
            )
        return (
            "현재 PDF와 Verified KG에서 이 질문을 확정할 직접 근거를 찾지 못했습니다. "
            "사용자 정보를 더 입력해도 확인되지 않은 규정을 추측하지 않습니다."
        )

    @staticmethod
    def _used_profile_fields(profile: UserProfile) -> tuple[str, ...]:
        used: list[str] = []
        for name in (
            "admission_year",
            "curriculum_year",
            "department_id",
            "current_grade_year",
            "admission_type",
            "major_type",
            "career_goal",
        ):
            if getattr(profile, name) is not None:
                used.append(name)
        if profile.completed_courses:
            used.append("completed_courses")
        if profile.credits:
            used.append("credits")
        if profile.english_credentials:
            used.append("english_credentials")
        return tuple(used)

    @staticmethod
    def _profile_label(name: str) -> str:
        labels = {
            "curriculum_year": "적용 교육과정 연도",
            "department_id": "학과",
            "completed_courses": "이수 과목",
            "credits": "이수학점",
            "credits.general": "교양 이수학점",
            "credits.major": "전공 이수학점",
        }
        return labels.get(name, name)
