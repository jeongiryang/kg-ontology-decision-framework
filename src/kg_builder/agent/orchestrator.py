"""LLM-led, bounded orchestration over the existing grounded query service.

The model may resolve dialogue references, choose a small set of tools, and draft a
natural rendering of a small approved Claim set.  It cannot create school facts or
bypass the canonical Cypher/SafetyPipeline inside
``PersonalizedCurriculumChatService``.  A factual draft is displayed only after Python
rechecks its subject, enum/polarity, numeric roles, and Claim/Citation approval; every
unsupported combination falls back to the deterministic Claim renderer.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, Mapping

from kg_builder.answer.contracts import (
    ChatResponse,
    ChatStatus,
    ClaimPolarity,
    ClaimType,
    GroundedClaim,
)
from kg_builder.answer.korean_renderer import ENUM_KO
from kg_builder.answer.personalized_service import (
    PersonalizedChatResult,
    PersonalizedCurriculumChatService,
)
from kg_builder.answer.renderer import _ApprovedCompositePayload
from kg_builder.llm.client import LLMResponseError, StructuredLLMClient
from kg_builder.personalization import DecisionOutcome, OutcomeStatus, UserProfile
from kg_builder.query.progress import ProgressCallback

from .contracts import (
    AgentChatResult,
    AgentTraceEvent,
    ConversationContext,
    MAX_KG_QUERIES_PER_TURN,
    MAX_TOOL_CALLS,
    ToolName,
)
from .tools import validate_tool_input


_REFERENCE = re.compile(r"(?:그[ \t]*과목|그거|그것|그러면|앞의[ \t]*과목|둘[ \t]*중)")
_ELLIPTICAL_FOLLOWUP = re.compile(
    r"^[ \t]*(?:학수번호|과목코드|학점|이수구분|언제|몇[ \t]*학점|"
    r"다시|둘의|둘[ \t]*다|그중|그[ \t]*추천|그[ \t]*기준|같은[ \t]*거|"
    r"0[ \t]*학점|총학점만|확인[ \t]*가능한|"
    r"(?:성적표|개인[ \t]*정보)[ \t]*없이|요약|정리)"
)
_EXPLICIT_TOPIC_REDIRECT = re.compile(r"(?:말고|대신|이번에는|주제를[ \t]*바꿔)")
_CURRICULUM_SCOPE_QUESTION = re.compile(
    r"(?:어느|어떤|무슨)[ \t]*(?:학년도[ \t]*)?교육과정|"
    r"(?:적용|조회)[ \t]*(?:되는|할)[ \t]*교육과정|교육과정[ \t]*(?:기준|적용)"
)
_SERVICE_SCOPE_QUESTION = re.compile(
    r"(?:현재|지금)[ \t]*(?:지원|조회)[ \t]*(?:연도|학년도|범위)|"
    r"(?:지원|조회)[ \t]*(?:가능한|하는)[ \t]*(?:연도|학년도|범위)"
)
_GROUNDING_POLICY_ACK = re.compile(
    r"(?:확인되지[ \t]*않은|근거가[ \t]*없|근거[ \t]*없|추측하지|날조하지)"
)
_REQUIREMENT_TOPIC = re.compile(
    r"(?:균형교양|확대교양|기초교양|교양|전공[ \t]*필수|전공[ \t]*선택|"
    r"전공|대학영어|영어[ \t]*(?:면제|대체)|졸업)"
)
_AUDIENCE_TOPIC = re.compile(r"(?:편입생|신입생|재학생|복수전공생|부전공생)")
_GENERIC_REQUIREMENT_FOLLOWUP = re.compile(
    r"(?:총학점|같은[ \t]*영역|한[ \t]*영역|그[ \t]*기준|그[ \t]*요건|"
    r"면제|충족|넘으면|되는[ \t]*거)"
)
_COURSE_ASPECT = re.compile(
    r"(?:학수번호|과목[ \t]*코드|학점|이수구분|학년|학기|언제|개설|과목[ \t]*정보)"
)
_MULTI_TOPIC_SUMMARY = re.compile(
    r"(?:지금까지|앞서|이전).{0,24}(?:정리|요약|구분)|"
    r"(?:정보|내용|사실).{0,16}(?:구분|정리|요약)|(?:정리|요약)해[ \t]*줘"
)
_CREDIT_CALCULATION = re.compile(r"(?:부족|모자라|남은|얼마나[ \t]*남|계산|절반|충족)")
_MORE_REQUIRED_COURSES = re.compile(
    r"(?:더|추가로|앞으로|남은).{0,28}(?:전공[ \t]*)?필수[ \t]*과목|"
    r"(?:전공[ \t]*)?필수[ \t]*과목.{0,20}(?:더|추가|남)"
)
_CREDIT_REQUIREMENT_QUERIES = {
    "general": "2026학년도 교양 최소 이수학점 기준을 확인해 주세요.",
    "major": "2026학년도 컴퓨터공학과 전공 학점 합계 기준을 확인해 주세요.",
    "total": "2026학년도 컴퓨터공학과 졸업학점 최소 기준을 확인해 주세요.",
}
_MAJOR_REQUIRED_LIST_QUERY = (
    "2026학년도 컴퓨터공학과 전공필수 과목 목록과 각 과목 학점을 확인해 주세요."
)
# These are registered fact families, not evaluation-question phrases.  When a user
# asks for more than one family in a single turn each family is independently grounded
# and the sealed results are combined.
_FACT_FAMILY_QUERIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?:학과[ \t]*)?교육[ \t]*목표"),
        "컴퓨터공학과 교육목표를 확인해 주세요.",
    ),
    (
        re.compile(r"(?:학과[ \t]*)?인재상"),
        "컴퓨터공학과 인재상을 확인해 주세요.",
    ),
    (
        re.compile(r"(?:진로[ \t]*분야|진출[ \t]*분야|졸업[ \t]*후[ \t]*진로)"),
        "컴퓨터공학과 졸업 후 진출 분야를 확인해 주세요.",
    ),
    (
        re.compile(r"(?:전공[ \t]*능력|전공[ \t]*역량)"),
        "컴퓨터공학과 전공능력을 확인해 주세요.",
    ),
)
_CLAUSE_SPLIT = re.compile(r"(?:,|그리고|덧붙여|게다가|또한)")
_ACADEMIC_CLAUSE = re.compile(
    r"(?:학사|교육과정|교양|전공|졸업|학점|과목|수강|학년|학기|학번|학과|"
    r"면제|영어|TOEIC|토익|규정|요건)",
    re.IGNORECASE,
)
_OUT_OF_SCOPE_CLAUSE = re.compile(
    r"(?:날씨|식당|맛집|영화|주식|뉴스|운세|여행|교통|음악|게임|요리|스포츠)"
)
_ACADEMIC_LIVE_CONTRAST = re.compile(
    r"(?=.*(?:교육과정|학년|학기|과목|개설))(?=.*(?:실시간|잔여석|시간표|증원))",
    re.DOTALL,
)
_SAFE_DISCOURSE = re.compile(r"^[가-힣A-Za-z0-9\s.,?!·()%-]{0,320}$")
_FACT_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_COURSE_CODE = re.compile(r"(?<![A-Z0-9_])[A-Z]{2,}[A-Z0-9_-]*\d[A-Z0-9_-]*(?![A-Z0-9_])")
_FACT_CONTROL_TOKEN = re.compile(
    r"전공필수|전공선택|교양필수|교양선택|자유선택|"
    r"최소|최대|이상|이하|초과|미만|면제|의무|없|있|아니|않|불가|가능|충족"
)
_FORBIDDEN_NARRATIVE = re.compile(
    r"(?:system\s*prompt|api\s*key|password|token|cypher|neo4j|traceback|"
    r"비밀번호|토큰|프롬프트|MATCH\s*\(|RETURN\s+|CREATE\s+|DELETE\s+)",
    re.IGNORECASE,
)
_TOOL_DETAIL = {
    ToolName.READ_USER_PROFILE: "브라우저가 제공한 구조화 프로필을 확인했습니다.",
    ToolName.RESOLVE_COURSE: "대화에서 언급한 과목 identity를 검증했습니다.",
    ToolName.QUERY_CURRICULUM: "승인된 읽기 전용 KG 질의 경로를 실행했습니다.",
    ToolName.CALCULATE_REMAINING_CREDITS: "검증된 기준과 사용자 진술을 분리해 계산했습니다.",
    ToolName.ASK_CLARIFICATION: "답변에 필요한 최소 정보를 확인했습니다.",
    ToolName.GROUNDED_NARRATIVE: "검증된 사실 문장을 유지하며 대화형 표현을 구성했습니다.",
}

_PLANNER_TOOLS = tuple(
    item for item in ToolName if item is not ToolName.GROUNDED_NARRATIVE
)


_PLAN_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "resolved_question": {"type": "string", "minLength": 1, "maxLength": 2000},
        "referenced_course_codes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
            "uniqueItems": True,
        },
        "tools": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [item.value for item in _PLANNER_TOOLS],
            },
            "minItems": 1,
            "maxItems": MAX_TOOL_CALLS,
        },
        "topic": {"type": ["string", "null"], "maxLength": 160},
        "followup_question": {"type": ["string", "null"], "maxLength": 2000},
        "subquestions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 2000},
            "maxItems": 3,
            "uniqueItems": True,
        },
    },
    "required": ["resolved_question", "referenced_course_codes", "tools", "topic"],
    "additionalProperties": False,
}

_NARRATIVE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "grounded_answer": {"type": "string", "maxLength": 5000},
        "intro": {"type": "string", "maxLength": 160},
        "closing": {"type": "string", "maxLength": 320},
    },
    "required": ["grounded_answer", "intro", "closing"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class _Plan:
    question: str
    course_codes: tuple[str, ...]
    tools: tuple[ToolName, ...]
    topic: str | None
    followup_question: str | None = None
    subquestions: tuple[str, ...] = ()


class AgenticCurriculumChatService:
    """Bounded tool orchestrator that reuses the sole grounded query pipeline."""

    def __init__(
        self,
        service: PersonalizedCurriculumChatService,
        client: StructuredLLMClient,
    ) -> None:
        self.service = service
        self.client = client

    def ask(
        self,
        question: str,
        *,
        profile: UserProfile | None = None,
        resolved: Mapping[str, Any] | None = None,
        conversation: ConversationContext | None = None,
        progress_callback: ProgressCallback | None = None,
        trace_callback: Callable[[AgentTraceEvent], None] | None = None,
    ) -> AgentChatResult:
        trace: list[AgentTraceEvent] = []
        current_profile = profile or UserProfile()
        extractor = getattr(self.service, "extractor", None)
        extraction = (
            extractor.extract(question, current_profile)
            if extractor is not None
            else None
        )
        message_profile = (
            extraction.profile
            if extraction is not None and not extraction.conflicts
            else current_profile
        )
        if (
            extraction is not None
            and extraction.changed_fields
            and not extraction.conflicts
            and self.service._is_profile_statement_only(question)
        ):
            self._record(
                trace,
                trace_callback,
                ToolName.READ_USER_PROFILE,
                "COMPLETED",
                0,
            )
            labels = ", ".join(
                dict.fromkeys(
                    self.service._profile_label(name)
                    for name in extraction.changed_fields
                )
            )
            message = (
                f"제공한 사용자 정보({labels})를 현재 브라우저 프로필에 반영했습니다. "
                "이 정보는 학교 규정 근거와 구분되며 다음 질문에서만 참고합니다."
            )
            personalized = PersonalizedChatResult(
                response=ChatResponse.unresolved(str(uuid.uuid4())),
                outcome=DecisionOutcome(
                    OutcomeStatus.ADVISORY,
                    message,
                    used_profile_fields=extraction.changed_fields,
                ),
                profile=extraction.profile,
                changed_profile_fields=extraction.changed_fields,
            )
            empty_plan = _Plan(
                question,
                tuple(item.course_code for item in extraction.profile.completed_courses),
                (ToolName.READ_USER_PROFILE,),
                conversation.current_topic if conversation else None,
            )
            return AgentChatResult(
                personalized=personalized,
                conversation_id=(
                    conversation.conversation_id if conversation else "conversation:single"
                ),
                turn_id=conversation.turn_id if conversation else "turn:single",
                display_answer=message,
                trace=tuple(trace),
                summary=self._summary(
                    conversation,
                    conversation.current_topic if conversation else None,
                    personalized,
                ),
                current_topic=conversation.current_topic if conversation else None,
                recent_course_codes=self._course_codes(
                    conversation, empty_plan, personalized
                ),
            )
        if _SERVICE_SCOPE_QUESTION.search(question):
            self._record(
                trace, trace_callback, ToolName.READ_USER_PROFILE, "COMPLETED", 0
            )
            message = (
                "현재 서비스의 조회 범위는 2026학년도 공통 교양과 컴퓨터공학과 "
                "교육과정입니다. 다른 학년도·학과 규정은 현재 범위 밖입니다."
            )
            personalized = PersonalizedChatResult(
                ChatResponse.unresolved(str(uuid.uuid4())),
                DecisionOutcome(OutcomeStatus.ADVISORY, message),
                current_profile,
            )
            empty_plan = _Plan(question, (), (ToolName.READ_USER_PROFILE,), None)
            return AgentChatResult(
                personalized=personalized,
                conversation_id=conversation.conversation_id if conversation else "conversation:single",
                turn_id=conversation.turn_id if conversation else "turn:single",
                display_answer=message,
                trace=tuple(trace),
                summary=self._summary(conversation, None, personalized),
                current_topic=conversation.current_topic if conversation else None,
                recent_course_codes=self._course_codes(conversation, empty_plan, personalized),
            )
        if conversation is not None and _GROUNDING_POLICY_ACK.search(question):
            self._record(
                trace, trace_callback, ToolName.READ_USER_PROFILE, "COMPLETED", 0
            )
            message = (
                "확인했습니다. 학교 규정은 VERIFIED Evidence가 있는 범위에서만 "
                "답하고, 확인되지 않은 적용 여부는 추측하지 않겠습니다."
            )
            personalized = PersonalizedChatResult(
                ChatResponse.unresolved(str(uuid.uuid4())),
                DecisionOutcome(OutcomeStatus.ADVISORY, message),
                current_profile,
            )
            empty_plan = _Plan(question, (), (ToolName.READ_USER_PROFILE,), conversation.current_topic)
            return AgentChatResult(
                personalized=personalized,
                conversation_id=conversation.conversation_id,
                turn_id=conversation.turn_id,
                display_answer=message,
                trace=tuple(trace),
                summary=self._summary(conversation, conversation.current_topic, personalized),
                current_topic=conversation.current_topic,
                recent_course_codes=self._course_codes(conversation, empty_plan, personalized),
            )
        if (
            _CURRICULUM_SCOPE_QUESTION.search(question)
            and current_profile.admission_year is not None
            and current_profile.department_id is not None
        ):
            self._record(
                trace, trace_callback, ToolName.READ_USER_PROFILE, "COMPLETED", 0
            )
            message = (
                f"입력한 학번·학과 정보를 기준으로 현재 서비스는 "
                f"{current_profile.curriculum_year or current_profile.admission_year}학년도 "
                "컴퓨터공학과 교육과정 범위에서 조회합니다. 이는 사용자 입력을 적용한 "
                "검색 범위이며, 개인별 공식 교육과정 적용 판정을 대신하지 않습니다."
            )
            personalized = PersonalizedChatResult(
                response=ChatResponse.unresolved(str(uuid.uuid4())),
                outcome=DecisionOutcome(
                    OutcomeStatus.ADVISORY,
                    message,
                    used_profile_fields=(
                        "admission_year",
                        "curriculum_year",
                        "department_id",
                    ),
                ),
                profile=current_profile,
            )
            empty_plan = _Plan(
                question,
                (),
                (ToolName.READ_USER_PROFILE,),
                conversation.current_topic if conversation else None,
            )
            return AgentChatResult(
                personalized=personalized,
                conversation_id=(
                    conversation.conversation_id if conversation else "conversation:single"
                ),
                turn_id=conversation.turn_id if conversation else "turn:single",
                display_answer=message,
                trace=tuple(trace),
                summary=self._summary(
                    conversation,
                    conversation.current_topic if conversation else None,
                    personalized,
                ),
                current_topic=conversation.current_topic if conversation else None,
                recent_course_codes=self._course_codes(
                    conversation, empty_plan, personalized
                ),
            )
        plan = self._plan(question, conversation)
        if conversation is not None:
            plan = self._inherit_dialogue_scope(question, conversation, plan)
        if plan.course_codes and re.search(
            r"(?:그중|그[ \t]*가운데|이[ \t]*중).{0,16}(?:빼|누락|제외)",
            question,
        ):
            plan = _Plan(
                f"{plan.question}\n요청 필드: 각 과목의 이수구분",
                plan.course_codes,
                plan.tools,
                plan.topic,
                plan.followup_question,
                plan.subquestions,
            )
        calls = self._normalize_tools(plan.tools)
        if profile is not None and ToolName.READ_USER_PROFILE not in calls:
            calls = (ToolName.READ_USER_PROFILE, *calls)
        if (
            message_profile.credits
            and _CREDIT_CALCULATION.search(question)
            and ToolName.CALCULATE_REMAINING_CREDITS not in calls
        ):
            calls = (*calls, ToolName.CALCULATE_REMAINING_CREDITS)
        calls = calls[:MAX_TOOL_CALLS]

        if ToolName.READ_USER_PROFILE in calls:
            # Accessing the typed DTO is the complete operation; it never persists or
            # logs the profile values.
            validate_tool_input(ToolName.READ_USER_PROFILE, {})
            (profile or UserProfile()).to_dict()
            self._record(trace, trace_callback, ToolName.READ_USER_PROFILE, "COMPLETED", 0)
        if ToolName.RESOLVE_COURSE in calls:
            validate_tool_input(
                ToolName.RESOLVE_COURSE, {"course_codes": list(plan.course_codes)}
            )
            known_codes = {item.course_code for item in self.service.course_resolver.courses}
            if not set(plan.course_codes).issubset(known_codes):
                raise ValueError("conversation referred to an unknown course identity")
            self._record(trace, trace_callback, ToolName.RESOLVE_COURSE, "COMPLETED", 0)

        started = perf_counter()
        validate_tool_input(ToolName.QUERY_CURRICULUM, {"question": plan.question})
        personalized = self.service.ask(
            plan.question,
            profile=profile,
            resolved=resolved,
            progress_callback=progress_callback,
        )
        profile_statement = bool(
            personalized.changed_profile_fields
            and self.service._is_profile_statement_only(question)
        )
        if profile_statement:
            labels = ", ".join(
                dict.fromkeys(
                    self.service._profile_label(name)
                    for name in personalized.changed_profile_fields
                )
            )
            personalized = PersonalizedChatResult(
                response=ChatResponse.unresolved(personalized.response.request_id),
                outcome=DecisionOutcome(
                    OutcomeStatus.ADVISORY,
                    f"제공한 사용자 정보({labels})를 현재 브라우저 프로필에 반영했습니다. "
                    "이 정보는 학교 규정 근거와 구분되며 다음 질문에서만 참고합니다.",
                    used_profile_fields=personalized.changed_profile_fields,
                ),
                profile=personalized.profile,
                changed_profile_fields=personalized.changed_profile_fields,
            )
        self._record(
            trace,
            trace_callback,
            ToolName.QUERY_CURRICULUM,
            "COMPLETED",
            int((perf_counter() - started) * 1000),
        )
        tool_results = [personalized]
        query_executions = 1
        if ToolName.CALCULATE_REMAINING_CREDITS in calls:
            calculation_results: list[PersonalizedChatResult] = []
            categories = [
                category
                for category in ("general", "major", "total")
                if category in personalized.profile.credits_by_category
            ]
            # When category totals are present but no explicit total was supplied,
            # the deterministic presentation layer may calculate their sum.  It may
            # not infer a missing category value.
            if len(categories) > 1 and "total" not in categories:
                categories.append("total")
            for category in categories[:3]:
                if query_executions >= MAX_KG_QUERIES_PER_TURN:
                    break
                requirement_question = _CREDIT_REQUIREMENT_QUERIES[category]
                validate_tool_input(
                    ToolName.QUERY_CURRICULUM,
                    {"question": requirement_question},
                )
                requirement_started = perf_counter()
                requirement = self.service.ask(
                    requirement_question,
                    profile=tool_results[-1].profile,
                    resolved=None,
                    progress_callback=progress_callback,
                )
                calculation_results.append(requirement)
                query_executions += 1
                self._record(
                    trace,
                    trace_callback,
                    ToolName.QUERY_CURRICULUM,
                    "COMPLETED",
                    int((perf_counter() - requirement_started) * 1000),
                )
            if any(
                item.response.status is ChatStatus.ANSWERABLE
                for item in calculation_results
            ):
                tool_results = calculation_results
        subquestions = list(plan.subquestions)
        for item in self._fact_family_subquestions(question):
            if item != plan.question and item not in subquestions:
                subquestions.append(item)
        if (
            not profile_statement
            and _MORE_REQUIRED_COURSES.search(question)
            and personalized.profile.completed_courses
            and _MAJOR_REQUIRED_LIST_QUERY not in subquestions
        ):
            subquestions.append(_MAJOR_REQUIRED_LIST_QUERY)
        for subquestion in (() if profile_statement else subquestions[:3]):
            if query_executions >= MAX_KG_QUERIES_PER_TURN:
                break
            validate_tool_input(ToolName.QUERY_CURRICULUM, {"question": subquestion})
            sub_started = perf_counter()
            subresult = self.service.ask(
                subquestion,
                profile=tool_results[-1].profile,
                resolved=None,
                progress_callback=progress_callback,
            )
            tool_results.append(subresult)
            query_executions += 1
            self._record(
                trace,
                trace_callback,
                ToolName.QUERY_CURRICULUM,
                "COMPLETED",
                int((perf_counter() - sub_started) * 1000),
            )
        if (
            not profile_statement
            and plan.followup_question
            and query_executions < MAX_KG_QUERIES_PER_TURN
            and personalized.response.status in {ChatStatus.UNRESOLVED, ChatStatus.NOT_FOUND}
        ):
            retry_started = perf_counter()
            validate_tool_input(
                ToolName.QUERY_CURRICULUM, {"question": plan.followup_question}
            )
            retry = self.service.ask(
                plan.followup_question,
                profile=personalized.profile,
                resolved=resolved,
                progress_callback=progress_callback,
            )
            query_executions += 1
            self._record(
                trace,
                trace_callback,
                ToolName.QUERY_CURRICULUM,
                "COMPLETED",
                int((perf_counter() - retry_started) * 1000),
            )
            if retry.response.status is ChatStatus.ANSWERABLE:
                personalized = retry
                tool_results[0] = retry
        personalized = self._combine_grounded(
            tool_results, personalized, question=question
        )
        if (
            ToolName.CALCULATE_REMAINING_CREDITS in calls
            and personalized.response.status is ChatStatus.ANSWERABLE
        ):
            grounded_message = getattr(self.service, "_grounded_message", None)
            if grounded_message is not None:
                message = grounded_message(
                    question, personalized.profile, personalized.response
                )
                personalized = PersonalizedChatResult(
                    response=personalized.response,
                    outcome=DecisionOutcome(
                        personalized.outcome.status,
                        message,
                        required_user_fields=personalized.outcome.required_user_fields,
                        used_profile_fields=personalized.outcome.used_profile_fields,
                        limitations=personalized.outcome.limitations,
                    ),
                    profile=personalized.profile,
                    changed_profile_fields=personalized.changed_profile_fields,
                )
        if (
            personalized.response.status is ChatStatus.ANSWERABLE
            and (
                self._contains_mixed_out_of_scope_clause(question)
                or _ACADEMIC_LIVE_CONTRAST.search(question)
            )
        ):
            limitation = (
                "질문의 교육과정 부분은 검증된 근거로 답했지만, 실시간 개설·잔여석·"
                "시간표 정보는 현재 PDF와 Verified KG에 없습니다."
                if _ACADEMIC_LIVE_CONTRAST.search(question)
                else "질문의 교육과정 부분은 검증된 근거로 답했지만, 함께 요청한 다른 "
                "주제는 현재 2026 교육과정 데이터 범위 밖입니다."
            )
            personalized = PersonalizedChatResult(
                personalized.response,
                DecisionOutcome(
                    OutcomeStatus.INSUFFICIENT_EVIDENCE,
                    f"{personalized.outcome.message}\n\n{limitation}",
                    used_profile_fields=personalized.outcome.used_profile_fields,
                    limitations=tuple(
                        dict.fromkeys((*personalized.outcome.limitations, limitation))
                    ),
                ),
                personalized.profile,
                personalized.changed_profile_fields,
            )
        if (
            ToolName.CALCULATE_REMAINING_CREDITS in calls
            and personalized.outcome.used_profile_fields
        ):
            validate_tool_input(
                ToolName.CALCULATE_REMAINING_CREDITS,
                {
                    "categories": sorted(
                        set((personalized.profile.credits_by_category or {}).keys())
                        & {"total", "general", "major", "free_elective"}
                    )
                },
            )
            self._record(
                trace,
                trace_callback,
                ToolName.CALCULATE_REMAINING_CREDITS,
                "COMPLETED",
                0,
            )
        if (
            ToolName.ASK_CLARIFICATION in calls
            and personalized.outcome.status is OutcomeStatus.NEEDS_USER_INFO
        ):
            validate_tool_input(
                ToolName.ASK_CLARIFICATION,
                {"missing_fields": list(personalized.outcome.required_user_fields)},
            )
            self._record(trace, trace_callback, ToolName.ASK_CLARIFICATION, "COMPLETED", 0)
        narrative_started = perf_counter()
        display = self._narrative(question, personalized)
        validate_tool_input(
            ToolName.GROUNDED_NARRATIVE,
            {
                "claim_count": len(personalized.response.grounded_claims),
                "evidence_count": len(personalized.response.citations),
            },
        )
        self._record(
            trace,
            trace_callback,
            ToolName.GROUNDED_NARRATIVE,
            "COMPLETED",
            int((perf_counter() - narrative_started) * 1000),
        )
        summary = self._summary(conversation, plan.topic, personalized)
        codes = self._course_codes(conversation, plan, personalized)
        conversation_id = conversation.conversation_id if conversation else "conversation:single"
        turn_id = conversation.turn_id if conversation else "turn:single"
        return AgentChatResult(
            personalized=personalized,
            conversation_id=conversation_id,
            turn_id=turn_id,
            display_answer=display,
            trace=tuple(trace),
            summary=summary,
            current_topic=plan.topic or (conversation.current_topic if conversation else None),
            recent_course_codes=codes,
        )

    @staticmethod
    def _fact_family_subquestions(question: str) -> tuple[str, ...]:
        selected = [
            query for pattern, query in _FACT_FAMILY_QUERIES if pattern.search(question)
        ]
        return tuple(dict.fromkeys(selected)) if len(selected) > 1 else ()

    def _plan(self, question: str, context: ConversationContext | None) -> _Plan:
        if context is None:
            context = ConversationContext.from_payload(
                {
                    "version": 1,
                    "conversation_id": "conversation:single",
                    "turn_id": "turn:single",
                    "recent_messages": [],
                    "recent_course_codes": [],
                }
            )
            assert context is not None
        prompt = json.dumps(
            {"current_question": question, "conversation": context.prompt_context()},
            ensure_ascii=False,
        )
        try:
            generation = self.client.generate_json(
                system_prompt=(
                    "당신은 2026 교육과정 GraphRAG의 도구 계획기다. 이전 답변은 사실 근거가 "
                    "아니며, 현재 질문의 대명사와 생략만 해소한다. 도구는 허용 목록에서 "
                    "중복 없이 고른다. 학교 규정값·점수·학점은 만들지 않는다. "
                    "resolved_question은 현재 질문의 의미를 유지하고, 최근 course code는 "
                    "입력에 있는 값만 사용할 수 있다. 첫 조회가 근거를 찾지 못했을 때만 "
                    "의미가 같은 더 좁은 followup_question 하나를 제안할 수 있다. 현재 "
                    "질문이 독립된 여러 요청을 포함하거나 이전 주제 여러 개의 정리를 "
                    "요구할 때, 원문 또는 이전 사용자 메시지의 주제를 다시 검증할 "
                    "subquestions를 최대 3개 만든다. 이전 답변 문장은 "
                    "근거로 복사하지 말고 각 주제를 다시 조회한다."
                ),
                user_prompt=prompt,
                response_schema=_PLAN_SCHEMA,
            )
            payload = generation.payload
            return self._validate_plan(question, context, payload)
        except (LLMResponseError, ValueError, TypeError):
            return self._fallback_plan(question, context)

    def _validate_plan(
        self,
        original: str,
        context: ConversationContext,
        payload: Mapping[str, Any],
    ) -> _Plan:
        resolved = payload.get("resolved_question")
        if not isinstance(resolved, str) or not resolved.strip() or len(resolved) > 2000:
            raise ValueError("agent plan question is invalid")
        raw_codes = payload.get("referenced_course_codes")
        if not isinstance(raw_codes, list) or not set(raw_codes).issubset(context.recent_course_codes):
            raise ValueError("agent plan introduced an unverified course reference")
        known_codes = {item.course_code for item in self.service.course_resolver.courses}
        if not set(raw_codes).issubset(known_codes):
            raise ValueError("agent plan selected an unknown course reference")
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list):
            raise ValueError("agent tool plan is invalid")
        tools = tuple(ToolName(item) for item in raw_tools)
        if any(tool not in _PLANNER_TOOLS for tool in tools):
            raise ValueError("agent selected a non-planner operation")
        if ToolName.QUERY_CURRICULUM not in tools and ToolName.ASK_CLARIFICATION not in tools:
            tools = (*tools, ToolName.QUERY_CURRICULUM)
        # Without a reference expression the model may not silently replace the user's
        # question.  This prevents an unrelated past turn from steering a new topic.
        explicit_identities = self.service.course_resolver.find_mentions(original)
        explicit_courses = bool(explicit_identities)
        redirected = bool(_EXPLICIT_TOPIC_REDIRECT.search(original))
        contextual = bool(
            (_REFERENCE.search(original) or _ELLIPTICAL_FOLLOWUP.search(original))
            and not redirected
        )
        if explicit_courses and contextual:
            # A user may switch the course while eliding only the requested field
            # ("그러면 자료구조는?").  Let the model recover that field, but never
            # carry earlier course identities into the new subject.
            raw_codes = []
            original_codes = {item.course_code for item in explicit_identities}
            resolved_codes = {
                item.course_code
                for item in self.service.course_resolver.find_mentions(resolved)
            }
            if not original_codes.issubset(resolved_codes) or not resolved_codes.issubset(
                original_codes
            ):
                resolved = original
        elif not contextual:
            resolved = original
            raw_codes = []
        elif not raw_codes:
            prior_user_terms = {
                term
                for message in context.recent_messages
                if message.role.value == "user"
                for term in re.findall(r"[가-힣A-Za-z]{2,}", message.content)
            }
            resolved_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", resolved))
            if not prior_user_terms.intersection(resolved_terms):
                previous = self._latest_user_request(context)
                resolved = (
                    f"{previous}\n후속 요청: {original}" if previous else original
                )
        topic = payload.get("topic")
        followup = payload.get("followup_question")
        if followup is not None:
            if not isinstance(followup, str) or not followup.strip() or len(followup) > 2000:
                raise ValueError("agent follow-up query is invalid")
            original_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", original))
            followup_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", followup))
            if not original_terms.intersection(followup_terms) and not raw_codes:
                raise ValueError("agent follow-up query changed the user topic")
        raw_subquestions = payload.get("subquestions", [])
        if not isinstance(raw_subquestions, list) or len(raw_subquestions) > 3:
            raise ValueError("agent subquestions are invalid")
        allowed_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", original))
        for message in context.recent_messages:
            if message.role.value == "user":
                allowed_terms.update(re.findall(r"[가-힣A-Za-z]{2,}", message.content))
        if context.current_topic:
            allowed_terms.update(re.findall(r"[가-힣A-Za-z]{2,}", context.current_topic))
        subquestions: list[str] = []
        for subquestion in raw_subquestions:
            if not isinstance(subquestion, str) or not subquestion.strip() or len(subquestion) > 2000:
                raise ValueError("agent subquestion is invalid")
            terms = set(re.findall(r"[가-힣A-Za-z]{2,}", subquestion))
            mentions_known_code = any(code in subquestion for code in context.recent_course_codes)
            if not allowed_terms.intersection(terms) and not mentions_known_code:
                raise ValueError("agent subquestion is unrelated to verified dialogue topics")
            normalized = self._append_codes(subquestion.strip(), tuple(raw_codes))
            if normalized not in subquestions and normalized != resolved.strip():
                subquestions.append(normalized)
        if _MULTI_TOPIC_SUMMARY.search(original):
            subquestions = list(
                self._summary_subquestions(context, tuple(subquestions))
            )
        return _Plan(
            self._append_codes(resolved.strip(), tuple(raw_codes)),
            tuple(raw_codes),
            tools,
            topic.strip() if isinstance(topic, str) and topic.strip() else None,
            self._append_codes(followup.strip(), tuple(raw_codes))
            if isinstance(followup, str) and followup.strip()
            else None,
            tuple(subquestions),
        )

    def _fallback_plan(self, question: str, context: ConversationContext | None) -> _Plan:
        codes: tuple[str, ...] = ()
        tools = [ToolName.QUERY_CURRICULUM]
        contextual = bool(
            (_REFERENCE.search(question) or _ELLIPTICAL_FOLLOWUP.search(question))
            and not self.service.course_resolver.find_mentions(question)
            and not _EXPLICIT_TOPIC_REDIRECT.search(question)
        )
        resolved_question = question
        if context and contextual:
            previous = self._latest_user_request(context)
            if previous:
                resolved_question = f"{previous}\n후속 요청: {question}"
        if context and contextual and context.recent_course_codes:
            known_codes = {item.course_code for item in self.service.course_resolver.courses}
            codes = tuple(
                code for code in context.recent_course_codes[-20:] if code in known_codes
            )
            if codes:
                tools.insert(0, ToolName.RESOLVE_COURSE)
        subquestions: tuple[str, ...] = ()
        if context and _MULTI_TOPIC_SUMMARY.search(question):
            subquestions = self._summary_subquestions(context)
        return _Plan(
            AgenticCurriculumChatService._append_codes(resolved_question, codes),
            codes,
            tuple(tools),
            context.current_topic if context else None,
            None,
            subquestions,
        )

    def _latest_user_request(self, context: ConversationContext) -> str | None:
        for message in reversed(context.recent_messages):
            if message.role.value != "user":
                continue
            if self._is_profile_assertion(message.content):
                continue
            return message.content
        return None

    def _summary_subquestions(
        self,
        context: ConversationContext,
        proposed: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        """Recover up to three distinct recent topics for evidence revalidation."""

        recovered: list[str] = []
        seen_topics: set[str] = set()
        for question in proposed:
            identities = self.service.course_resolver.find_mentions(question)
            if identities:
                topic_key = "course:" + ",".join(
                    sorted(item.course_code for item in identities)
                )
            else:
                match = _REQUIREMENT_TOPIC.search(question)
                if match is None:
                    topic_key = "text:" + question.casefold()
                else:
                    topic_key = "requirement:" + re.sub(r"[ \t]", "", match.group(0))
            if topic_key in seen_topics:
                continue
            seen_topics.add(topic_key)
            recovered.append(question)
            if len(recovered) == 3:
                return tuple(recovered)
        recent: list[tuple[str, str]] = []
        for message in reversed(context.recent_messages):
            if message.role.value != "user" or self._is_profile_assertion(message.content):
                continue
            identities = self.service.course_resolver.find_mentions(message.content)
            if identities:
                topic_key = "course:" + ",".join(
                    sorted(item.course_code for item in identities)
                )
            else:
                match = _REQUIREMENT_TOPIC.search(message.content)
                if match is None:
                    # Pronoun-only turns do not create another topic; their grounded
                    # subject is represented by the preceding explicit turn.
                    continue
                topic_key = "requirement:" + re.sub(r"[ \t]", "", match.group(0))
            if topic_key in seen_topics or any(key == topic_key for key, _ in recent):
                continue
            recent.append((topic_key, message.content))
            if len(recovered) + len(recent) == 3:
                break
        for topic_key, question in reversed(recent):
            seen_topics.add(topic_key)
            recovered.append(question)
        return tuple(recovered)

    def _inherit_dialogue_scope(
        self,
        question: str,
        context: ConversationContext,
        plan: _Plan,
    ) -> _Plan:
        """Attach only omitted controlled scope, never prior answer prose.

        The LLM remains responsible for conversational interpretation.  This small
        deterministic guard prevents a generic follow-up such as ``총학점만?`` from
        losing the last requirement family when the model omits it, while avoiding
        the older failure mode of copying the entire previous question.
        """

        hints: list[str] = []
        explicit_courses = self.service.course_resolver.find_mentions(question)
        if (
            not explicit_courses
            and context.recent_course_codes
            and _ELLIPTICAL_FOLLOWUP.search(question)
            and _COURSE_ASPECT.search(question)
        ):
            codes = plan.course_codes or context.recent_course_codes
            return _Plan(
                self._append_codes(question, tuple(codes)),
                tuple(codes),
                plan.tools,
                plan.topic,
                plan.followup_question,
                plan.subquestions,
            )
        if (
            explicit_courses
            and (_REFERENCE.search(question) or _ELLIPTICAL_FOLLOWUP.search(question))
            and not _COURSE_ASPECT.search(question)
        ):
            for message in reversed(context.recent_messages):
                if message.role.value != "user":
                    continue
                match = _COURSE_ASPECT.search(message.content)
                if match:
                    hints.append(f"요청 필드: {match.group(0)}")
                    break
        if not _GENERIC_REQUIREMENT_FOLLOWUP.search(question) and not hints:
            return plan
        prefixes: list[str] = []
        if _GENERIC_REQUIREMENT_FOLLOWUP.search(question) and not _REQUIREMENT_TOPIC.search(question):
            for message in reversed(context.recent_messages):
                if message.role.value != "user":
                    continue
                match = _REQUIREMENT_TOPIC.search(message.content)
                if match:
                    prefixes.append(match.group(0))
                    break
        if "면제" in question and not _AUDIENCE_TOPIC.search(question):
            for message in reversed(context.recent_messages):
                if message.role.value != "user":
                    continue
                match = _AUDIENCE_TOPIC.search(message.content)
                if match:
                    prefixes.append(match.group(0))
                    break
        prefixes = list(dict.fromkeys(prefixes))
        additions = [
            *(f"대화에서 생략된 적용 범위: {prefix}" for prefix in prefixes),
            *hints,
        ]
        additions = [item for item in additions if item not in plan.question]
        if not additions:
            return plan
        # An explicitly named new course or audience-scoped requirement owns the
        # subject of this turn.  Rebuild from the current wording plus typed hints so
        # an LLM-expanded previous topic cannot leak into it.
        base_question = question if explicit_courses or any(
            _AUDIENCE_TOPIC.search(item) for item in additions
        ) else plan.question
        return _Plan(
            f"{base_question}\n" + "\n".join(additions),
            plan.course_codes,
            plan.tools,
            plan.topic,
            plan.followup_question,
            plan.subquestions,
        )

    def _is_profile_assertion(self, text: str) -> bool:
        extractor = getattr(self.service, "extractor", None)
        if extractor is None:
            return False
        extracted = extractor.extract(text, UserProfile())
        correction = bool(
            re.search(
                r"(?:\d+(?:\.\d+)?[ \t]*(?:점|학점).{0,16}(?:정정|수정|바꿀)|"
                r"\d+(?:\.\d+)?[ \t]*(?:가|이)?[ \t]*아니라[ \t]*\d+)",
                text,
            )
        )
        return bool(
            (extracted.changed_fields or correction)
            and self.service._is_profile_statement_only(text)
        )

    def _contains_mixed_out_of_scope_clause(self, question: str) -> bool:
        clauses = [item.strip() for item in _CLAUSE_SPLIT.split(question) if item.strip()]
        if len(clauses) < 2:
            return False
        academic = [
            bool(_ACADEMIC_CLAUSE.search(item) or self.service.course_resolver.find_mentions(item))
            for item in clauses
        ]
        return any(academic) and any(
            not is_academic and _OUT_OF_SCOPE_CLAUSE.search(clause)
            for clause, is_academic in zip(clauses, academic, strict=True)
        )

    @staticmethod
    def _append_codes(question: str, codes: tuple[str, ...]) -> str:
        if not codes:
            return question
        return f"{question}\n문맥상 검증할 과목 학수번호: {' '.join(codes)}"

    @staticmethod
    def _normalize_tools(tools: tuple[ToolName, ...]) -> tuple[ToolName, ...]:
        output: list[ToolName] = []
        for tool in tools:
            if tool not in output:
                output.append(tool)
        if ToolName.QUERY_CURRICULUM not in output:
            output.append(ToolName.QUERY_CURRICULUM)
        return tuple(output)

    def _combine_grounded(
        self,
        results: list[PersonalizedChatResult],
        fallback: PersonalizedChatResult,
        *,
        question: str,
    ) -> PersonalizedChatResult:
        """Combine only independently approved grounded results.

        The model may decompose a multi-topic request, but it never receives an API
        for supplying answer prose, Claims, or citations.  Each subquery traverses
        the normal validator/rendering path first, and this method only unions sealed
        ANSWERABLE DTOs.  If fewer than two grounded results exist, the best single
        result (or the original safe fallback) is returned unchanged.
        """

        answerable: list[PersonalizedChatResult] = []
        seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        for result in results:
            response = result.response
            if response.status is not ChatStatus.ANSWERABLE:
                continue
            signature = (response.used_fact_ids, response.used_evidence_ids)
            if signature in seen:
                continue
            seen.add(signature)
            answerable.append(result)
        if not answerable:
            return fallback
        ungrounded = [
            item
            for item in results
            if item.response.status is not ChatStatus.ANSWERABLE
            and item.outcome.status
            in {OutcomeStatus.INSUFFICIENT_EVIDENCE, OutcomeStatus.OUT_OF_SCOPE}
        ]
        if len(answerable) == 1:
            single = answerable[0]
            if not ungrounded:
                return single
            message = "\n\n".join(
                dict.fromkeys(
                    [
                        single.outcome.message,
                        *(item.outcome.message for item in ungrounded),
                    ]
                )
            )
            return PersonalizedChatResult(
                response=single.response,
                outcome=DecisionOutcome(
                    OutcomeStatus.INSUFFICIENT_EVIDENCE,
                    message,
                    required_user_fields=single.outcome.required_user_fields,
                    used_profile_fields=single.outcome.used_profile_fields,
                    limitations=single.outcome.limitations,
                ),
                profile=single.profile,
                changed_profile_fields=single.changed_profile_fields,
            )

        selected = answerable[:4]
        approved = _ApprovedCompositePayload._issue(
            tuple(item.response for item in selected)
        )
        response = selected[0].response.from_approved_answer(
            selected[0].response.request_id,
            approved,
        )
        messages = tuple(dict.fromkeys(item.outcome.message for item in selected))
        statuses = {item.outcome.status for item in selected}
        status = (
            OutcomeStatus.INSUFFICIENT_EVIDENCE
            if ungrounded
            else OutcomeStatus.ADVISORY
            if OutcomeStatus.ADVISORY in statuses
            else OutcomeStatus.ANSWERED
        )
        required = tuple(
            dict.fromkeys(
                field
                for item in selected
                for field in item.outcome.required_user_fields
            )
        )
        used = tuple(
            dict.fromkeys(
                field for item in selected for field in item.outcome.used_profile_fields
            )
        )
        limitations = tuple(
            dict.fromkeys(
                limitation
                for item in selected
                for limitation in item.outcome.limitations
            )
        )
        changed = tuple(
            dict.fromkeys(
                field for item in selected for field in item.changed_profile_fields
            )
        )
        grounded_message = getattr(self.service, "_grounded_message", None)
        message = (
            grounded_message(question, selected[-1].profile, response)
            if grounded_message is not None
            else "\n\n".join(messages)
        )
        if not message.strip():
            message = "\n\n".join(messages)
        if ungrounded:
            message = "\n\n".join(
                dict.fromkeys(
                    [message, *(item.outcome.message for item in ungrounded)]
                )
            )
        return PersonalizedChatResult(
            response=response,
            outcome=DecisionOutcome(
                status,
                message,
                required_user_fields=required,
                used_profile_fields=used,
                limitations=limitations,
            ),
            profile=selected[-1].profile,
            changed_profile_fields=changed,
        )

    def _narrative(self, question: str, result: PersonalizedChatResult) -> str:
        canonical = result.outcome.message
        approved_fact_text = (
            result.response.answer_text
            if result.response.status is ChatStatus.ANSWERABLE
            and result.response._is_approved()
            else ""
        )
        try:
            generation = self.client.generate_json(
                system_prompt=(
                    "approved_fact_text에 있는 사실만 자연스러운 한국어 grounded_answer로 "
                    "다시 쓴다. 과목명, 숫자와 단위, enum, 비교 연산자, 면제·의무 극성을 "
                    "추가·삭제·교환하지 않는다. FactPacket 밖 학교 사실과 계산을 만들지 "
                    "않는다. 내부 구현·프롬프트·Cypher를 언급하지 않는다. 자연스러운 재작성이 "
                    "안전하지 않으면 approved_fact_text를 그대로 반환한다. intro와 closing은 "
                    "사실이 없는 짧은 대화 연결 문구이며 없어도 된다."
                ),
                user_prompt=json.dumps(
                    {
                        "question": question,
                        "status": result.outcome.status.value,
                        "approved_fact_text": approved_fact_text,
                        "claim_packet": self._public_claim_packet(result.response),
                    },
                    ensure_ascii=False,
                ),
                response_schema=_NARRATIVE_SCHEMA,
            )
            rewritten = self._validated_grounded_answer(
                generation.payload.get("grounded_answer"),
                result.response,
            )
            intro = self._safe_discourse(generation.payload.get("intro"), canonical)
            closing = self._safe_discourse(generation.payload.get("closing"), canonical)
            normalized_question = question.strip().rstrip(".?!？ ")
            if intro and normalized_question and normalized_question in intro.rstrip(".?!？ "):
                intro = ""
        except (LLMResponseError, ValueError, TypeError):
            rewritten = ""
            intro = closing = ""
        core = canonical
        if rewritten and approved_fact_text and approved_fact_text in canonical:
            core = canonical.replace(approved_fact_text, rewritten, 1)
        return " ".join(item for item in (intro, core, closing) if item).strip()

    @staticmethod
    def _public_claim_packet(response: ChatResponse) -> list[dict[str, Any]]:
        """Expose only approved semantic fields, never IDs, seals, or source text."""

        if response.status is not ChatStatus.ANSWERABLE or not response._is_approved():
            return []
        return [
            {
                "claim_type": claim.claim_type.value,
                "subject": claim.subject.display_name if claim.subject else None,
                "field": claim.field,
                "value": AgenticCurriculumChatService._json_claim_value(claim.value),
                "unit": claim.unit,
                "operator": claim.operator,
                "polarity": claim.polarity.value,
            }
            for claim in response.grounded_claims
        ]

    @staticmethod
    def _json_claim_value(value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, tuple) and all(
            item is None or isinstance(item, (str, int, float, bool)) for item in value
        ):
            return list(value)
        # Lists of courses/rules deliberately remain opaque.  Their deterministic
        # renderer output is not eligible for a free-form rewrite.
        return "APPROVED_COMPLEX_VALUE"

    @staticmethod
    def _validated_grounded_answer(value: Any, response: ChatResponse) -> str:
        """Return a semantics-preserving factual draft or an empty fallback signal.

        This is intentionally conservative.  A small course field, one numeric rule,
        one Boolean policy, or the count/credit aggregate pair can be checked.  Lists,
        verbatim rules, recommendations, and other complex facts keep the canonical
        deterministic renderer sentence.
        """

        if (
            not isinstance(value, str)
            or response.status is not ChatStatus.ANSWERABLE
            or not response._is_approved()
        ):
            return ""
        draft = " ".join(value.split()).strip()
        source = " ".join(response.answer_text.split()).strip()
        if not draft or not source or draft == source:
            return source if draft == source else ""
        if len(draft) < max(8, int(len(source) * 0.55)) or len(draft) > len(source) * 1.65:
            return ""
        if _FORBIDDEN_NARRATIVE.search(draft) or any(ord(char) < 32 for char in draft):
            return ""
        claims = response.grounded_claims
        if not AgenticCurriculumChatService._rewritable_claims(claims):
            return ""
        if Counter(_FACT_NUMBER.findall(draft)) != Counter(_FACT_NUMBER.findall(source)):
            return ""
        if Counter(_COURSE_CODE.findall(draft)) != Counter(_COURSE_CODE.findall(source)):
            return ""
        if Counter(_FACT_CONTROL_TOKEN.findall(draft)) != Counter(
            _FACT_CONTROL_TOKEN.findall(source)
        ):
            return ""
        subjects = {
            claim.subject.display_name
            for claim in claims
            if claim.subject and claim.subject.display_name in source
        }
        if any(subject not in draft for subject in subjects):
            return ""
        if not all(
            AgenticCurriculumChatService._claim_role_is_preserved(claim, draft)
            for claim in claims
        ):
            return ""
        return draft

    @staticmethod
    def _rewritable_claims(claims: tuple[GroundedClaim, ...]) -> bool:
        if not claims or len(claims) > 3:
            return False
        kinds = {claim.claim_type for claim in claims}
        if kinds == {ClaimType.FIELD_VALUE}:
            subjects = {claim.subject for claim in claims}
            return len(subjects) == 1 and None not in subjects
        if len(claims) == 1 and kinds <= {
            ClaimType.NUMERIC_REQUIREMENT,
            ClaimType.BOOLEAN_POLICY,
        }:
            return True
        return kinds == {ClaimType.AGGREGATE} and {
            claim.field for claim in claims
        } <= {"fact_count", "credits_sum"}

    @staticmethod
    def _claim_role_is_preserved(claim: GroundedClaim, draft: str) -> bool:
        value = claim.value
        if claim.field == "course_code":
            return isinstance(value, str) and value in draft
        if claim.field == "grade_year":
            values = value if isinstance(value, tuple) else (value,)
            return all(
                re.search(rf"(?<!\d){re.escape(str(item))}\s*학년", draft)
                for item in values
            )
        if claim.field == "semester":
            label = ENUM_KO.get(value)
            return isinstance(label, str) and label in draft
        if claim.field == "completion_type":
            label = ENUM_KO.get(value)
            return isinstance(label, str) and label in draft
        if claim.field in {"credits", "credits_sum"} or claim.unit == "CREDIT":
            return re.search(rf"(?<!\d){re.escape(str(value))}\s*학점", draft) is not None
        if claim.field == "fact_count" or claim.unit == "COURSE":
            return re.search(
                rf"(?<!\d){re.escape(str(value))}\s*(?:개|과목)", draft
            ) is not None
        if claim.unit == "AREA":
            return re.search(
                rf"(?<!\d){re.escape(str(value))}\s*개\s*영역", draft
            ) is not None
        if claim.unit == "COURSE_PER_AREA":
            return re.search(
                rf"(?<!\d){re.escape(str(value))}\s*과목", draft
            ) is not None
        if claim.claim_type is ClaimType.BOOLEAN_POLICY:
            return (
                claim.value is True
                and claim.polarity is ClaimPolarity.EXEMPT
                and "면제" in draft
            )
        return False

    @staticmethod
    def _safe_discourse(value: Any, canonical: str = "") -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text or not _SAFE_DISCOURSE.fullmatch(text):
            return ""
        if text in canonical:
            return ""
        # Narrative glue may not introduce factual tokens.  Numbers and curriculum
        # entities stay in the approved deterministic text.
        if re.search(r"\d|학점|점수|학년|학기|과목|필수|선택|면제|졸업", text):
            return ""
        if _FORBIDDEN_NARRATIVE.search(text):
            return ""
        return text

    @staticmethod
    def _summary(
        context: ConversationContext | None,
        topic: str | None,
        result: PersonalizedChatResult,
    ) -> str:
        previous = context.summary if context else ""
        status = result.outcome.status.value
        # Summary intentionally stores no answer prose, personal credits, or raw user
        # question.  It is a routing hint, never a factual source.
        safe_topic = ""
        if isinstance(topic, str) and _SAFE_DISCOURSE.fullmatch(topic) and len(topic) <= 160:
            safe_topic = f" 현재 주제: {topic}."
        current = f"최근 대화 상태: {status}.{safe_topic}"
        return f"{previous} {current}".strip()[-1200:]

    def _course_codes(
        self,
        context: ConversationContext | None,
        plan: _Plan,
        result: PersonalizedChatResult,
    ) -> tuple[str, ...]:
        result_codes: list[str] = []

        def add_result_code(code: str | None) -> None:
            if isinstance(code, str) and code not in result_codes:
                result_codes.append(code)

        # Grounded claims carry entity IDs.  Only verified Course codes already known
        # by the resolver are retained as dialogue references.  A grounded result
        # replaces, rather than unions with, the prior focus: otherwise a list answer
        # permanently makes a later singular ``그 과목`` refer to every course seen in
        # the room.  Historical topics remain in the bounded messages/summary.
        for claim in result.response.grounded_claims:
            if claim.subject is None:
                # List/Aggregate Claims carry their subject identities in immutable
                # items rather than the optional single-subject slot.
                for item in claim.value if isinstance(claim.value, tuple) else ():
                    code = getattr(item, "course_code", None)
                    add_result_code(code)
                    if code is None:
                        name = getattr(item, "display_name", None)
                        if isinstance(name, str):
                            for identity in self.service.course_resolver.find_mentions(name):
                                add_result_code(identity.course_code)
                continue
            entity_id = claim.subject.entity_id
            if entity_id.startswith("course:cwnu:"):
                add_result_code(entity_id.rsplit(":", 1)[-1])
            else:
                for identity in self.service.course_resolver.find_mentions(
                    claim.subject.display_name
                ):
                    add_result_code(identity.course_code)
        if not result_codes:
            for course in result.profile.completed_courses:
                add_result_code(course.course_code)
        if result_codes:
            return tuple(result_codes[-20:])
        if plan.course_codes:
            return tuple(plan.course_codes[-20:])
        return tuple(context.recent_course_codes[-20:] if context else ())

    @staticmethod
    def _record(
        trace: list[AgentTraceEvent],
        callback: Callable[[AgentTraceEvent], None] | None,
        tool: ToolName,
        state: str,
        elapsed_ms: int,
    ) -> None:
        event = AgentTraceEvent(
            sequence=len(trace) + 1,
            tool=tool,
            state=state,
            elapsed_ms=elapsed_ms,
            detail=_TOOL_DETAIL[tool],
        )
        trace.append(event)
        if callback is not None:
            callback(event)
