"""
Unit tests for StubAgent helpers, AgentRegistry, and AgentContext primitives.

Pure unit tests — no Restate, no DB, no HTTP.
AgentContext and Restate ctx are mocked.

StubAgent.run() integration tests (require live stack) are in:
  tests/integration/test_stub_agent.py
"""

from __future__ import annotations

import pytest

from ironbridge.platform.agents.agent_run import AgentRunRequest
from ironbridge.platform.agents.context import AgentCancelledError, AgentContext, _fetch_thread
from ironbridge.platform.agents.registry import AgentRegistry
from ironbridge.agents.stub import StubAgent, _call_llm, _execute_tool


# ── _call_llm ─────────────────────────────────────────────────────────────────

def test_call_llm_returns_none_on_empty_history():
    assert _call_llm([]) is None


def test_call_llm_echoes_user_message():
    history = [{"role": "USER", "content": {"parts": [{"type": "text", "text": "hello"}]}}]
    result = _call_llm(history)
    assert result is not None
    assert "hello" in result["content"]
    assert result["done"] is True


def test_call_llm_triggers_write_file_tool():
    history = [{"role": "USER", "content": {"parts": [{"type": "text", "text": "please write this"}]}}]
    result = _call_llm(history)
    assert result is not None
    assert result["done"] is True
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["name"] == "write_file"


def test_call_llm_returns_done_on_non_user_last_message():
    history = [{"role": "ASSISTANT", "content": {"parts": [{"type": "text", "text": "I did it"}]}}]
    result = _call_llm(history)
    assert result["done"] is True
    assert result["content"] == ""


# ── _execute_tool ─────────────────────────────────────────────────────────────

def test_execute_tool_search():
    result = _execute_tool({"name": "search", "arguments": {"query": "test"}})
    assert "results" in result


def test_execute_tool_write_file():
    result = _execute_tool({"name": "write_file", "arguments": {"path": "/tmp/x.txt"}})
    assert result["written"] is True
    assert result["path"] == "/tmp/x.txt"


def test_execute_tool_unknown():
    result = _execute_tool({"name": "explode", "arguments": {}})
    assert "error" in result


# ── AgentRegistry ─────────────────────────────────────────────────────────────

def test_registry_register_and_resolve():
    reg = AgentRegistry()
    reg.register("stub", StubAgent)
    agent = reg.resolve("stub")
    assert isinstance(agent, StubAgent)


def test_registry_resolve_unknown_raises():
    reg = AgentRegistry()
    with pytest.raises(KeyError, match="No agent registered"):
        reg.resolve("nonexistent")


def test_registry_each_resolve_returns_new_instance():
    reg = AgentRegistry()
    reg.register("stub", StubAgent)
    a1 = reg.resolve("stub")
    a2 = reg.resolve("stub")
    assert a1 is not a2


def test_registry_all():
    reg = AgentRegistry()
    reg.register("stub", StubAgent)
    assert "stub" in reg.all()


# ── AgentContext ──────────────────────────────────────────────────────────────

class MockRestateCtx:
    """Minimal mock of Restate WorkflowContext for unit testing."""

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
        self.sends.append({"service": service, "handler": handler, "key": key})

    def set(self, key, value):
        self._state[key] = value

    def key(self):
        return "test-run-id"


class MockPromise:
    def __init__(self, resolved_value=None):
        self._value = resolved_value

    async def peek(self):
        return self._value

    async def resolve(self, value):
        self._value = value

    def value(self):
        return self


def make_req(**kwargs) -> AgentRunRequest:
    defaults = {
        "run_id": "run-1",
        "agent_id": "stub",
        "thread_id": "thread-1",
        "tenant_id": "tenant-a",
    }
    defaults.update(kwargs)
    return AgentRunRequest(**defaults)


def test_agent_context_is_cancelled_false():
    import asyncio
    ctx = MockRestateCtx(cancelled=False)
    agent_ctx = AgentContext(ctx, make_req())
    assert asyncio.run(agent_ctx.is_cancelled()) is False


def test_agent_context_is_cancelled_true():
    import asyncio
    ctx = MockRestateCtx(cancelled=True)
    agent_ctx = AgentContext(ctx, make_req())
    assert asyncio.run(agent_ctx.is_cancelled()) is True


def test_agent_context_step_raises_on_cancel():
    import asyncio
    ctx = MockRestateCtx(cancelled=True)
    agent_ctx = AgentContext(ctx, make_req())
    with pytest.raises(AgentCancelledError):
        asyncio.run(agent_ctx.step("do_thing", lambda: "result"))


def test_agent_context_step_runs_fn_when_not_cancelled():
    import asyncio
    ctx = MockRestateCtx(cancelled=False)
    agent_ctx = AgentContext(ctx, make_req())
    result = asyncio.run(agent_ctx.step("do_thing", lambda: "result"))
    assert result == "result"


def test_agent_context_write_message_enqueues_send():
    ctx = MockRestateCtx()
    agent_ctx = AgentContext(ctx, make_req())
    agent_ctx.write_message({"version": 1, "parts": [{"type": "text", "text": "hi"}]}, 0)
    assert len(ctx.sends) == 1
    assert ctx.sends[0]["service"] == "Thread"
    assert ctx.sends[0]["handler"] == "add_message"
    assert ctx.sends[0]["key"] == "thread-1"


# ── ADR-21: Import side effect registers "stub" in agent_registry ─────────────
#
# Decision: agent implementations self-register at import time.
# The workflow runner resolves by agent_id from AgentRunRequest.
# Importing ironbridge.agents.stub must register "stub" in the module-level
# agent_registry singleton — no explicit registration call required by main.py
# beyond the import itself.

def test_stub_agent_registered_in_module_singleton():
    """
    Pre:  ironbridge.agents.stub imported (already happened via test imports above)
    Inv:  module-level agent_registry.register("stub", StubAgent) runs on import
    Post: "stub" present in agent_registry.all()
    """
    from ironbridge.platform.agents.registry import agent_registry
    assert "stub" in agent_registry.all(), (
        "ADR-21: importing ironbridge.agents.stub must register 'stub' "
        "in the module-level agent_registry singleton"
    )


def test_stub_agent_registry_resolves_stub_agent_instance():
    """
    Pre:  "stub" registered in agent_registry
    Post: agent_registry.resolve("stub") returns a StubAgent instance
    """
    from ironbridge.platform.agents.registry import agent_registry
    agent = agent_registry.resolve("stub")
    assert isinstance(agent, StubAgent)


def test_stub_agent_registry_each_resolve_is_new_instance():
    """
    Inv:  resolve() calls cls() — returns a new instance each time.
          Agents must not share state across runs.
    Pre:  "stub" registered
    Post: two consecutive resolve() calls return different objects
    """
    from ironbridge.platform.agents.registry import agent_registry
    a1 = agent_registry.resolve("stub")
    a2 = agent_registry.resolve("stub")
    assert a1 is not a2


def test_stub_agent_registered_class_is_stub_agent():
    """
    Inv:  registry stores the class, not an instance.
    Pre:  "stub" in agent_registry.all()
    Post: all()["stub"] is StubAgent (the class itself)
    """
    from ironbridge.platform.agents.registry import agent_registry
    assert agent_registry.all()["stub"] is StubAgent


