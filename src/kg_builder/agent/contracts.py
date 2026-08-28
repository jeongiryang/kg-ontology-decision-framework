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


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    """Turn-local budgets; this never weakens query or Evidence validation."""

    mode: AgentMode = AgentMode.CONSERVATIVE
    max_tool_calls: int = MAX_TOOL_CALLS
    max_kg_queries: int = MAX_KG_QUERIES_PER_TURN
    max_subquestions: int = 3
    max_turn_seconds: float = 120.0

    def __post_init__(self) -> None:
        ceilings = (
            (6, 6, 5, 150.0)
            if self.mode is AgentMode.EXPANDED
            else (MAX_TOOL_CALLS, MAX_KG_QUERIES_PER_TURN, 3, 120.0)
        )
        values = (
            self.max_tool_calls,
            self.max_kg_queries,
            self.max_subquestions,
            self.max_turn_seconds,
        )
        if not isinstance(self.mode, AgentMode) or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in values
        ):
            raise ValueError("agent policy is invalid")
        if any(value <= 0 or value > ceiling for value, ceiling in zip(values, ceilings)):
            raise ValueError("agent policy exceeds its bounded mode")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AgentPolicy":
        source = os.environ if environ is None else environ
        raw_mode = source.get("KG_AGENT_MODE", AgentMode.CONSERVATIVE.value)
        try:
            mode = AgentMode(raw_mode.strip().lower())
        except (AttributeError, ValueError) as exc:
            raise ValueError("KG_AGENT_MODE must be conservative or expanded") from exc
        if mode is AgentMode.EXPANDED:
            return cls(
                mode=mode,
                max_tool_calls=6,
                max_kg_queries=6,
                max_subquestions=5,
                max_turn_seconds=150.0,
            )
        return cls()


class ToolName(StrEnum):
    READ_USER_PROFILE = "read_user_profile"
    RESOLVE_COURSE = "resolve_course"
    QUERY_CURRICULUM = "query_curriculum"
    CALCULATE_REMAINING_CREDITS = "calculate_remaining_credits"
    ASK_CLARIFICATION = "ask_clarification"
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
        }


@dataclass(frozen=True, slots=True)
class AgentTraceEvent:
    sequence: int
    tool: ToolName
    state: str
    elapsed_ms: int
    detail: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "tool": self.tool.value,
            "state": self.state,
            "elapsed_ms": max(0, self.elapsed_ms),
            "detail": self.detail,
        }


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
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
