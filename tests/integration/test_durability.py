"""
Scenario 3: Durability.

Proves that thread history is fully recoverable from durable storage.
Cannot kill -9 the server in a test, so we verify the DB invariant:
every message is persisted with correct state before the API returns.

A separate manual test procedure for kill -9 is documented below.

Manual kill -9 test:
  1. docker compose up
  2. POST /Thread/{id}/add_message  (note thread_id)
  3. docker compose kill app
  4. docker compose start app
  5. POST /Thread/{thread_id}/get   → messages intact
  6. POST /Thread/{thread_id}/add_message (new message) → position continues
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


def test_message_persisted_before_response(client, raw_db):
    """Message is in DB as soon as add_message returns 200."""
    thread_id = f"dur-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    key = f"dur-key-{thread_id}"
    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "ping", key)

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    count = raw_db.execute(
        text("SELECT COUNT(*) FROM messages WHERE thread_id = :tid"),
        {"tid": thread_id},
    ).scalar()

    assert count >= 1


def test_full_history_recoverable_via_get(client):
    """Thread.get returns full message history."""
    thread_id = f"hist-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    messages = ["first", "second", "third"]
    for i, text in enumerate(messages):
        add_message(
            client, thread_id, TENANT_A,
            "alice", "HUMAN", "USER", text,
            f"hist-{thread_id}-{i}",
        )

    # Allow agent responses to land
    time.sleep(3)

    resp = client.post(f"/Thread/{thread_id}/get", json={"tenant_id": TENANT_A, "user_name": USER_NAME})
    resp.raise_for_status()
    data = resp.json()

    persisted = [
        m for m in data.get("messages", [])
        if m.get("participant_type") == "HUMAN"
    ]
    assert len(persisted) == len(messages)

    texts = [
        next(p["text"] for p in m["content"]["parts"] if p["type"] == "text")
        for m in persisted
    ]
    assert texts == messages


def test_position_continues_after_gap(client, raw_db):
    """New messages continue position counter from where it left off."""
    thread_id = f"cont-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "one", f"cont-{thread_id}-1")
    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "two", f"cont-{thread_id}-2")

    time.sleep(1)

    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "three", f"cont-{thread_id}-3")

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    rows = raw_db.execute(
        text("SELECT position FROM messages WHERE thread_id = :tid ORDER BY position"),
        {"tid": thread_id},
    ).fetchall()

    positions = [r[0] for r in rows]
    assert positions == list(range(1, len(positions) + 1))
