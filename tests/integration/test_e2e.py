"""
Scenario 1: Basic end-to-end flow.

Client POSTs a message → agent responds → both appear in thread.
Alice POSTs again → agent responds again.
Full exchange is durable and retrievable via Thread.get.
"""

import time
import uuid

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import IRONBRIDGE_URL, TENANT_A, USER_NAME, add_message, create_thread


@pytest.fixture
def client():
    return httpx.Client(base_url=IRONBRIDGE_URL, timeout=15)


def test_message_triggers_agent_response(client, raw_db):
    thread_id = f"e2e-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(
        client, thread_id, TENANT_A,
        "alice", "HUMAN", "USER", "ping",
        f"e2e-{thread_id}-1",
    )

    # Agent responds asynchronously — give it time
    time.sleep(3)

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    rows = raw_db.execute(
        text("SELECT participant_type, position FROM messages WHERE thread_id = :tid ORDER BY position"),
        {"tid": thread_id},
    ).fetchall()

    types = [r[0] for r in rows]
    assert "HUMAN" in types
    assert "AGENT" in types


def test_multi_turn_exchange(client, raw_db):
    thread_id = f"multi-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "first", f"multi-{thread_id}-1")
    time.sleep(3)
    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "second", f"multi-{thread_id}-2")
    time.sleep(3)

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    rows = raw_db.execute(
        text("SELECT participant_type FROM messages WHERE thread_id = :tid ORDER BY position"),
        {"tid": thread_id},
    ).fetchall()

    types = [r[0] for r in rows]
    human_count = types.count("HUMAN")
    agent_count = types.count("AGENT")

    assert human_count == 2
    assert agent_count >= 2


def test_agent_response_position_follows_human(client, raw_db):
    """Agent response always has a higher position than the human message that triggered it."""
    thread_id = f"pos-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "go", f"pos-{thread_id}-1")
    time.sleep(3)

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    rows = raw_db.execute(
        text("SELECT participant_type, position FROM messages WHERE thread_id = :tid ORDER BY position"),
        {"tid": thread_id},
    ).fetchall()

    human_pos = next(r[1] for r in rows if r[0] == "HUMAN")
    agent_pos = next(r[1] for r in rows if r[0] == "AGENT")

    assert agent_pos > human_pos


def test_thread_get_returns_full_history(client):
    thread_id = f"hist-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "hello", f"hist-{thread_id}-1")
    time.sleep(3)

    resp = client.post(f"/Thread/{thread_id}/get", json={"tenant_id": TENANT_A, "user_name": USER_NAME})
    resp.raise_for_status()
    data = resp.json()

    messages = data.get("messages", [])
    assert len(messages) >= 2  # human + at least one agent response
    positions = [m["position"] for m in messages]
    assert positions == sorted(positions)
    assert len(set(positions)) == len(positions)  # no duplicate positions
