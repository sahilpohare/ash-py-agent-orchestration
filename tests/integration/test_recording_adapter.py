"""
RecordingAdapter unit tests.

Preconditions (stated per test via comments):
  - adapter fixture provides a clean singleton with no recorded messages
  - ChannelMessage.from_dict() parses known part types; silently drops unknown types
  - _call_llm(history) returns None for empty history, dict otherwise

Invariants:
  - RecordingAdapter.install() returns the same singleton on every call
  - get_adapter("recording") always returns the singleton after first install()
  - on_message() never raises — it only appends
  - received() never mutates internal state — returns a copy
  - clear() leaves received() returning []
  - ChannelMessage.thread_id (from from_dict) is what filtering uses — not ctx.thread_id

Postconditions (stated per test via comments):
  - After on_message(msg): len(received()) increases by 1
  - After clear(): received() == []
  - After received(thread_id=x): every returned message has .thread_id == x
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage, EventPart, TextPart
from ironbridge.platform.channels.registry import get_adapter
from tests.channel_adapter_stub import RecordingAdapter


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def adapter() -> RecordingAdapter:
    """Singleton RecordingAdapter, cleared before each test."""
    a = RecordingAdapter.install()
    a.clear()
    # postcondition of fixture: adapter starts empty
    assert a.received() == []
    return a


def _ctx(thread_id="t1") -> ChannelContext:
    return ChannelContext(MagicMock(), thread_id, "ch-1", "tenant-a")


def _msg(thread_id="t1", role="USER", text="hello") -> ChannelMessage:
    return ChannelMessage.from_dict({
        "thread_id": thread_id,
        "participant_id": "alice",
        "participant_type": "HUMAN",
        "role": role,
        "content": {"version": 1, "parts": [{"type": "text", "text": text}]},
    })


# ── Registry invariant ────────────────────────────────────────────────────────

def test_install_returns_singleton():
    """
    Pre:  module loaded
    Post: install() called twice returns identical object;
          get_adapter("recording") returns that object
    """
    a1 = RecordingAdapter.install()
    a2 = RecordingAdapter.install()

    assert a1 is a2
    assert get_adapter("recording") is a1


def test_recording_adapter_registered(adapter):
    """
    Pre:  adapter fixture has called install()
    Post: get_adapter("recording") is the same singleton returned by install()
    """
    assert get_adapter("recording") is adapter


# ── on_message preconditions and postconditions ───────────────────────────────

def test_on_message_records_single(adapter):
    """
    Pre:  adapter is empty
    Post: received() has exactly 1 entry; entry.role == "USER"
    """
    # pre
    assert adapter.received() == []

    adapter.on_message(_msg(text="ping"), {}, _ctx())

    # post
    result = adapter.received()
    assert len(result) == 1
    assert result[0].role == "USER"
    assert result[0].thread_id == "t1"


def test_on_message_records_multiple(adapter):
    """
    Pre:  adapter is empty
    Post: each call appends one entry — no deduplication, count == n calls
    """
    assert adapter.received() == []

    for i in range(3):
        adapter.on_message(_msg(text=f"msg {i}"), {}, _ctx())

    result = adapter.received()
    assert len(result) == 3
    # order preserved
    texts = [p.text for r in result for p in r.parts if isinstance(p, TextPart)]
    assert texts == ["msg 0", "msg 1", "msg 2"]


def test_on_message_same_message_twice(adapter):
    """
    Pre:  adapter is empty
    Post: two calls with same object → two entries (no identity-based dedup)
    """
    assert adapter.received() == []

    msg = _msg()
    adapter.on_message(msg, {}, _ctx())
    adapter.on_message(msg, {}, _ctx())

    assert len(adapter.received()) == 2


def test_on_message_does_not_mutate_message(adapter):
    """
    Pre:  a ChannelMessage
    Post: the message object is unchanged after on_message()
    """
    msg = _msg(text="original")
    original_thread_id = msg.thread_id
    original_role = msg.role

    adapter.on_message(msg, {}, _ctx())

    assert msg.thread_id == original_thread_id
    assert msg.role == original_role


# ── received() filter postconditions ─────────────────────────────────────────

def test_received_no_filter_returns_all(adapter):
    """
    Pre:  messages from two threads recorded
    Post: received() with no filter returns all; count == total on_message() calls
    """
    adapter.on_message(_msg(thread_id="t1"), {}, _ctx("t1"))
    adapter.on_message(_msg(thread_id="t2"), {}, _ctx("t2"))

    result = adapter.received()
    assert len(result) == 2


def test_received_returns_copy_not_reference(adapter):
    """
    Pre:  one message recorded
    Post: mutating the returned list does not affect internal state
    """
    adapter.on_message(_msg(), {}, _ctx())

    result = adapter.received()
    result.clear()  # mutate the returned copy

    # internal state unchanged
    assert len(adapter.received()) == 1


def test_received_filters_by_thread_id(adapter):
    """
    Pre:  messages from t1 and t2 recorded
    Post: received(thread_id=x) returns only messages where .thread_id == x
    """
    adapter.on_message(_msg(thread_id="t1", text="a"), {}, _ctx("t1"))
    adapter.on_message(_msg(thread_id="t2", text="b"), {}, _ctx("t2"))

    r1 = adapter.received(thread_id="t1")
    r2 = adapter.received(thread_id="t2")

    assert len(r1) == 1
    assert all(m.thread_id == "t1" for m in r1)
    assert len(r2) == 1
    assert all(m.thread_id == "t2" for m in r2)


def test_received_unknown_thread_returns_empty(adapter):
    """
    Pre:  one message on t1
    Post: received(thread_id="no-such") == []
    """
    adapter.on_message(_msg(thread_id="t1"), {}, _ctx("t1"))

    assert adapter.received(thread_id="no-such-thread") == []


def test_ctx_thread_id_does_not_affect_recording(adapter):
    """
    Pre:  message with thread_id="t-msg", ctx with thread_id="t-ctx-different"
    Post: filtering uses ChannelMessage.thread_id, not ChannelContext.thread_id
    """
    msg = _msg(thread_id="t-msg")
    adapter.on_message(msg, {}, _ctx("t-ctx-different"))

    assert len(adapter.received(thread_id="t-msg")) == 1
    assert len(adapter.received(thread_id="t-ctx-different")) == 0


# ── clear() postconditions ────────────────────────────────────────────────────

def test_clear_resets_to_empty(adapter):
    """
    Pre:  one message recorded
    Post: received() == [] immediately after clear()
    """
    adapter.on_message(_msg(), {}, _ctx())
    assert len(adapter.received()) == 1  # pre-clear state

    adapter.clear()

    assert adapter.received() == []


def test_clear_then_record_again(adapter):
    """
    Pre:  message "before" recorded, then clear()
    Post: only message "after" is in received(); "before" is gone
    """
    adapter.on_message(_msg(text="before"), {}, _ctx())
    adapter.clear()

    adapter.on_message(_msg(text="after"), {}, _ctx())

    result = adapter.received()
    assert len(result) == 1
    texts = [p.text for p in result[0].parts if isinstance(p, TextPart)]
    assert texts == ["after"]
    assert not any("before" in t for t in texts)


def test_clear_is_idempotent(adapter):
    """
    Pre:  adapter is already empty
    Post: calling clear() again does not raise and received() is still []
    """
    assert adapter.received() == []
    adapter.clear()
    assert adapter.received() == []


# ── Part parsing postconditions ───────────────────────────────────────────────

def test_assistant_role_recorded(adapter):
    """
    Pre:  message with role=ASSISTANT
    Post: recorded message has role == "ASSISTANT"
    """
    msg = _msg(role="ASSISTANT", text="Echo: hello")
    adapter.on_message(msg, {}, _ctx())

    result = adapter.received()
    assert len(result) == 1
    assert result[0].role == "ASSISTANT"


def test_event_part_extra_fields_preserved(adapter):
    """
    Pre:  EventPart with extra fields (step, error) — EventPart has extra='allow'
    Post: extra fields are accessible on the parsed EventPart
    """
    msg = ChannelMessage.from_dict({
        "thread_id": "t1",
        "participant_id": "system",
        "participant_type": "SYSTEM",
        "role": "SYSTEM",
        "content": {"version": 1, "parts": [
            {"type": "event", "event": "AGENT_RUN_RETRY", "step": "llm_call_0", "error": "timeout"}
        ]},
    })
    adapter.on_message(msg, {}, _ctx())

    recorded = adapter.received()[0]
    event_parts = [p for p in recorded.parts if isinstance(p, EventPart)]

    # post
    assert len(event_parts) == 1
    assert event_parts[0].event == "AGENT_RUN_RETRY"
    assert event_parts[0].step == "llm_call_0"   # extra field preserved
    assert event_parts[0].error == "timeout"      # extra field preserved


def test_unknown_part_type_dropped_message_still_delivered(adapter):
    """
    Pre:  content with one valid TextPart and one unknown part type
    Post: message is delivered to adapter; only the valid part survives;
          unknown part is silently dropped (no exception raised)
    """
    msg = ChannelMessage.from_dict({
        "thread_id": "t1",
        "participant_id": "alice",
        "participant_type": "HUMAN",
        "role": "USER",
        "content": {"version": 1, "parts": [
            {"type": "text", "text": "valid"},
            {"type": "unknown_future_type", "data": "x"},
        ]},
    })
    adapter.on_message(msg, {}, _ctx())

    result = adapter.received()
    # post: message delivered
    assert len(result) == 1
    # post: only the valid part survives
    assert len(result[0].parts) == 1
    assert isinstance(result[0].parts[0], TextPart)
    assert result[0].parts[0].text == "valid"


# ── StubAgent output contract ─────────────────────────────────────────────────

def test_stub_agent_llm_echo_produces_text_part(adapter):
    """
    Pre:  history with one USER message containing text "hello"
    Post: _call_llm returns done=True, content contains "hello";
          when shaped into ChannelMessage, produces one ASSISTANT TextPart
          containing the echoed text.

    Scope: tests _call_llm output contract + ChannelMessage parsing.
    Does NOT test the full delivery pipeline (requires running stack).
    """
    from ironbridge.agents.stub import _call_llm

    history = [{"role": "USER", "content": {"parts": [{"type": "text", "text": "hello"}]}}]

    response = _call_llm(history)

    # postconditions on _call_llm
    assert response is not None
    assert response["done"] is True
    assert "hello" in response["content"]

    # shape into ChannelMessage as ChannelDelivery would
    msg = ChannelMessage.from_dict({
        "thread_id": "t-stub",
        "participant_id": "agent-run-xyz",
        "participant_type": "AGENT",
        "role": "ASSISTANT",
        "content": {"version": 1, "parts": [{"type": "text", "text": response["content"]}]},
    })
    adapter.on_message(msg, {}, _ctx("t-stub"))

    recorded = adapter.received(thread_id="t-stub")

    # postconditions on recorded message
    assert len(recorded) == 1
    assert recorded[0].role == "ASSISTANT"
    assert recorded[0].participant_type == "AGENT"
    text_parts = [p for p in recorded[0].parts if isinstance(p, TextPart)]
    assert len(text_parts) == 1
    assert "hello" in text_parts[0].text


def test_stub_agent_llm_empty_history_returns_none():
    """
    Pre:  empty history
    Post: _call_llm returns None — no message should be written by agent
    """
    from ironbridge.agents.stub import _call_llm

    result = _call_llm([])

    assert result is None


def test_stub_agent_llm_non_user_last_returns_done_empty():
    """
    Pre:  last message in history is ASSISTANT (not USER)
    Post: _call_llm returns done=True with empty content — agent loop terminates
    """
    from ironbridge.agents.stub import _call_llm

    history = [{"role": "ASSISTANT", "content": {"parts": [{"type": "text", "text": "done"}]}}]

    result = _call_llm(history)

    assert result is not None
    assert result["done"] is True
    assert result["content"] == ""
