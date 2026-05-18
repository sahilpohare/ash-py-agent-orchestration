"""
Typed channel message contract.

ChannelMessage is what adapters receive in on_message().
Parts are discriminated unions — adapters pattern-match on type.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_KNOWN_PART_TYPES = {
    "text", "text_delta", "stream_end", "event",
    "response_request", "response_reply", "tool_call", "reasoning",
}


# ── Parts ─────────────────────────────────────────────────────────────────────

class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class TextDeltaPart(BaseModel):
    type: Literal["text_delta"]
    text: str


class StreamEndPart(BaseModel):
    type: Literal["stream_end"]


class EventPart(BaseModel):
    type: Literal["event"]
    event: str
    model_config = {"extra": "allow"}  # carries arbitrary kwargs (error, queue_position, etc.)


class ResponseRequestPart(BaseModel):
    type: Literal["response_request"]
    request_id: str
    prompt: str
    options: list[dict] | None = None
    multi_select: bool = False
    context: dict = Field(default_factory=dict)
    created_by: str = ""


class ResponseReplyPart(BaseModel):
    type: Literal["response_reply"]
    request_id: str
    selected: list[str] = Field(default_factory=list)


class ToolCallPart(BaseModel):
    type: Literal["tool_call"]
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class ReasoningPart(BaseModel):
    type: Literal["reasoning"]
    text: str


Part = Annotated[
    TextPart | TextDeltaPart | StreamEndPart | EventPart | ResponseRequestPart | ResponseReplyPart | ToolCallPart | ReasoningPart,
    Field(discriminator="type"),
]


# ── Message ───────────────────────────────────────────────────────────────────

class ChannelMessage(BaseModel):
    thread_id: str
    participant_id: str
    participant_type: str   # HUMAN | AGENT | SYSTEM
    role: str               # USER | ASSISTANT | SYSTEM
    parts: list[Part] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, message: dict) -> ChannelMessage:
        content = message.get("content", {})
        raw_parts = content.get("parts", []) if isinstance(content, dict) else []
        parsed_parts = []
        for p in raw_parts:
            try:
                parsed_parts.append(_parse_part(p))
            except ValueError:
                pass  # unknown part type — silently drop
            except Exception:
                if p.get("type") in _KNOWN_PART_TYPES:
                    logger.warning("failed to parse %r part: %r", p.get("type"), p)
                # unknown type with parse error — drop silently
        return cls(
            thread_id=message.get("thread_id", ""),
            participant_id=message.get("participant_id", ""),
            participant_type=message.get("participant_type", ""),
            role=message.get("role", ""),
            parts=parsed_parts,
        )


def _parse_part(raw: dict) -> Any:
    t = raw.get("type", "")
    if t == "text":
        return TextPart(**raw)
    if t == "text_delta":
        return TextDeltaPart(**raw)
    if t == "stream_end":
        return StreamEndPart(**raw)
    if t == "event":
        return EventPart(**raw)
    if t == "response_request":
        return ResponseRequestPart(**raw)
    if t == "response_reply":
        return ResponseReplyPart(**raw)
    if t == "tool_call":
        return ToolCallPart(**raw)
    if t == "reasoning":
        return ReasoningPart(**raw)
    raise ValueError(f"Unknown part type: {t!r}")
