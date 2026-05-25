"""
Reconciliation tests — thread resume after deploy/crash.

DB is source of truth. Threads are always resumable with the same thread_id.
Restate journal is ephemeral execution cache — stale journals are discarded.

Reconciliation steps:
  1. Detect stale run (RUNNING in DB but Restate workflow dead)
  2. Write FAILED event for old run_id
  3. Start new AgentRun with same thread_id, new run_id
  4. Agent fetches history from DB — LLM continues naturally
  5. Scan history for unresolved HITL — re-register promise, don't re-issue prompt
  6. Continue — idempotent writes prevent duplicates

Pure unit tests — no Restate, no DB, no HTTP.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from ironbridge.platform.agents.agent_run import AgentRunRequest
from ironbridge.platform.agents.context import AgentCancelledError, AgentContext
from ironbridge.platform.sessions.thread import MessageView


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_req(**kwargs) -> AgentRunRequest:
    defaults = {
        "run_id": "run-new",
        "agent_id": "stub",
        "thread_id": "thread-1",
        "tenant_id": "tenant-a",
    }
    defaults.update(kwargs)
    return AgentRunRequest(**defaults)


def make_message(role: str, text: str, position: int = 1, part_type: str = "text") -> MessageView:
    return MessageView(
        id=f"msg-{position}",
        participant_id="user-1",
        participant_type="HUMAN",
        role=role,
        content={"parts": [{"type": part_type, "text": text}]},
        position=position,
    )


def make_response_request(request_id: str, position: int = 2) -> MessageView:
    return MessageView(
        id=f"msg-{position}",
        participant_id="agent-run-run-old",
        participant_type="AGENT",
        role="ASSISTANT",
        content={"parts": [{"type": "response_request", "request_id": request_id, "prompt": "Approve?", "options": []}]},
        position=position,
    )


def make_response_reply(request_id: str, position: int = 3) -> MessageView:
    return MessageView(
        id=f"msg-{position}",
        participant_id="user-1",
        participant_type="HUMAN",
        role="USER",
        content={"parts": [{"type": "response_reply", "request_id": request_id, "selected": ["approve"]}]},
        position=position,
    )


class MockRestateCtx:
    def __init__(self, cancelled: bool = False):
        self._cancelled = cancelled
        self._state = {}
        self.sends = []
        self.runs = []

    def promise(self, name: str):
        return MockPromise(self._cancelled if name == "cancel" else None)

    async def run(self, name: str, fn):
        self.runs.append(name)
        return fn()

    def generic_send(self, service, handler, payload, key=None):
        self.sends.append({"service": service, "handler": handler, "payload": payload, "key": key})

    def set(self, key, value):
        self._state[key] = value

    def key(self):
        return "run-new"


class MockPromise:
    def __init__(self, resolved_value=None):
        self._value = resolved_value

    async def peek(self):
        return self._value

    async def resolve(self, value):
        self._value = value

    def value(self):
        return self


# ── Step 4: agent resumes from DB history ─────────────────────────────────────


def test_resume_fetches_full_history_from_db():
    """
    After deploy, new AgentRun starts with same thread_id.
    get_history() returns full thread from DB — agent sees prior messages.
    """
    prior_history = [
        make_message("USER", "What is the weather?", position=1),
        make_message("ASSISTANT", "It is sunny.", position=2),
    ]

    ctx = MockRestateCtx()
    agent_ctx = AgentContext(ctx, make_req())

    with patch(
        "ironbridge.platform.agents.context._fetch_thread",
        return_value=prior_history,
    ) as mock_fetch:
        history = agent_ctx.get_history()

    mock_fetch.assert_called_once_with("thread-1", "tenant-a", limit=200)
    assert len(history) == 2
    assert history[0].role == "USER"
    assert history[1].role == "ASSISTANT"


def test_resume_history_includes_all_prior_turns():
    """
    All prior assistant messages survive in DB.
    New run sees complete conversation — LLM has full context.
    """
    prior_history = [
        make_message("USER", "Step 1", position=1),
        make_message("ASSISTANT", "Done step 1", position=2),
        make_message("USER", "Step 2", position=3),
        make_message("ASSISTANT", "Done step 2", position=4),
        make_message("USER", "Step 3", position=5),
    ]

    ctx = MockRestateCtx()
    agent_ctx = AgentContext(ctx, make_req())

    with patch(
        "ironbridge.platform.agents.context._fetch_thread",
        return_value=prior_history,
    ):
        history = agent_ctx.get_history()

    assert len(history) == 5
    # Last message is the unanswered user turn — agent continues from here
    assert history[-1].role == "USER"
    assert history[-1].content["parts"][0]["text"] == "Step 3"


# ── Step 5: unresolved HITL detection ─────────────────────────────────────────


def test_hitl_pending_detected_from_history():
    """
    response_request with no matching response_reply → HITL was pending.
    New run must detect this and re-register the promise, not re-issue prompt.
    """
    request_id = "hitl-req-001"
    history = [
        make_message("USER", "Deploy?", position=1),
        make_response_request(request_id, position=2),
        # No response_reply — HITL was pending when old run died
    ]

    pending = _find_pending_hitl(history)
    assert pending == [request_id]


def test_hitl_resolved_not_pending():
    """
    response_request with matching response_reply → HITL was resolved.
    New run should not re-register promise.
    """
    request_id = "hitl-req-002"
    history = [
        make_message("USER", "Deploy?", position=1),
        make_response_request(request_id, position=2),
        make_response_reply(request_id, position=3),
    ]

    pending = _find_pending_hitl(history)
    assert pending == []


def test_hitl_multiple_only_unresolved_pending():
    """
    Multiple HITL requests — only those without a reply are pending.
    """
    history = [
        make_message("USER", "Start", position=1),
        make_response_request("req-001", position=2),
        make_response_reply("req-001", position=3),   # resolved
        make_message("ASSISTANT", "Continuing...", position=4),
        make_response_request("req-002", position=5),  # pending
    ]

    pending = _find_pending_hitl(history)
    assert pending == ["req-002"]


def test_hitl_no_requests_in_history():
    """Thread with no HITL — no pending requests."""
    history = [
        make_message("USER", "Hello", position=1),
        make_message("ASSISTANT", "Hi there", position=2),
    ]

    pending = _find_pending_hitl(history)
    assert pending == []


# ── Step 6: idempotent writes prevent duplicates ───────────────────────────────


def test_write_message_uses_idempotency_key():
    """
    write_message enqueues to Thread.add_message with an idempotency_key in payload.
    Re-sending same message on resume does not duplicate in DB.
    """
    import json

    ctx = MockRestateCtx()
    agent_ctx = AgentContext(ctx, make_req())
    agent_ctx.write_message({"version": 1, "parts": [{"type": "text", "text": "Hello"}]}, message_count=0)

    assert len(ctx.sends) == 1
    send = ctx.sends[0]
    assert send["service"] == "Thread"
    assert send["handler"] == "add_message"
    assert send["key"] == "thread-1"

    payload = json.loads(send["payload"])
    assert "idempotency_key" in payload
    assert payload["tenant_id"] == "tenant-a"
    assert payload["participant_id"] == "agent-run-run-new"


def test_new_run_id_does_not_affect_thread_id():
    """
    Reconciliation starts new run_id but keeps same thread_id.
    All writes still target the original thread.
    """
    ctx = MockRestateCtx()
    # New run_id (run-new), same thread_id (thread-1)
    req = make_req(run_id="run-new", thread_id="thread-1")
    agent_ctx = AgentContext(ctx, req)

    agent_ctx.write_message({"version": 1, "parts": [{"type": "text", "text": "Resumed"}]}, message_count=0)

    assert ctx.sends[0]["key"] == "thread-1"


# ── Step 1-2: stale run detection ─────────────────────────────────────────────


def test_step_raises_cancelled_on_stale_run():
    """
    If new run detects cancel (e.g. from prior orphan cleanup),
    agent exits cleanly at step boundary — does not re-execute completed work.
    """
    ctx = MockRestateCtx(cancelled=True)
    agent_ctx = AgentContext(ctx, make_req())

    with pytest.raises(AgentCancelledError):
        asyncio.run(agent_ctx.step("do_work", lambda: "result"))


def test_step_executes_when_not_cancelled():
    """Fresh run (not cancelled) proceeds normally."""
    ctx = MockRestateCtx(cancelled=False)
    agent_ctx = AgentContext(ctx, make_req())

    result = asyncio.run(agent_ctx.step("do_work", lambda: "result"))
    assert result == "result"


# ── Helper: HITL pending scan (to be implemented in AgentContext or reconciler) -


def _find_pending_hitl(history: list[MessageView]) -> list[str]:
    """
    Scan thread history for unresolved HITL requests.
    Returns list of request_ids that have a response_request but no response_reply.

    This logic belongs in the reconciliation layer (AgentContext or a
    dedicated reconciler). Defined here to specify the contract.
    """
    requested: set[str] = set()
    resolved: set[str] = set()

    for msg in history:
        for part in msg.content.get("parts", []):
            if part.get("type") == "response_request":
                requested.add(part["request_id"])
            elif part.get("type") == "response_reply":
                resolved.add(part["request_id"])

    return [rid for rid in requested if rid not in resolved]
