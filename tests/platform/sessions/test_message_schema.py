"""
Tests for ADR-2, ADR-5, ADR-7, ADR-13.

Preconditions, invariants, and postconditions are stated per test.
No DB, no Restate — pure Python.

ADR-2  All writes are upserts.
       _content_key() is pure: same content → same key; different → different.
       pk, timestamps, _idempotency_key excluded from hash.

ADR-5  Idempotency key is caller-supplied.
       Message declares UNIQUE(thread_id, idempotency_key) in __table_args__.
       add_message() stores the caller-supplied key unchanged.

ADR-7  create() must not overwrite a pre-set id.
       Thread.create() assigns a new id only when id is unset.
       When id is already set (derive layer sets it before calling create),
       create() must leave it unchanged.

ADR-13 Content uses versioned parts model.
       All 8 known part types parse correctly via ChannelMessage.from_dict().
       Unknown part types are silently dropped.
"""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import UniqueConstraint

from ironbridge.platform.channels.message import (
    ChannelMessage,
    EventPart,
    ReasoningPart,
    ResponseReplyPart,
    ResponseRequestPart,
    StreamEndPart,
    TextDeltaPart,
    TextPart,
    ToolCallPart,
)
from ironbridge.platform.sessions.message import Message
from ironbridge.platform.sessions.thread import Thread
from ironbridge.shared.derive.repository import _content_key
from ironbridge.shared.framework.effects import ActionContext


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_thread(id: str = "t1") -> Thread:
    t = Thread()
    t.id = id
    t.tenant_id = None
    return t


def _msg_from_parts(parts: list) -> ChannelMessage:
    return ChannelMessage.from_dict({
        "thread_id": "t1",
        "participant_id": "alice",
        "participant_type": "HUMAN",
        "role": "USER",
        "content": {"version": 1, "parts": parts},
    })


# ── ADR-2: _content_key — pure function contracts ─────────────────────────────

def test_content_key_is_deterministic():
    """
    Pre:  same values dict passed twice
    Inv:  _content_key is pure — no randomness, no side effects
    Post: both calls return identical strings
    """
    values = {"thread_id": "t1", "role": "USER", "position": 1}
    assert _content_key(values, pk="id") == _content_key(values, pk="id")


def test_content_key_returns_sha256_hex():
    """
    Pre:  any non-empty values dict
    Post: result is a 64-character lowercase hex string (SHA-256 output)
    """
    key = _content_key({"role": "USER", "content": "hello"}, pk="id")
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_content_key_differs_on_different_content():
    """
    Pre:  two dicts with different meaningful values
    Post: keys differ — distinct messages must not share an idempotency key
    """
    v1 = {"role": "USER", "content": "hello"}
    v2 = {"role": "USER", "content": "world"}
    assert _content_key(v1, pk="id") != _content_key(v2, pk="id")


def test_content_key_excludes_pk():
    """
    Inv:  pk column is excluded from hash — same content, different pk → same key.
          Two rows with identical content but different primary keys are the same
          logical message; the upsert must deduplicate them.
    Pre:  two dicts differing only in the pk field ("id")
    Post: keys are equal
    """
    v1 = {"id": "aaa", "role": "USER", "content": "hello"}
    v2 = {"id": "bbb", "role": "USER", "content": "hello"}
    assert _content_key(v1, pk="id") == _content_key(v2, pk="id")


def test_content_key_excludes_created_at():
    """
    Inv:  created_at excluded — same message written at different times is
          still the same logical message.
    Pre:  two dicts differing only in created_at
    Post: keys are equal
    """
    v1 = {"role": "USER", "content": "hi", "created_at": datetime.datetime(2024, 1, 1)}
    v2 = {"role": "USER", "content": "hi", "created_at": datetime.datetime(2025, 6, 1)}
    assert _content_key(v1, pk="id") == _content_key(v2, pk="id")


def test_content_key_excludes_updated_at():
    """
    Inv:  updated_at excluded (same reasoning as created_at).
    Pre:  two dicts differing only in updated_at
    Post: keys are equal
    """
    v1 = {"role": "USER", "content": "hi", "updated_at": datetime.datetime(2024, 1, 1)}
    v2 = {"role": "USER", "content": "hi", "updated_at": datetime.datetime(2025, 6, 1)}
    assert _content_key(v1, pk="id") == _content_key(v2, pk="id")


