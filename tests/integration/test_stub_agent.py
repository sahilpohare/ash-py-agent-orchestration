"""
StubAgent integration tests.

Requires running stack: postgres + restate + app.

Preconditions:
  - Thread exists in DB and Restate before add_message
  - StubAgent registered as "stub" (default agent_id)
  - Stack is reachable at IRONBRIDGE_URL

Invariants:
  - StubAgent always responds to a USER message
  - Empty history produces no AGENT message
  - Cancelled run produces no AGENT message

Postconditions:
  - After add_message(USER): at least one AGENT message appears in DB
  - After add_message with empty-history thread: no AGENT message
  - StubAgent echo: AGENT message content contains the user's text
"""

import time
import uuid

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import IRONBRIDGE_URL, TENANT_A, USER_NAME, add_message, create_thread


@pytest.fixture
def client():
    return httpx.Client(base_url=IRONBRIDGE_URL, timeout=30)


def _agent_messages(raw_db, thread_id: str) -> list:
    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    rows = raw_db.execute(
        text(
            "SELECT participant_type, content FROM messages "
            "WHERE thread_id = :tid AND participant_type = 'AGENT' ORDER BY position"
        ),
        {"tid": thread_id},
    ).fetchall()
    return rows


def test_stub_agent_run_simple_echo(client, raw_db):
    """
    Pre:  thread exists, one USER message with text "hello"
    Post: at least one AGENT message in DB; content contains "hello"
    """
    thread_id = f"stub-echo-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(
        client, thread_id, TENANT_A,
        "alice", "HUMAN", "USER", "hello",
        f"stub-{thread_id}-1",
    )

    time.sleep(4)

    rows = _agent_messages(raw_db, thread_id)
    assert len(rows) >= 1, "expected at least one AGENT message"

    import json
    content = rows[0][1] if isinstance(rows[0][1], dict) else json.loads(rows[0][1])
    texts = [p.get("text", "") for p in content.get("parts", []) if p.get("type") == "text"]
    assert any("hello" in t for t in texts), f"echo not found in agent response: {texts}"


def test_stub_agent_run_empty_history_does_nothing(client, raw_db):
    """
    Pre:  thread exists but no USER message sent — agent not triggered
    Post: no AGENT messages in DB for this thread
    """
    thread_id = f"stub-empty-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    # No add_message — agent is never triggered
    time.sleep(1)

    rows = _agent_messages(raw_db, thread_id)
    assert len(rows) == 0, f"expected no AGENT messages, got {len(rows)}"


def test_stub_agent_responds_to_each_turn(client, raw_db):
    """
    Pre:  two sequential USER messages
    Post: at least two AGENT messages (one per turn)
    """
    thread_id = f"stub-turns-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "first", f"stub-{thread_id}-1")
    time.sleep(4)
    add_message(client, thread_id, TENANT_A, "alice", "HUMAN", "USER", "second", f"stub-{thread_id}-2")
    time.sleep(4)

    rows = _agent_messages(raw_db, thread_id)
    assert len(rows) >= 2, f"expected at least 2 AGENT messages, got {len(rows)}"


def test_stub_agent_cancelled_produces_no_extra_message(client, raw_db):
    """
    Pre:  USER message sent, then cancel() called immediately
    Post: run is marked cancelled; may have zero or one AGENT message
          (depending on timing), but does not keep producing messages
    """
    thread_id = f"stub-cancel-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    add_message(
        client, thread_id, TENANT_A,
        "alice", "HUMAN", "USER", "hello",
        f"stub-{thread_id}-1",
    )

    # Fetch the run_id from agent_run_events to cancel it
    import json
    time.sleep(0.5)
    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    run_rows = raw_db.execute(
        text("SELECT run_id FROM agent_run_events WHERE thread_id = :tid ORDER BY created_at LIMIT 1"),
        {"tid": thread_id},
    ).fetchall()

    if run_rows:
        run_id = run_rows[0][0]
        cancel_resp = client.post(
            f"/AgentRun/{run_id}/cancel",
            json={"tenant_id": TENANT_A, "user_name": USER_NAME},
        )
        # cancel may return 404 if run already completed — that's fine
        assert cancel_resp.status_code in (200, 404, 409)

    time.sleep(2)

    # Count AGENT messages — should be 0 or 1 (not growing)
    rows_before = _agent_messages(raw_db, thread_id)
    time.sleep(2)
    rows_after = _agent_messages(raw_db, thread_id)

    assert len(rows_after) == len(rows_before), (
        "agent kept writing messages after cancel — run not stopped"
    )
