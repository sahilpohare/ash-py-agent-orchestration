"""Domain tests for Agent."""

import pytest

from ironbridge.platform.agents.agent import Agent, AgentStatus


def make_agent() -> Agent:
    a = Agent()
    a.id = "test-agent-id"  # framework sets id = ctx.key() before calling create()
    a.create(name="Researcher", model="gpt-4o")
    return a


def test_create_preserves_framework_id():
    a = Agent()
    a.id = "framework-set-id"
    result = a.create(name="Researcher", model="gpt-4o")
    assert result.id == "framework-set-id"


def test_create_returns_self():
    a = Agent()
    result = a.create(name="Researcher", model="gpt-4o")
    assert result is a


def test_create_sets_name_and_model():
    a = make_agent()
    assert a.name == "Researcher"
    assert a.model == "gpt-4o"


def test_create_status_is_active():
    a = make_agent()
    assert a.status == AgentStatus.ACTIVE


def test_create_default_tools_empty():
    a = make_agent()
    assert a.tools == {}


def test_create_with_instructions():
    a = Agent()
    a.create(name="Writer", model="gpt-4o", instructions="You are a technical writer.")
    assert a.instructions == "You are a technical writer."


def test_create_instructions_default_none():
    a = make_agent()
    assert a.instructions is None


def test_create_with_tools():
    tools = {"search": {"description": "Search the web"}}
    a = Agent()
    a.create(name="Searcher", model="gpt-4o", tools=tools)
    assert a.tools == tools


def test_update_name():
    a = make_agent()
    a.update(name="Updated Researcher")
    assert a.name == "Updated Researcher"


def test_update_model():
    a = make_agent()
    a.update(model="claude-sonnet-4-6")
    assert a.model == "claude-sonnet-4-6"


def test_update_instructions():
    a = make_agent()
    a.update(instructions="Be concise.")
    assert a.instructions == "Be concise."


def test_update_tools():
    a = make_agent()
    tools = {"write_file": {"description": "Write a file"}}
    a.update(tools=tools)
    assert a.tools == tools


def test_update_partial_does_not_clear_other_fields():
    a = Agent()
    a.create(name="Researcher", model="gpt-4o", instructions="Be thorough.")
    a.update(name="Senior Researcher")
    assert a.instructions == "Be thorough."
    assert a.model == "gpt-4o"


def test_update_returns_self():
    a = make_agent()
    result = a.update(name="New Name")
    assert result is a


def test_deactivate():
    a = make_agent()
    a.deactivate()
    assert a.status == AgentStatus.INACTIVE


def test_get_returns_self():
    a = make_agent()
    assert a.get() is a


def test_agent_actions():
    actions = set(Agent.__actions__.keys())
    assert {"create", "update", "deactivate", "get"}.issubset(actions)