def test_content_key_excludes_idempotency_key_column():
    """
    Inv:  _idempotency_key column itself is excluded — avoids circular dependency
          where the hash input contains the hash output.
    Pre:  two dicts differing only in _idempotency_key value
    Post: keys are equal
    """
    v1 = {"role": "USER", "_idempotency_key": "old"}
    v2 = {"role": "USER", "_idempotency_key": "new"}
    assert _content_key(v1, pk="id") == _content_key(v2, pk="id")


# ── ADR-5: Message schema — caller-supplied idempotency key ───────────────────

def test_message_has_unique_thread_idempotency_constraint():
    """
    Pre:  Message model loaded
    Inv:  UniqueConstraint("thread_id", "idempotency_key") declared in __table_args__
    Post: constraint found with exactly those two columns
    """
    col_sets = [
        frozenset(col.key for col in c.columns)
        for c in Message.__table_args__
        if isinstance(c, UniqueConstraint)
    ]
    assert frozenset({"thread_id", "idempotency_key"}) in col_sets, (
        "ADR-5: Message must declare UNIQUE(thread_id, idempotency_key)"
    )


def test_add_message_stores_caller_supplied_idempotency_key():
    """
    Pre:  caller supplies idempotency_key="my-stable-key"
    Inv:  add_message() stores it unchanged — no hashing, no modification
    Post: msg.idempotency_key == "my-stable-key"
    """
    t = _make_thread()
    msg = t.add_message(
        action_ctx=ActionContext(),
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": []},
        idempotency_key="my-stable-key",
    )
    assert msg.idempotency_key == "my-stable-key"


def test_two_messages_have_independent_idempotency_keys():
    """
    Pre:  two add_message() calls with different keys on the same thread
    Post: each message retains its own supplied key — no cross-contamination
    """
    t = _make_thread()
    ctx = ActionContext()
    m1 = t.add_message(action_ctx=ctx, participant_id="a", participant_type="HUMAN",
                       role="USER", content={"version": 1, "parts": []}, idempotency_key="k1")
    m2 = t.add_message(action_ctx=ctx, participant_id="a", participant_type="HUMAN",
                       role="USER", content={"version": 1, "parts": []}, idempotency_key="k2")
    assert m1.idempotency_key == "k1"
    assert m2.idempotency_key == "k2"
    assert m1.idempotency_key != m2.idempotency_key


# ── ADR-7: create() must not overwrite a pre-set id ───────────────────────────

def test_create_assigns_id_when_unset():
    """
    Pre:  Thread with no id (empty string / falsy)
    Post: create() assigns a non-empty id
    """
    t = Thread()
    assert not t.id
    t.create()
    assert t.id


def test_create_does_not_overwrite_preset_id():
    """
    Pre:  Thread.id already set — simulates derive/restate.py doing
          `instance.id = ctx.key()` before calling create()
    Inv:  create() MUST NOT overwrite the pre-set id (ADR-7)
    Post: t.id == "pre-set-id" after create()
    """
    t = Thread()
    t.id = "pre-set-id-from-restate"
    t.create()
    assert t.id == "pre-set-id-from-restate"


def test_create_returns_self_when_id_preset():
    """
    Pre:  Thread with id pre-set
    Post: create() returns self; returned object id matches preset
    """
    t = Thread()
    t.id = "stable-id"
    result = t.create()
    assert result is t
    assert result.id == "stable-id"


# ── ADR-13: All 8 known part types parse via ChannelMessage.from_dict ─────────

def test_part_text_parses():
    """Pre: text part. Post: TextPart with correct text field."""
    msg = _msg_from_parts([{"type": "text", "text": "hello"}])
    assert len(msg.parts) == 1
    assert isinstance(msg.parts[0], TextPart)
    assert msg.parts[0].text == "hello"


def test_part_text_delta_parses():
    """Pre: text_delta part. Post: TextDeltaPart with text field."""
    msg = _msg_from_parts([{"type": "text_delta", "text": "delta"}])
    assert isinstance(msg.parts[0], TextDeltaPart)
    assert msg.parts[0].text == "delta"


