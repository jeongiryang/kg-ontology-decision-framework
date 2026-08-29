"""LLM-led, bounded orchestration over the existing grounded query service.

The model may resolve dialogue references, decompose a bounded request, choose the
next approved retrieval, and render complete FactPacket sections.  It cannot create
school facts or bypass the canonical Cypher/SafetyPipeline inside
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
    AgentMode,
    AgentPolicy,
    AgentTraceEvent,
    ConversationContext,
    ToolName,
)
from .tools import validate_tool_input


_REFERENCE = re.compile(r"(?:그[ \t]*과목|그거|그것|그러면|앞의[ \t]*과목|둘[ \t]*중)")
_COURSE_SUBSTITUTION = re.compile(
    r"(?:(?:대신|대체).{0,24}(?:인정|가능|돼|되)|"
    r"(?:인정|가능).{0,24}(?:대신|대체))"
)
_SUBSTITUTION_RULE_FOLLOWUP = re.compile(r"(?:대체|인정|산입)")
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
    r"면제|충족|넘으면|되는[ \t]*거|"
    r"(?:성적표|개인[ \t]*(?:정보|이력))[ \t]*없이.{0,24}(?:일반|공통)[ \t]*(?:기준|요건|규정))"
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
_LIVE_INFORMATION = re.compile(
    r"(?:잔여\s*석|남은\s*자리|자리.{0,8}(?:있|없|남)|증원|시간표|시간대|"
    r"실시간\s*개설|이번\s*학기.{0,8}(?:열리|개설))"
)
_SAFE_DISCOURSE = re.compile(r"^[가-힣A-Za-z0-9\s.,?!·()%-]{0,320}$")
_FACT_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")
_COURSE_CODE = re.compile(r"(?<![A-Z0-9_])[A-Z]{2,}[A-Z0-9_-]*\d[A-Z0-9_-]*(?![A-Z0-9_])")
_FACT_CONTROL_TOKEN = re.compile(
    r"전공필수|전공선택|교양필수|교양선택|자유선택|"
    r"최소|최대|이상|이하|초과|미만|면제|의무|없|있|아니|않|불가|가능|충족"
)
_ASSERTION_CONTROL_TOKEN = re.compile(
    r"인정|대체|승인|권장|추천|재수강"
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
    ToolName.ASSESS_EVIDENCE: "확보한 근거를 평가하고 다음 탐색 필요 여부를 결정했습니다.",
    ToolName.GROUNDED_NARRATIVE: "검증된 사실 문장을 유지하며 대화형 표현을 구성했습니다.",
}

_PLANNER_TOOLS = tuple(
    item
    for item in ToolName
    if item not in {ToolName.ASSESS_EVIDENCE, ToolName.GROUNDED_NARRATIVE}
)


_PLAN_SCHEMA_BASE: Mapping[str, Any] = {
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
            "maxItems": 4,
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

_EVIDENCE_DECISION_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["STOP", "QUERY"]},
        "next_question": {"type": ["string", "null"], "maxLength": 2000},
        "reason": {
            "type": "string",
            "enum": [
                "SUFFICIENT_EVIDENCE",
                "MISSING_EVIDENCE",
                "INDEPENDENT_SUBQUESTION",
                "NO_SAFE_QUERY",
            ],
        },
    },
    "required": ["action", "next_question", "reason"],
    "additionalProperties": False,
}

_FACT_PACKET_NARRATIVE_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "packet_id": {"type": "string", "pattern": "^fact:[1-4]$"},
                    "text": {"type": "string", "minLength": 1, "maxLength": 5000},
                },
                "required": ["packet_id", "text"],
                "additionalProperties": False,
            },
            "maxItems": 4,
            "uniqueItems": True,
        },
        "intro": {"type": "string", "maxLength": 160},
        "closing": {"type": "string", "maxLength": 320},
    },
    "required": ["sections", "intro", "closing"],
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
        *,
        policy: AgentPolicy | None = None,
    ) -> None:
        self.service = service
        self.client = client
        self.policy = policy or AgentPolicy()

    def _plan_schema(self) -> dict[str, Any]:
        schema = json.loads(json.dumps(_PLAN_SCHEMA_BASE))
        schema["properties"]["tools"]["maxItems"] = self.policy.max_tool_calls
        schema["properties"]["subquestions"]["maxItems"] = self.policy.max_subquestions
        return schema

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
        turn_started = perf_counter()
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
        # Profile completeness and service-scope gates must be evaluated against the
        # user's actual turn before an LLM is allowed to rewrite it into a narrower
        # retrieval question.  Otherwise a request such as "내가 들은 과목에서 무엇을
        # 더 들어야 하나" can be reduced to a generic recommendation lookup and lose
        # the missing-credit requirement.  This gate supplies no academic answer: it
        # only preserves typed user assertions and the deterministic five-state
        # boundary already owned by PersonalizedCurriculumChatService.
        preliminary = (
            self.service._preflight(question, extraction)
            if extraction is not None
            else None
        )
        if preliminary is not None:
            personalized = self.service.ask(
                question,
                profile=current_profile,
                resolved=resolved,
                progress_callback=progress_callback,
            )
            if profile is not None or personalized.changed_profile_fields:
                self._record(
                    trace,
                    trace_callback,
                    ToolName.READ_USER_PROFILE,
                    "COMPLETED",
                    0,
                )
            if personalized.outcome.status is OutcomeStatus.NEEDS_USER_INFO:
                validate_tool_input(
                    ToolName.ASK_CLARIFICATION,
                    {
                        "missing_fields": list(
                            personalized.outcome.required_user_fields
                        )
                    },
                )
                self._record(
                    trace,
                    trace_callback,
                    ToolName.ASK_CLARIFICATION,
                    "COMPLETED",
                    0,
                )
            direct_plan = _Plan(
                question,
                tuple(
                    item.course_code
                    for item in personalized.profile.completed_courses
                ),
                tuple(item.tool for item in trace),
                conversation.current_topic if conversation else None,
            )
            return AgentChatResult(
                personalized=personalized,
                conversation_id=(
                    conversation.conversation_id
                    if conversation
                    else "conversation:single"
                ),
                turn_id=conversation.turn_id if conversation else "turn:single",
                display_answer=personalized.outcome.message,
                trace=tuple(trace),
                summary=self._summary(
                    conversation,
                    conversation.current_topic if conversation else None,
                    personalized,
                ),
                current_topic=conversation.current_topic if conversation else None,
                recent_course_codes=self._course_codes(
                    conversation, direct_plan, personalized
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
        calls = calls[: self.policy.max_tool_calls]

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
        executed_questions = {" ".join(plan.question.split())}
        calculation_results: list[PersonalizedChatResult] = []
        if ToolName.CALCULATE_REMAINING_CREDITS in calls:
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
                if not self._query_budget_available(query_executions, turn_started):
                    break
                requirement_question = _CREDIT_REQUIREMENT_QUERIES[category]
                normalized_question = " ".join(requirement_question.split())
                if normalized_question in executed_questions:
                    continue
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
                executed_questions.add(normalized_question)
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
                # Keep the original semantic result alongside deterministic credit
                # lookups.  It can carry an evidence-completeness limitation (for
                # example, a total-credit rule does not establish which curriculum
                # area the remaining credits must come from).  Replacing it here
                # would turn a supported partial calculation into an incorrectly
                # complete answer.
                tool_results.extend(calculation_results)
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
        if self.policy.mode is AgentMode.AGENTIC and not profile_statement:
            pending = list(dict.fromkeys(subquestions[: self.policy.max_subquestions]))
            if plan.followup_question:
                pending.append(plan.followup_question)
            (
                tool_results,
                personalized,
                query_executions,
            ) = self._run_result_driven_loop(
                question=question,
                plan=plan,
                conversation=conversation,
                pending_questions=pending,
                tool_results=tool_results,
                fallback=personalized,
                profile=tool_results[-1].profile,
                resolved=resolved,
                progress_callback=progress_callback,
                trace=trace,
                trace_callback=trace_callback,
                executed_questions=executed_questions,
                query_executions=query_executions,
                turn_started=turn_started,
            )
        else:
            for subquestion in (
                () if profile_statement else subquestions[: self.policy.max_subquestions]
            ):
                if not self._query_budget_available(query_executions, turn_started):
                    break
                normalized_question = " ".join(subquestion.split())
                if normalized_question in executed_questions:
                    continue
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
                executed_questions.add(normalized_question)
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
                and self._query_budget_available(query_executions, turn_started)
                and personalized.response.status
                in {ChatStatus.UNRESOLVED, ChatStatus.NOT_FOUND}
                and " ".join(plan.followup_question.split()) not in executed_questions
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
                executed_questions.add(" ".join(plan.followup_question.split()))
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
        # A result-driven recovery query is intentionally narrower than the user's
        # original request.  Re-apply the presentation service's semantic evidence
        # boundary to the combined, sealed response so a nearby verified rule cannot
        # replace the missing rule the user actually asked about (for example, a
        # generic credit threshold cannot establish course substitution).  This does
        # not alter Claims or Citations; it only keeps the five-state outcome honest.
        grounding_limitation = getattr(self.service, "_grounding_limitation", None)
        if (
            personalized.response.status is ChatStatus.ANSWERABLE
            and callable(grounding_limitation)
        ):
            limitation = grounding_limitation(question, personalized.response)
            if limitation is not None:
                grounded_message = getattr(self.service, "_grounded_message", None)
                preserve_advisory = (
                    personalized.outcome.status is OutcomeStatus.ADVISORY
                )
                message = personalized.outcome.message
                if not preserve_advisory and callable(grounded_message):
                    message = grounded_message(
                        question, personalized.profile, personalized.response
                    )
                display_message = (
                    message
                    if limitation in message
                    else "\n\n".join((message, limitation))
                )
                personalized = PersonalizedChatResult(
                    response=personalized.response,
                    outcome=DecisionOutcome(
                        OutcomeStatus.ADVISORY
                        if preserve_advisory
                        else OutcomeStatus.INSUFFICIENT_EVIDENCE,
                        display_message,
                        required_user_fields=(
                            personalized.outcome.required_user_fields
                        ),
                        used_profile_fields=personalized.outcome.used_profile_fields,
                        limitations=tuple(
                            dict.fromkeys(
                                (*personalized.outcome.limitations, limitation)
                            )
                        ),
                    ),
                    profile=personalized.profile,
                    changed_profile_fields=personalized.changed_profile_fields,
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
                calculation_complete = bool(
                    calculation_results
                    and any(
                        item.response.status is ChatStatus.ANSWERABLE
                        for item in calculation_results
                    )
                    and "사용자 진술" in message
                    and "학점이 남습니다" in message
                )
                calculation_resolves_outcome = bool(
                    calculation_complete
                    and not personalized.outcome.limitations
                    and personalized.outcome.status
                    is not OutcomeStatus.INSUFFICIENT_EVIDENCE
                )
                personalized = PersonalizedChatResult(
                    response=personalized.response,
                    outcome=DecisionOutcome(
                        OutcomeStatus.ANSWERED
                        if calculation_resolves_outcome
                        else personalized.outcome.status,
                        message
                        if calculation_resolves_outcome
                        else personalized.outcome.message,
                        required_user_fields=personalized.outcome.required_user_fields,
                        used_profile_fields=personalized.outcome.used_profile_fields,
                        limitations=()
                        if calculation_resolves_outcome
                        else personalized.outcome.limitations,
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
        display, narrative_metadata = self._narrative(
            question,
            personalized,
            sources=tool_results,
        )
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
            metadata=narrative_metadata,
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

    def _run_result_driven_loop(
        self,
        *,
        question: str,
        plan: _Plan,
        conversation: ConversationContext | None,
        pending_questions: list[str],
        tool_results: list[PersonalizedChatResult],
        fallback: PersonalizedChatResult,
        profile: UserProfile,
        resolved: Mapping[str, Any] | None,
        progress_callback: ProgressCallback | None,
        trace: list[AgentTraceEvent],
        trace_callback: Callable[[AgentTraceEvent], None] | None,
        executed_questions: set[str],
        query_executions: int,
        turn_started: float,
    ) -> tuple[list[PersonalizedChatResult], PersonalizedChatResult, int]:
        """Let the model choose the next grounded query after seeing safe results.

        The decision can only select a validated question.  It never supplies Cypher,
        Facts, Claims, or citations, and every selected question re-enters the sole
        personalized query/SafetyPipeline path.
        """

        pending = [
            item
            for item in dict.fromkeys(pending_questions)
            if " ".join(item.split()) not in executed_questions
        ]
        current = fallback
        for iteration in range(1, self.policy.max_iterations + 1):
            if not self._query_budget_available(query_executions, turn_started):
                break
            # One approved answer with no independently requested part is already
            # sufficient.  Avoid paying for an assessment call on a simple question.
            if (
                not pending
                and current.response.status is ChatStatus.ANSWERABLE
                and current.response._is_approved()
            ):
                break
            assessment_started = perf_counter()
            remaining = self.policy.max_kg_queries - query_executions
            validate_tool_input(
                ToolName.ASSESS_EVIDENCE,
                {
                    "result_count": min(6, len(tool_results)),
                    "remaining_query_budget": max(0, min(5, remaining)),
                },
            )
            next_question, reason = self._assess_evidence(
                question=question,
                plan=plan,
                conversation=conversation,
                results=tool_results,
                pending=pending,
                executed_questions=executed_questions,
                remaining_query_budget=remaining,
            )
            self._record(
                trace,
                trace_callback,
                ToolName.ASSESS_EVIDENCE,
                "COMPLETED",
                int((perf_counter() - assessment_started) * 1000),
                metadata={
                    "iteration": iteration,
                    "decision": "QUERY" if next_question else "STOP",
                    "reason": reason,
                },
            )
            if not next_question:
                break
            normalized = " ".join(next_question.split())
            if normalized in executed_questions:
                break
            validate_tool_input(ToolName.QUERY_CURRICULUM, {"question": next_question})
            query_started = perf_counter()
            result = self.service.ask(
                next_question,
                profile=profile,
                resolved=resolved if next_question == plan.followup_question else None,
                progress_callback=progress_callback,
            )
            tool_results.append(result)
            if result.response.status is ChatStatus.ANSWERABLE:
                current = result
                # A narrower recovery query replaces the failed lookup it was
                # designed to repair.  Keeping that predecessor would downgrade a
                # successfully grounded answer to INSUFFICIENT_EVIDENCE.  Failed
                # independent subquestions remain so partially answered compound
                # questions still report their missing part.
                recovery = reason == "MISSING_EVIDENCE" or (
                    plan.followup_question is not None
                    and next_question == plan.followup_question
                    and reason != "INDEPENDENT_SUBQUESTION"
                )
                if recovery:
                    for index in range(len(tool_results) - 2, -1, -1):
                        if tool_results[index].response.status is not ChatStatus.ANSWERABLE:
                            del tool_results[index]
                            break
            profile = result.profile
            query_executions += 1
            executed_questions.add(normalized)
            pending = [item for item in pending if " ".join(item.split()) != normalized]
            self._record(
                trace,
                trace_callback,
                ToolName.QUERY_CURRICULUM,
                "COMPLETED",
                int((perf_counter() - query_started) * 1000),
                metadata={"iteration": iteration},
            )
        return tool_results, current, query_executions

    def _assess_evidence(
        self,
        *,
        question: str,
        plan: _Plan,
        conversation: ConversationContext | None,
        results: list[PersonalizedChatResult],
        pending: list[str],
        executed_questions: set[str],
        remaining_query_budget: int,
    ) -> tuple[str | None, str]:
        packet = [
            {
                "result": index,
                "wire_status": item.response.status.value,
                "outcome_status": item.outcome.status.value,
                "approved_claims": self._public_claim_packet(item.response),
                "citation_count": len(item.response.citations),
                "limitations": list(item.outcome.limitations),
            }
            for index, item in enumerate(results[-6:], start=1)
        ]
        try:
            generation = self.client.generate_json(
                system_prompt=(
                    "당신은 근거 탐색 제어기다. 결과 packet과 원래 질문을 비교해 독립된 "
                    "요구가 아직 남았을 때만 QUERY를 선택한다. 이미 답한 내용을 다시 "
                    "조회하지 않는다. next_question은 pending_questions 중 하나를 그대로 "
                    "고르거나 같은 주제의 더 좁은 근거 조회여야 한다. 학교 규정값, 답변, "
                    "Cypher, Evidence ID를 만들지 않는다. 충분한 VERIFIED Claim과 Citation이 "
                    "있거나 안전한 추가 질문을 만들 수 없으면 STOP한다."
                ),
                user_prompt=json.dumps(
                    {
                        "original_question": question,
                        "resolved_question": plan.question,
                        "topic": plan.topic,
                        "conversation": conversation.prompt_context()
                        if conversation
                        else {},
                        "results": packet,
                        "pending_questions": pending,
                        "remaining_query_budget": remaining_query_budget,
                    },
                    ensure_ascii=False,
                ),
                response_schema=_EVIDENCE_DECISION_SCHEMA,
            )
            payload = generation.payload
            action = payload.get("action")
            reason = payload.get("reason")
            candidate = payload.get("next_question")
            if action == "STOP" and candidate is None and isinstance(reason, str):
                return None, reason
            if action != "QUERY" or not isinstance(candidate, str):
                raise ValueError("agent evidence decision is invalid")
            candidate = candidate.strip()
            if not candidate or len(candidate) > 2000:
                raise ValueError("agent next question is invalid")
            normalized = " ".join(candidate.split())
            if normalized in executed_questions:
                raise ValueError("agent repeated a grounded query")
            if _COURSE_SUBSTITUTION.search(question) and not _SUBSTITUTION_RULE_FOLLOWUP.search(
                candidate
            ):
                raise ValueError("agent next question dropped the substitution intent")
            if candidate not in pending and not self._related_followup(
                question, plan, conversation, candidate
            ):
                raise ValueError("agent next question changed the user topic")
            return candidate, reason if isinstance(reason, str) else "MISSING_EVIDENCE"
        except (LLMResponseError, ValueError, TypeError):
            # A failed control decision cannot create a new free-form query.  The
            # first already-validated planner subquestion is the only safe fallback.
            for candidate in pending:
                if " ".join(candidate.split()) not in executed_questions:
                    return candidate, "INDEPENDENT_SUBQUESTION"
            return None, "NO_SAFE_QUERY"

    def _related_followup(
        self,
        question: str,
        plan: _Plan,
        conversation: ConversationContext | None,
        candidate: str,
    ) -> bool:
        if _COURSE_SUBSTITUTION.search(question) and not _SUBSTITUTION_RULE_FOLLOWUP.search(
            candidate
        ):
            return False
        allowed_text = " ".join(
            [
                question,
                plan.question,
                plan.topic or "",
                *(
                    (message.content for message in conversation.recent_messages)
                    if conversation
                    else ()
                ),
            ]
        )
        allowed_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", allowed_text))
        candidate_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", candidate))
        if not candidate_terms or not allowed_terms.intersection(candidate_terms):
            return False
        mentioned = self.service.course_resolver.find_mentions(candidate)
        known_codes = {item.course_code for item in self.service.course_resolver.courses}
        if any(item.course_code not in known_codes for item in mentioned):
            return False
        if mentioned:
            allowed_codes = {
                item.course_code
                for item in self.service.course_resolver.find_mentions(allowed_text)
            } | set(plan.course_codes)
            if conversation is not None:
                allowed_codes.update(conversation.recent_course_codes)
            if not {item.course_code for item in mentioned}.issubset(allowed_codes):
                return False
        return True

    def _query_budget_available(self, executions: int, started: float) -> bool:
        return (
            executions < self.policy.max_kg_queries
            and perf_counter() - started < self.policy.max_turn_seconds
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
                    f"subquestions를 최대 {self.policy.max_subquestions}개 만든다. 이전 답변 문장은 "
                    "근거로 복사하지 말고 각 주제를 다시 조회한다."
                ),
                user_prompt=prompt,
                response_schema=self._plan_schema(),
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
        if (
            not isinstance(raw_codes, list)
            or any(not isinstance(item, str) for item in raw_codes)
            or not set(raw_codes).issubset(context.recent_course_codes)
        ):
            raise ValueError("agent plan introduced an unverified course reference")
        raw_codes = list(dict.fromkeys(raw_codes))
        known_codes = {item.course_code for item in self.service.course_resolver.courses}
        if not set(raw_codes).issubset(known_codes):
            raise ValueError("agent plan selected an unknown course reference")
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list) or any(
            not isinstance(item, str) for item in raw_tools
        ):
            raise ValueError("agent tool plan is invalid")
        raw_tools = list(dict.fromkeys(raw_tools))
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
        if topic is not None and (
            not isinstance(topic, str)
            or not topic.strip()
            or len(topic.strip()) > 160
            or not _SAFE_DISCOURSE.fullmatch(topic.strip())
        ):
            raise ValueError("agent topic is invalid")
        followup = payload.get("followup_question")
        if followup is not None:
            if not isinstance(followup, str) or not followup.strip() or len(followup) > 2000:
                raise ValueError("agent follow-up query is invalid")
            # A missing substitution rule cannot be recovered with a merely nearby
            # graduation-credit or course-offering lookup. Keep only a narrower
            # query that still asks about substitution/recognition; otherwise stop
            # with the original evidence gap instead of presenting unrelated facts.
            if _COURSE_SUBSTITUTION.search(original) and not _SUBSTITUTION_RULE_FOLLOWUP.search(
                followup
            ):
                followup = None
            else:
                original_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", original))
                followup_terms = set(re.findall(r"[가-힣A-Za-z]{2,}", followup))
                if not original_terms.intersection(followup_terms) and not raw_codes:
                    raise ValueError("agent follow-up query changed the user topic")
        raw_subquestions = payload.get("subquestions", [])
        if (
            not isinstance(raw_subquestions, list)
            or any(not isinstance(item, str) for item in raw_subquestions)
            or len(raw_subquestions) > self.policy.max_subquestions
        ):
            raise ValueError("agent subquestions are invalid")
        raw_subquestions = list(dict.fromkeys(raw_subquestions))
        # Independent subqueries may expand only an actual compound request or a
        # request to re-ground several earlier topics. A model suggestion that merely
        # shares a generic word such as "교육과정" must not attach an unrelated but
        # valid school rule to a simple answer.
        if not self._independent_subquestions_allowed(original, context):
            raw_subquestions = []
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
            # When several explicit user turns exist, replay those exact requests;
            # a generic model proposal must not replace them. A single compound turn
            # may still use the validated model decomposition, and a first-turn
            # compound summary has no prior messages to replay.
            prior_user_turns = sum(
                message.role.value == "user" for message in context.recent_messages
            )
            proposed = tuple(subquestions) if prior_user_turns < 2 else ()
            subquestions = list(self._summary_subquestions(context, proposed))
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

    @staticmethod
    def _independent_subquestions_allowed(
        question: str, context: ConversationContext
    ) -> bool:
        if _MULTI_TOPIC_SUMMARY.search(question):
            return True
        if len(AgenticCurriculumChatService._fact_family_subquestions(question)) > 1:
            return True
        clauses = [
            item.strip()
            for item in _CLAUSE_SPLIT.split(question)
            if item.strip() and _ACADEMIC_CLAUSE.search(item)
        ]
        if len(clauses) > 1:
            return True
        # A short "정리해 줘" turn is handled above using the current question.
        # Explicitly checking the prior user request here supports equivalent summary
        # wording while never treating the assistant answer as a factual source.
        if context.recent_messages and re.search(r"(?:각각|두\s*가지|여러\s*가지)", question):
            return True
        return False

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
        """Recover distinct recent topics within the configured evidence budget."""

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
            if len(recovered) == self.policy.max_subquestions:
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
                if match is None and _LIVE_INFORMATION.search(message.content):
                    topic_key = "live-information"
                elif match is None:
                    # Pronoun-only turns do not create another topic; their grounded
                    # subject is represented by the preceding explicit turn.
                    continue
                else:
                    topic_key = "requirement:" + re.sub(r"[ \t]", "", match.group(0))
            if topic_key in seen_topics or any(key == topic_key for key, _ in recent):
                continue
            recent.append((topic_key, message.content))
            if len(recovered) + len(recent) == self.policy.max_subquestions:
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
            explicit_courses
            and context.recent_course_codes
            and _COURSE_SUBSTITUTION.search(question)
        ):
            codes = tuple(
                dict.fromkeys(
                    (
                        *(item.course_code for item in explicit_courses),
                        *context.recent_course_codes,
                    )
                )
            )
            tools = tuple(
                dict.fromkeys(
                    (*plan.tools, ToolName.RESOLVE_COURSE, ToolName.QUERY_CURRICULUM)
                )
            )
            return _Plan(
                self._append_codes(question, codes),
                codes,
                tools,
                plan.topic,
                None,
                (),
            )
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
            not explicit_courses
            and context.recent_course_codes
            and _ACADEMIC_LIVE_CONTRAST.search(question)
        ):
            codes = plan.course_codes or context.recent_course_codes
            tools = tuple(
                dict.fromkeys(
                    (*plan.tools, ToolName.RESOLVE_COURSE, ToolName.QUERY_CURRICULUM)
                )
            )
            return _Plan(
                self._append_codes(question, tuple(codes)),
                tuple(codes),
                tools,
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
            if ungrounded or OutcomeStatus.INSUFFICIENT_EVIDENCE in statuses
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
        partial_limitations = tuple(
            dict.fromkeys(
                limitation
                for item in selected
                if item.outcome.status is OutcomeStatus.INSUFFICIENT_EVIDENCE
                for limitation in item.outcome.limitations
                if limitation not in message
            )
        )
        if partial_limitations:
            message = "\n\n".join((message, *partial_limitations))
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

    def _narrative(
        self,
        question: str,
        result: PersonalizedChatResult,
        *,
        sources: list[PersonalizedChatResult],
    ) -> tuple[str, dict[str, Any]]:
        if self.policy.mode is AgentMode.AGENTIC:
            return self._fact_packet_narrative(question, result, sources)
        return self._legacy_narrative(question, result)

    def _legacy_narrative(
        self, question: str, result: PersonalizedChatResult
    ) -> tuple[str, dict[str, Any]]:
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
                expanded=self.policy.mode is AgentMode.EXPANDED,
            )
            intro = self._safe_discourse(
                generation.payload.get("intro"), canonical, max_length=160
            )
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
        display = " ".join(item for item in (intro, core, closing) if item).strip()
        return display, {
            "packet_count": 1 if approved_fact_text else 0,
            "rewritten_sections": int(bool(rewritten and rewritten != approved_fact_text)),
            "canonical_fallback_sections": int(bool(approved_fact_text and not rewritten)),
            "repair_attempts": 0,
        }

    def _fact_packet_narrative(
        self,
        question: str,
        result: PersonalizedChatResult,
        sources: list[PersonalizedChatResult],
    ) -> tuple[str, dict[str, Any]]:
        canonical = result.outcome.message
        approved: list[PersonalizedChatResult] = []
        seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        for source in [*sources, result]:
            response = source.response
            if response.status is not ChatStatus.ANSWERABLE or not response._is_approved():
                continue
            signature = (response.used_fact_ids, response.used_evidence_ids)
            if signature in seen:
                continue
            seen.add(signature)
            approved.append(source)
            if len(approved) == 4:
                break
        if not approved:
            return canonical, {
                "packet_count": 0,
                "rewritten_sections": 0,
                "canonical_fallback_sections": 0,
                "repair_attempts": 0,
            }
        packets = [
            {
                "packet_id": f"fact:{index}",
                "approved_fact_text": source.response.answer_text,
                "claims": self._public_claim_packet(source.response),
                "citation_count": len(source.response.citations),
                "limitations": list(source.outcome.limitations),
            }
            for index, source in enumerate(approved, start=1)
        ]
        sections: dict[str, str] = {}
        intro = closing = ""
        repair_attempts = 0
        try:
            payload = self.client.generate_json(
                system_prompt=(
                    "FactPacket 각각의 approved_fact_text만 자연스러운 한국어로 다시 쓴다. "
                    "packet_id를 바꾸거나 합치지 않는다. 과목명, 학수번호, 숫자·단위, enum, "
                    "비교 연산자, 면제·의무·대체·인정 의미를 추가·삭제·교환하지 않는다. "
                    "FactPacket 밖 학교 규정과 계산을 만들지 않는다. 각 section이 안전하게 "
                    "재작성되지 않으면 해당 approved_fact_text를 그대로 반환한다. intro와 "
                    "closing은 새로운 사실이 없는 짧은 대화 연결 문구다."
                ),
                user_prompt=json.dumps(
                    {
                        "question": question,
                        "outcome_status": result.outcome.status.value,
                        "fact_packets": packets,
                    },
                    ensure_ascii=False,
                ),
                response_schema=_FACT_PACKET_NARRATIVE_SCHEMA,
            ).payload
            sections = self._validated_packet_sections(payload, approved)
            intro = self._safe_discourse(
                payload.get("intro"), canonical, max_length=160
            )
            closing = self._safe_discourse(payload.get("closing"), canonical)
        except (LLMResponseError, ValueError, TypeError):
            sections = {}
        missing = [
            index
            for index in range(1, len(approved) + 1)
            if f"fact:{index}" not in sections
        ]
        if missing and self.policy.max_narrative_repairs:
            repair_attempts = 1
            repair_packets = [packets[index - 1] for index in missing]
            try:
                repaired = self.client.generate_json(
                    system_prompt=(
                        "앞선 초안에서 검증되지 않은 FactPacket만 다시 쓴다. packet_id와 "
                        "approved_fact_text의 사실 토큰을 모두 유지하고 새로운 학교 규정, "
                        "계산, 조건을 추가하지 않는다. 안전한 재작성이 어렵다면 원문을 "
                        "그대로 반환한다."
                    ),
                    user_prompt=json.dumps(
                        {"question": question, "fact_packets": repair_packets},
                        ensure_ascii=False,
                    ),
                    response_schema=_FACT_PACKET_NARRATIVE_SCHEMA,
                ).payload
                repaired_sections = self._validated_packet_sections(
                    repaired,
                    approved,
                    allowed_indexes=set(missing),
                )
                sections.update(repaired_sections)
            except (LLMResponseError, ValueError, TypeError):
                pass
        core = canonical
        rewritten_count = 0
        fallback_count = 0
        for index, source in enumerate(approved, start=1):
            packet_id = f"fact:{index}"
            original = source.response.answer_text
            rewritten = sections.get(packet_id)
            replacement = rewritten or original
            if rewritten and rewritten != original:
                rewritten_count += 1
            elif not rewritten:
                fallback_count += 1
            if original in core:
                core = core.replace(original, replacement, 1)
        normalized_question = question.strip().rstrip(".?!？ ")
        if intro and normalized_question and normalized_question in intro.rstrip(".?!？ "):
            intro = ""
        display = " ".join(item for item in (intro, core, closing) if item).strip()
        return display, {
            "packet_count": len(approved),
            "rewritten_sections": rewritten_count,
            "canonical_fallback_sections": fallback_count,
            "repair_attempts": repair_attempts,
        }

    def _validated_packet_sections(
        self,
        payload: Mapping[str, Any],
        approved: list[PersonalizedChatResult],
        *,
        allowed_indexes: set[int] | None = None,
    ) -> dict[str, str]:
        raw_sections = payload.get("sections")
        if not isinstance(raw_sections, list) or len(raw_sections) > len(approved):
            raise ValueError("FactPacket sections are invalid")
        output: dict[str, str] = {}
        for raw in raw_sections:
            if not isinstance(raw, Mapping) or set(raw) != {"packet_id", "text"}:
                raise ValueError("FactPacket section contract is invalid")
            packet_id = raw.get("packet_id")
            match = re.fullmatch(r"fact:([1-4])", packet_id or "")
            if match is None:
                raise ValueError("FactPacket ID is invalid")
            index = int(match.group(1))
            if index > len(approved) or (
                allowed_indexes is not None and index not in allowed_indexes
            ):
                raise ValueError("FactPacket section is out of scope")
            if packet_id in output:
                raise ValueError("FactPacket section is duplicated")
            validated = self._validated_grounded_answer(
                raw.get("text"),
                approved[index - 1].response,
                expanded=True,
            )
            if validated:
                output[packet_id] = validated
        return output

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
        if isinstance(value, tuple) and all(
            isinstance(getattr(item, "display_name", None), str) for item in value
        ):
            return [
                {
                    "display_name": item.display_name,
                    "course_code": getattr(item, "course_code", None),
                    "credits": getattr(item, "credits", None),
                }
                for item in value
            ]
        return "APPROVED_COMPLEX_VALUE"

    @staticmethod
    def _validated_grounded_answer(
        value: Any,
        response: ChatResponse,
        *,
        expanded: bool = False,
    ) -> str:
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
        if not draft or not source or len(draft) > 5000 or draft == source:
            return source if draft == source else ""
        if len(draft) < max(8, int(len(source) * 0.55)) or len(draft) > len(source) * 1.65:
            return ""
        if _FORBIDDEN_NARRATIVE.search(draft) or any(ord(char) < 32 for char in draft):
            return ""
        claims = response.grounded_claims
        if not AgenticCurriculumChatService._rewritable_claims(
            claims, expanded=expanded
        ):
            return ""
        if Counter(_FACT_NUMBER.findall(draft)) != Counter(_FACT_NUMBER.findall(source)):
            return ""
        if Counter(_COURSE_CODE.findall(draft)) != Counter(_COURSE_CODE.findall(source)):
            return ""
        if Counter(_FACT_CONTROL_TOKEN.findall(draft)) != Counter(
            _FACT_CONTROL_TOKEN.findall(source)
        ):
            return ""
        if Counter(_ASSERTION_CONTROL_TOKEN.findall(draft)) != Counter(
            _ASSERTION_CONTROL_TOKEN.findall(source)
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
    def _rewritable_claims(
        claims: tuple[GroundedClaim, ...], *, expanded: bool = False
    ) -> bool:
        max_claims = 12 if expanded else 3
        if not claims or len(claims) > max_claims:
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
        if expanded and ClaimType.COURSE_LIST in kinds and kinds <= {
            ClaimType.COURSE_LIST,
            ClaimType.AGGREGATE,
            ClaimType.FIELD_VALUE,
        }:
            return all(
                claim.claim_type is not ClaimType.COURSE_LIST
                or (
                    isinstance(claim.value, tuple)
                    and 0 < len(claim.value) <= 20
                )
                for claim in claims
            )
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
        if claim.claim_type is ClaimType.COURSE_LIST:
            return all(
                isinstance(getattr(item, "display_name", None), str)
                and item.display_name in draft
                and (
                    getattr(item, "course_code", None) is None
                    or item.course_code in draft
                )
                and (
                    getattr(item, "credits", None) is None
                    or re.search(
                        rf"(?<!\d){re.escape(str(item.credits))}\s*학점", draft
                    )
                    is not None
                )
                for item in value
            ) if isinstance(value, tuple) else False
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
    def _safe_discourse(
        value: Any, canonical: str = "", *, max_length: int = 320
    ) -> str:
        if not isinstance(value, str):
            return ""
        text = value.strip()
        if not text or len(text) > max_length or not _SAFE_DISCOURSE.fullmatch(text):
            return ""
        if text in canonical:
            return ""
        # Narrative glue may not introduce factual tokens.  Numbers and curriculum
        # entities stay in the approved deterministic text.
        if (
            re.search(r"\d|학점|점수|학년|학기|과목|필수|선택|면제|졸업", text)
            or _FACT_CONTROL_TOKEN.search(text)
            or _ASSERTION_CONTROL_TOKEN.search(text)
            or _OUT_OF_SCOPE_CLAUSE.search(text)
        ):
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
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        event = AgentTraceEvent(
            sequence=len(trace) + 1,
            tool=tool,
            state=state,
            elapsed_ms=elapsed_ms,
            detail=_TOOL_DETAIL[tool],
            metadata=dict(metadata or {}),
        )
        trace.append(event)
        if callback is not None:
            callback(event)
