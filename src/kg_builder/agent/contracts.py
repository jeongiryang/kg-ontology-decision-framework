"""Strict contracts for the bounded GraphRAG conversation layer.

These objects are presentation and orchestration contracts.  They never replace the
sealed :class:`ChatResponse`, and an earlier assistant message is never accepted as
school-rule Evidence.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from kg_builder.answer.contracts import ChatResponse
from kg_builder.answer.personalized_service import PersonalizedChatResult


CONVERSATION_VERSION = 1
REQUEST_FULFILLMENT_VERSION = 1
MAX_RECENT_MESSAGES = 8
MAX_MESSAGE_CHARS = 4_000
MAX_SUMMARY_CHARS = 1_200
MAX_TOOL_CALLS = 4
MAX_KG_QUERIES_PER_TURN = 4
_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{7,127}\Z")


class ConversationRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class AgentMode(StrEnum):
    """How much bounded exploration the existing grounded pipeline may perform."""

    CONSERVATIVE = "conservative"
    EXPANDED = "expanded"
    AGENTIC = "agentic"


class RequestAction(StrEnum):
    LIST_COURSES = "list_courses"
    LOOKUP_COURSE = "lookup_course"
    LOOKUP_REQUIREMENT = "lookup_requirement"
    CHECK_ELIGIBILITY = "check_eligibility"
    CALCULATE_REMAINING = "calculate_remaining"
    RECOMMEND_COURSES = "recommend_courses"
    SOCIAL = "social"
    OTHER = "other"


class RequestedItemStatus(StrEnum):
    ANSWERED = "ANSWERED"
    NEEDS_USER_INFO = "NEEDS_USER_INFO"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class TurnFulfillmentStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class RequestedItem:
    """One normalized user request, without copying the raw question."""

    item_id: str
    action: RequestAction
    filters: Mapping[str, Any] = field(default_factory=dict)
    scope: str = "FILTERED"
    group_by: tuple[str, ...] = ()
    display_fields: tuple[str, ...] = ()
    status: RequestedItemStatus | None = None
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"item:[1-9][0-9]{0,2}", self.item_id):
            raise ValueError("requested item ID is invalid")
        if self.scope not in {"ALL", "FILTERED"}:
            raise ValueError("requested item scope is invalid")
        allowed_filters = {
            "academic_year",
            "department_id",
            "completion_type",
            "grade_year",
            "semester",
            "area_id",
            "area_ids",
            "course_codes",
        }
        if not set(self.filters).issubset(allowed_filters):
            raise ValueError("requested item filters are invalid")
        if len(self.filters) > 8 or len(self.group_by) > 4 or len(self.display_fields) > 8:
            raise ValueError("requested item exceeds its bound")
        for key, value in self.filters.items():
            if key in {"academic_year", "grade_year"}:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("requested item numeric filter is invalid")
            elif key in {"area_ids", "course_codes"}:
                if (
                    not isinstance(value, (list, tuple))
                    or not value
                    or len(value) > 32
                    or any(
                        not isinstance(item, str)
                        or not item
                        or len(item) > 96
                        for item in value
                    )
                ):
                    raise ValueError("requested item collection filter is invalid")
            elif not isinstance(value, str) or not value or len(value) > 96:
                raise ValueError("requested item string filter is invalid")
        for collection in (self.group_by, self.display_fields):
            if any(not isinstance(value, str) or not value or len(value) > 64 for value in collection):
                raise ValueError("requested item field is invalid")
        if self.reason_code is not None and not re.fullmatch(
            r"[A-Z][A-Z0-9_]{2,63}", self.reason_code
        ):
            raise ValueError("requested item reason code is invalid")

    def with_status(
        self, status: RequestedItemStatus, reason_code: str | None = None
    ) -> "RequestedItem":
        return RequestedItem(
            self.item_id,
            self.action,
            dict(self.filters),
            self.scope,
            self.group_by,
            self.display_fields,
            status,
            reason_code,
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "action": self.action.value,
            "filters": dict(self.filters),
            "scope": self.scope,
            "group_by": list(self.group_by),
            "display_fields": list(self.display_fields),
            "status": self.status.value if self.status else None,
            "reason_code": self.reason_code,
        }

    @classmethod
    def from_payload(cls, value: Any) -> "RequestedItem":
        if not isinstance(value, Mapping) or set(value) != {
            "item_id",
            "action",
            "filters",
            "scope",
            "group_by",
            "display_fields",
            "status",
            "reason_code",
        }:
            raise ValueError("pending requested item is invalid")
        status = value.get("status")
        return cls(
            str(value.get("item_id")),
            RequestAction(value.get("action")),
            dict(value.get("filters") or {}),
            str(value.get("scope")),
            tuple(value.get("group_by") or ()),
            tuple(value.get("display_fields") or ()),
            RequestedItemStatus(status) if status is not None else None,
            value.get("reason_code"),
        )


@dataclass(frozen=True, slots=True)
class PendingRequest:
    items: tuple[RequestedItem, ...]

    def __post_init__(self) -> None:
        if not self.items or len(self.items) > 6:
            raise ValueError("pending request item count is invalid")

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "version": REQUEST_FULFILLMENT_VERSION,
            "items": [item.to_public_dict() for item in self.items],
        }

    @classmethod
    def from_payload(cls, value: Any) -> "PendingRequest | None":
        if value is None:
            return None
        if not isinstance(value, Mapping) or set(value) != {"version", "items"}:
            raise ValueError("pending request is invalid")
        if value.get("version") != REQUEST_FULFILLMENT_VERSION:
            raise ValueError("pending request version is invalid")
        raw_items = value.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("pending request items are invalid")
        return cls(tuple(RequestedItem.from_payload(item) for item in raw_items))


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    """Turn-local budgets; this never weakens query or Evidence validation."""

    mode: AgentMode = AgentMode.CONSERVATIVE
    max_tool_calls: int = MAX_TOOL_CALLS
    max_kg_queries: int = MAX_KG_QUERIES_PER_TURN
    max_subquestions: int = 3
    max_turn_seconds: float = 120.0
    max_iterations: int = 1
    max_narrative_repairs: int = 0

    def __post_init__(self) -> None:
        ceilings = {
            AgentMode.CONSERVATIVE: (MAX_TOOL_CALLS, MAX_KG_QUERIES_PER_TURN, 3, 120.0, 1, 0),
            AgentMode.EXPANDED: (6, 6, 5, 150.0, 1, 0),
            AgentMode.AGENTIC: (6, 6, 4, 180.0, 4, 1),
        }.get(self.mode)
        values = (
            self.max_tool_calls,
            self.max_kg_queries,
            self.max_subquestions,
            self.max_turn_seconds,
            self.max_iterations,
            self.max_narrative_repairs,
        )
        if ceilings is None or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise ValueError("agent policy is invalid")
        if any(
            value < 0 or value > ceiling or (index < 5 and value == 0)
            for index, (value, ceiling) in enumerate(zip(values, ceilings))
        ):
            raise ValueError("agent policy exceeds its bounded mode")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AgentPolicy":
        source = os.environ if environ is None else environ
        raw_mode = source.get("KG_AGENT_MODE", AgentMode.AGENTIC.value)
        try:
            mode = AgentMode(raw_mode.strip().lower())
        except (AttributeError, ValueError) as exc:
            raise ValueError(
                "KG_AGENT_MODE must be conservative, expanded, or agentic"
            ) from exc
        if mode is AgentMode.EXPANDED:
            return cls(
                mode=mode,
                max_tool_calls=6,
                max_kg_queries=6,
                max_subquestions=5,
                max_turn_seconds=150.0,
            )
        if mode is AgentMode.AGENTIC:
            return cls(
                mode=mode,
                max_tool_calls=6,
                max_kg_queries=6,
                max_subquestions=4,
                max_turn_seconds=180.0,
                max_iterations=4,
                max_narrative_repairs=1,
            )
        return cls()


class ToolName(StrEnum):
    READ_USER_PROFILE = "read_user_profile"
    RESOLVE_COURSE = "resolve_course"
    QUERY_CURRICULUM = "query_curriculum"
    CALCULATE_REMAINING_CREDITS = "calculate_remaining_credits"
    ASK_CLARIFICATION = "ask_clarification"
    ASSESS_EVIDENCE = "assess_evidence"
    GROUNDED_NARRATIVE = "grounded_narrative"


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    turn_id: str
    role: ConversationRole
    content: str
    created_at: str
    response_status: str | None = None
    citation_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "ConversationMessage":
        turn_id = value.get("turn_id")
        content = value.get("content")
        created_at = value.get("created_at")
        try:
            role = ConversationRole(value.get("role"))
        except ValueError as exc:
            raise ValueError("conversation message role is invalid") from exc
        if not isinstance(turn_id, str) or not _OPAQUE_ID.fullmatch(turn_id):
            raise ValueError("conversation turn_id is invalid")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_MESSAGE_CHARS:
            raise ValueError("conversation message content is invalid")
        if not isinstance(created_at, str) or len(created_at) > 40:
            raise ValueError("conversation message created_at is invalid")
        # Validate the timestamp but do not trust it for ordering or authorization.
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("conversation message timestamp is invalid") from exc
        status = value.get("response_status")
        if status is not None and (not isinstance(status, str) or len(status) > 80):
            raise ValueError("conversation response status is invalid")
        return cls(
            turn_id=turn_id,
            role=role,
            content=content.strip(),
            created_at=created_at,
            response_status=status,
            citation_ids=_safe_ids(value.get("citation_ids")),
            evidence_ids=_safe_ids(value.get("evidence_ids")),
        )


@dataclass(frozen=True, slots=True)
class ConversationContext:
    conversation_id: str
    turn_id: str
    recent_messages: tuple[ConversationMessage, ...] = ()
    summary: str = ""
    current_topic: str | None = None
    recent_course_codes: tuple[str, ...] = ()
    recent_evidence_ids: tuple[str, ...] = ()
    pending_clarification: Mapping[str, Any] | None = None
    pending_request: PendingRequest | None = None
    version: int = CONVERSATION_VERSION

    @classmethod
    def from_payload(cls, value: Any) -> "ConversationContext | None":
        if value is None:
            return None
        if not isinstance(value, Mapping) or value.get("version") != CONVERSATION_VERSION:
            raise ValueError("conversation payload version is invalid")
        allowed = {
            "version",
            "conversation_id",
            "turn_id",
            "recent_messages",
            "summary",
            "current_topic",
            "recent_course_codes",
            "recent_evidence_ids",
            "pending_clarification",
            "pending_request",
        }
        if not set(value).issubset(allowed):
            raise ValueError("conversation payload contains unsupported fields")
        conversation_id = value.get("conversation_id")
        turn_id = value.get("turn_id")
        if not isinstance(conversation_id, str) or not _OPAQUE_ID.fullmatch(conversation_id):
            raise ValueError("conversation_id is invalid")
        if not isinstance(turn_id, str) or not _OPAQUE_ID.fullmatch(turn_id):
            raise ValueError("turn_id is invalid")
        raw_messages = value.get("recent_messages", ())
        if not isinstance(raw_messages, list) or len(raw_messages) > MAX_RECENT_MESSAGES:
            raise ValueError("recent_messages exceeds the context bound")
        messages = tuple(ConversationMessage.from_payload(item) for item in raw_messages)
        summary = value.get("summary", "")
        if not isinstance(summary, str) or len(summary) > MAX_SUMMARY_CHARS:
            raise ValueError("conversation summary is invalid")
        topic = value.get("current_topic")
        if topic is not None and (not isinstance(topic, str) or len(topic) > 160):
            raise ValueError("conversation topic is invalid")
        pending = value.get("pending_clarification")
        if pending is not None:
            if not isinstance(pending, Mapping) or set(pending) != {"prompt"}:
                raise ValueError("pending clarification is invalid")
            prompt = pending.get("prompt")
            if (
                not isinstance(prompt, str)
                or not prompt.strip()
                or len(prompt) > 500
                or any(ord(char) < 32 and char not in "\n\t" for char in prompt)
            ):
                raise ValueError("pending clarification prompt is invalid")
        return cls(
            conversation_id=conversation_id,
            turn_id=turn_id,
            recent_messages=messages,
            summary=summary.strip(),
            current_topic=topic.strip() if isinstance(topic, str) and topic.strip() else None,
            recent_course_codes=_safe_course_codes(value.get("recent_course_codes")),
            recent_evidence_ids=_safe_ids(value.get("recent_evidence_ids")),
            pending_clarification=(
                {"prompt": pending["prompt"].strip()} if pending is not None else None
            ),
            pending_request=PendingRequest.from_payload(value.get("pending_request")),
        )

    def prompt_context(self) -> dict[str, Any]:
        """Return bounded context; assistant text is context, never Evidence."""

        return {
            "summary": self.summary,
            "current_topic": self.current_topic,
            "recent_course_codes": list(self.recent_course_codes),
            "recent_messages": [
                {"role": item.role.value, "content": item.content}
                for item in self.recent_messages
            ],
            "pending_clarification": self.pending_clarification,
            "pending_request": (
                self.pending_request.to_public_dict() if self.pending_request else None
            ),
        }


@dataclass(frozen=True, slots=True)
class AgentTraceEvent:
    sequence: int
    tool: ToolName
    state: str
    elapsed_ms: int
    detail: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "sequence": self.sequence,
            "tool": self.tool.value,
            "state": self.state,
            "elapsed_ms": max(0, self.elapsed_ms),
            "detail": self.detail,
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass(frozen=True, slots=True)
class AgentChatResult:
    personalized: PersonalizedChatResult
    conversation_id: str
    turn_id: str
    display_answer: str
    trace: tuple[AgentTraceEvent, ...]
    summary: str
    current_topic: str | None
    recent_course_codes: tuple[str, ...]
    requested_items: tuple[RequestedItem, ...] = ()
    fulfillment_status: TurnFulfillmentStatus = TurnFulfillmentStatus.COMPLETE
    pending_request: PendingRequest | None = None

    @property
    def response(self) -> ChatResponse:
        return self.personalized.response

    def conversation_update(self) -> dict[str, Any]:
        response = self.response
        return {
            "type": "conversation_update",
            "version": CONVERSATION_VERSION,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "display_answer": self.display_answer,
            "summary": self.summary,
            "current_topic": self.current_topic,
            "recent_course_codes": list(self.recent_course_codes),
            "response_status": self.personalized.outcome.status.value,
            "citation_ids": list(response.used_evidence_ids),
            "evidence_ids": list(response.used_evidence_ids),
            "pending_clarification": (
                {"prompt": response.clarification}
                if response.clarification
                else None
            ),
            "pending_request": (
                self.pending_request.to_public_dict() if self.pending_request else None
            ),
            "request_fulfillment": self.request_fulfillment_update(),
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    def request_fulfillment_update(self) -> dict[str, Any]:
        return {
            "type": "request_fulfillment",
            "version": REQUEST_FULFILLMENT_VERSION,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "status": self.fulfillment_status.value,
            "requested_items": [item.to_public_dict() for item in self.requested_items],
        }


def _safe_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 100:
        raise ValueError("identifier collection is invalid")
    output: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _OPAQUE_ID.fullmatch(item):
            raise ValueError("identifier is invalid")
        if item not in output:
            output.append(item)
    return tuple(output)


def _safe_course_codes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > 20:
        raise ValueError("recent course codes are invalid")
    output: list[str] = []
    for item in value:
        if not isinstance(item, str) or not re.fullmatch(r"[A-Z0-9_-]{2,32}", item):
            raise ValueError("recent course code is invalid")
        if item not in output:
            output.append(item)
    return tuple(output)