def test_part_stream_end_parses():
    """Pre: stream_end part (no extra fields). Post: StreamEndPart."""
    msg = _msg_from_parts([{"type": "stream_end"}])
    assert isinstance(msg.parts[0], StreamEndPart)


def test_part_event_parses():
    """Pre: event part with event field. Post: EventPart with event string."""
    msg = _msg_from_parts([{"type": "event", "event": "AGENT_RUN_STARTED"}])
    assert isinstance(msg.parts[0], EventPart)
    assert msg.parts[0].event == "AGENT_RUN_STARTED"


def test_part_event_preserves_extra_fields():
    """
    Inv:  EventPart.model_config = extra='allow' — arbitrary kwargs preserved.
    Pre:  event part with step and error extra fields
    Post: extra fields accessible on parsed part
    """
    msg = _msg_from_parts([{
        "type": "event", "event": "AGENT_RUN_RETRY",
        "step": "llm_call_0", "error": "timeout",
    }])
    part = msg.parts[0]
    assert isinstance(part, EventPart)
    assert part.step == "llm_call_0"   # type: ignore[attr-defined]
    assert part.error == "timeout"     # type: ignore[attr-defined]


def test_part_response_request_parses():
    """Pre: response_request part with required fields. Post: ResponseRequestPart."""
    msg = _msg_from_parts([{
        "type": "response_request",
        "request_id": "req-1",
        "prompt": "Allow this?",
        "options": [{"id": "approve", "label": "Approve"}],
    }])
    part = msg.parts[0]
    assert isinstance(part, ResponseRequestPart)
    assert part.request_id == "req-1"
    assert part.prompt == "Allow this?"
    assert len(part.options) == 1


def test_part_response_reply_parses():
    """Pre: response_reply part. Post: ResponseReplyPart with selected list."""
    msg = _msg_from_parts([{
        "type": "response_reply",
        "request_id": "req-1",
        "selected": ["approve"],
    }])
    part = msg.parts[0]
    assert isinstance(part, ResponseReplyPart)
    assert part.request_id == "req-1"
    assert part.selected == ["approve"]


def test_part_tool_call_parses():
    """Pre: tool_call part with id, name, arguments. Post: ToolCallPart."""
    msg = _msg_from_parts([{
        "type": "tool_call",
        "id": "tc-1",
        "name": "search",
        "arguments": {"query": "test"},
    }])
    part = msg.parts[0]
    assert isinstance(part, ToolCallPart)
    assert part.id == "tc-1"
    assert part.name == "search"
    assert part.arguments == {"query": "test"}


def test_part_reasoning_parses():
    """Pre: reasoning part. Post: ReasoningPart with text."""
    msg = _msg_from_parts([{"type": "reasoning", "text": "thinking..."}])
    part = msg.parts[0]
    assert isinstance(part, ReasoningPart)
    assert part.text == "thinking..."


def test_content_version_preserved_in_message():
    """
    Inv:  content dict carries version key (ADR-13 versioned parts model).
    Pre:  add_message() called with {"version": 1, "parts": [...]}
    Post: msg.content["version"] == 1
    """
    t = _make_thread("t-version")
    msg = t.add_message(
        action_ctx=ActionContext(),
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hi"}]},
        idempotency_key="k",
    )
    assert msg.content["version"] == 1


def test_multiple_parts_all_parsed():
    """
    Pre:  two valid parts of different types in one message
    Post: both parsed; count == 2; types correct; order preserved
    """
    msg = _msg_from_parts([
        {"type": "text", "text": "hello"},
        {"type": "event", "event": "AGENT_RUN_STARTED"},
    ])
    assert len(msg.parts) == 2
    assert isinstance(msg.parts[0], TextPart)
    assert isinstance(msg.parts[1], EventPart)


def test_unknown_part_type_dropped_silently():
    """
    Inv:  unknown part types are silently dropped — no exception raised.
          Enables forward compatibility as new part types are added.
    Pre:  one valid text part + one unknown type
    Post: only the valid part survives; len == 1
    """
    msg = _msg_from_parts([
        {"type": "text", "text": "valid"},
        {"type": "future_part_v99", "data": "x"},
    ])
    assert len(msg.parts) == 1
    assert isinstance(msg.parts[0], TextPart)
    assert msg.parts[0].text == "valid"
