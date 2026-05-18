"""
Domain tests for Thread and Message.

No DB, no Restate. Pure Python — tests that the domain model
behaves correctly in isolation.
"""

import pytest

from ironbridge.platform.sessions.message import Message, MessageRole, ParticipantType
from ironbridge.platform.sessions.thread import Thread
from ironbridge.shared.framework.effects import ActionContext


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_thread(id: str = "thread-1") -> Thread:
    t = Thread()
    t.id = id
    t.tenant_id = None  # no DB lookups in unit tests
    return t


def noop_ctx() -> ActionContext:
    return ActionContext()


# ── Thread.create ─────────────────────────────────────────────────────────────

def test_create_assigns_id():
    t = Thread()
    result = t.create()
    assert result.id
    assert len(result.id) > 0


def test_create_returns_self():
    t = Thread()
    result = t.create()
    assert result is t


# ── Thread.add_message ────────────────────────────────────────────────────────

def test_add_message_returns_message():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hello"}]},
        idempotency_key="key-1",
    )
    assert isinstance(msg, Message)


def test_add_message_sets_thread_id():
    t = make_thread("thread-abc")
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hello"}]},
        idempotency_key="key-1",
    )
    assert msg.thread_id == "thread-abc"


def test_add_message_sets_participant():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hello"}]},
        idempotency_key="key-1",
    )
    assert msg.participant_id == "alice"
    assert msg.participant_type == ParticipantType.HUMAN


def test_add_message_sets_role():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hello"}]},
        idempotency_key="key-1",
    )
    assert msg.role == MessageRole.USER


def test_add_message_sets_content():
    t = make_thread()
    content = {"version": 1, "parts": [{"type": "text", "text": "hello"}]}
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content=content,
        idempotency_key="key-1",
    )
    assert msg.content == content


def test_add_message_sets_idempotency_key():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hello"}]},
        idempotency_key="my-key",
    )
    assert msg.idempotency_key == "my-key"


def test_add_message_position_sentinel():
    # position = -1 at domain level — assigned by derive layer
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hello"}]},
        idempotency_key="key-1",
    )
    assert msg.position == -1


def test_add_message_raw_response_defaults_none():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="agent-1",
        participant_type="AGENT",
        role="ASSISTANT",
        content={"version": 1, "parts": [{"type": "text", "text": "response"}]},
        idempotency_key="key-1",
    )
    assert msg.raw_response is None


def test_add_message_raw_response_stored():
    t = make_thread()
    raw = {"id": "chatcmpl-123", "model": "gpt-4o", "usage": {"prompt_tokens": 10}}
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="agent-1",
        participant_type="AGENT",
        role="ASSISTANT",
        content={"version": 1, "parts": [{"type": "text", "text": "response"}]},
        idempotency_key="key-1",
        raw_response=raw,
    )
    assert msg.raw_response == raw


def test_add_message_agent_participant_type():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="agent-run-xyz",
        participant_type="AGENT",
        role="ASSISTANT",
        content={"version": 1, "parts": [{"type": "text", "text": "done"}]},
        idempotency_key="key-1",
    )
    assert msg.participant_type == ParticipantType.AGENT


def test_add_message_system_role():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="system",
        participant_type="SYSTEM",
        role="SYSTEM",
        content={"version": 1, "parts": [{"type": "event", "event": "AGENT_RUN_STARTED"}]},
        idempotency_key="key-1",
    )
    assert msg.role == MessageRole.SYSTEM
    assert msg.participant_type == ParticipantType.SYSTEM


def test_add_message_invalid_role_raises():
    t = make_thread()
    with pytest.raises(ValueError):
        t.add_message(action_ctx=noop_ctx(), 
            participant_id="alice",
            participant_type="HUMAN",
            role="INVALID",
            content={"version": 1, "parts": []},
            idempotency_key="key-1",
        )


def test_add_message_invalid_participant_type_raises():
    t = make_thread()
    with pytest.raises(ValueError):
        t.add_message(action_ctx=noop_ctx(), 
            participant_id="alice",
            participant_type="ROBOT",
            role="USER",
            content={"version": 1, "parts": []},
            idempotency_key="key-1",
        )


def test_add_message_generates_id():
    t = make_thread()
    msg = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hi"}]},
        idempotency_key="key-1",
    )
    assert msg.id
    assert len(msg.id) > 0


def test_add_message_ids_are_unique():
    t = make_thread()
    msg1 = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hi"}]},
        idempotency_key="key-1",
    )
    msg2 = t.add_message(action_ctx=noop_ctx(), 
        participant_id="alice",
        participant_type="HUMAN",
        role="USER",
        content={"version": 1, "parts": [{"type": "text", "text": "hi again"}]},
        idempotency_key="key-2",
    )
    assert msg1.id != msg2.id


# ── Thread.get ────────────────────────────────────────────────────────────────

def test_get_returns_self():
    t = make_thread()
    result = t.get()
    assert isinstance(result, dict)
    assert result["id"] == t.id
    assert "messages" in result


# ── Action metadata ───────────────────────────────────────────────────────────

def test_thread_has_expected_actions():
    actions = set(Thread.__actions__.keys())
    assert "create" in actions
    assert "add_message" in actions
    assert "get" in actions
    assert "observe" in actions


def test_add_message_is_action():
    from ironbridge.shared.framework.actions import ActionKind
    assert Thread.__actions__["add_message"].kind == ActionKind.ACTION


def test_get_is_read():
    from ironbridge.shared.framework.actions import ActionKind
    assert Thread.__actions__["get"].kind == ActionKind.READ


def test_observe_is_stream():
    from ironbridge.shared.framework.actions import ActionKind
    assert Thread.__actions__["observe"].kind == ActionKind.STREAM
