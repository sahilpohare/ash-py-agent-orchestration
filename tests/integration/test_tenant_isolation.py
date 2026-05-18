"""
RLS (Row Level Security) integration tests — ADR-3.

Decision: Postgres RLS enforced via SET LOCAL app.tenant_id on every connection.
          Fails closed — a missing or wrong tenant_id returns zero rows.

Tables with RLS (from migrations 0001, a1b2c3d4e5f6, b1c2d3e4f5a6):
    users, agents, threads, messages, agent_run_events, channels, channel_bindings

Preconditions per test:
    - DB running with migrations applied
    - Rows for both TENANT_A and TENANT_B inserted before assertions
    - Sessions connect as the 'app' role (FORCE RLS applies to all roles)

Invariants:
    - SET LOCAL app.tenant_id = X → only rows with tenant_id = X are visible
    - SET LOCAL app.tenant_id = '' → zero rows (fails closed)
    - FORCE ROW LEVEL SECURITY → even the table owner is filtered
    - USING clause on INSERT → inserting a row with tenant_id != session tenant
      violates the policy (Postgres applies USING as WITH CHECK when no
      explicit WITH CHECK is given)

Postconditions per test:
    - own-tenant row present in result set
    - other-tenant row absent from result set
"""

import uuid

import pytest
from sqlalchemy import text

from tests.conftest import IRONBRIDGE_URL, TENANT_A, TENANT_B, create_thread
import httpx


@pytest.fixture
def client():
    return httpx.Client(base_url=IRONBRIDGE_URL, timeout=15)


def _insert_thread(thread_id: str, tenant_id: str) -> None:
    from ironbridge.shared.db import tenant_session
    from ironbridge.platform.sessions.thread import Thread
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    with tenant_session(tenant_id) as db:
        db.execute(
            pg_insert(Thread).values(id=thread_id, tenant_id=tenant_id).on_conflict_do_nothing()
        )
        db.commit()


def test_rls_tenant_a_cannot_see_tenant_b_threads(engine):
    thread_a = f"rls-a-{uuid.uuid4().hex[:8]}"
    thread_b = f"rls-b-{uuid.uuid4().hex[:8]}"

    _insert_thread(thread_a, TENANT_A)
    _insert_thread(thread_b, TENANT_B)

    # Tenant A session — no WHERE clause. SET LOCAL requires active transaction.
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
            rows = conn.execute(text("SELECT id FROM threads")).fetchall()

    ids = {r[0] for r in rows}
    assert thread_a in ids, "tenant-a thread missing from tenant-a session"
    assert thread_b not in ids, "tenant-b thread visible from tenant-a session — RLS breach"


def test_rls_tenant_b_cannot_see_tenant_a_threads(engine):
    thread_a = f"rls-a-{uuid.uuid4().hex[:8]}"
    thread_b = f"rls-b-{uuid.uuid4().hex[:8]}"

    _insert_thread(thread_a, TENANT_A)
    _insert_thread(thread_b, TENANT_B)

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_B})
            rows = conn.execute(text("SELECT id FROM threads")).fetchall()

    ids = {r[0] for r in rows}
    assert thread_b in ids, "tenant-b thread missing from tenant-b session"
    assert thread_a not in ids, "tenant-a thread visible from tenant-b session — RLS breach"


def test_rls_messages_isolated(engine):
    """Insert messages directly via DB to avoid HTTP timeout under load."""
    from ironbridge.shared.db import tenant_session
    from ironbridge.platform.sessions.thread import Thread
    from ironbridge.platform.sessions.message import Message, MessageRole, ParticipantType
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from cuid2 import cuid_wrapper
    _cuid = cuid_wrapper()

    thread_a = f"rls-msg-a-{uuid.uuid4().hex[:8]}"
    thread_b = f"rls-msg-b-{uuid.uuid4().hex[:8]}"

    for thread_id, tenant_id in [(thread_a, TENANT_A), (thread_b, TENANT_B)]:
        with tenant_session(tenant_id) as db:
            db.execute(
                pg_insert(Thread).values(id=thread_id, tenant_id=tenant_id).on_conflict_do_nothing()
            )
            db.execute(
                pg_insert(Message).values(
                    id=_cuid(), thread_id=thread_id, tenant_id=tenant_id,
                    participant_id="alice", participant_type=ParticipantType.HUMAN,
                    role=MessageRole.USER,
                    content={"version": 1, "parts": [{"type": "text", "text": "hi"}]},
                    idempotency_key=f"rls-msg-{thread_id}", position=1,
                ).on_conflict_do_nothing()
            )
            db.commit()

    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
            rows = conn.execute(text("SELECT thread_id FROM messages")).fetchall()

    thread_ids = {r[0] for r in rows}
    assert thread_a in thread_ids
    assert thread_b not in thread_ids, "tenant-b message visible from tenant-a session"


def test_rls_no_tenant_set_returns_empty(engine):
    """Session with no tenant set returns no rows — fails closed."""
    with engine.connect() as conn:
        with conn.begin():
            # Explicitly clear tenant — simulates a forgotten SET LOCAL
            conn.execute(text("SET LOCAL app.tenant_id = ''"))
            rows = conn.execute(text("SELECT id FROM threads")).fetchall()

    assert rows == [], f"expected empty, got {len(rows)} rows — RLS not enforced"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_direct(engine, table: str, values: dict, tenant_id: str) -> None:
    """Insert a row directly via DB, bypassing Restate, using tenant session."""
    cols = ", ".join(values.keys())
    params = ", ".join(f":{k}" for k in values.keys())
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text(f"SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            conn.execute(text(f"INSERT INTO {table} ({cols}) VALUES ({params}) ON CONFLICT DO NOTHING"), values)


def _visible_ids(engine, table: str, id_col: str, tenant_id: str) -> set:
    """Return all ids visible in table for the given tenant session."""
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": tenant_id})
            rows = conn.execute(text(f"SELECT {id_col} FROM {table}")).fetchall()
    return {r[0] for r in rows}


# ── users ─────────────────────────────────────────────────────────────────────

def test_rls_users_isolated(engine):
    """
    Pre:  one user row per tenant, inserted via tenant session
    Post: tenant-a session sees only tenant-a user; tenant-b user absent
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    uid_a = f"user-a-{uuid.uuid4().hex[:8]}"
    uid_b = f"user-b-{uuid.uuid4().hex[:8]}"

    for uid, tid in [(uid_a, TENANT_A), (uid_b, TENANT_B)]:
        _insert_direct(engine, "users", {
            "id": uid, "tenant_id": tid,
            "email": f"{uid}@example.com", "name": "Test",
            "role": "MEMBER", "status": "ACTIVE",
            "created_at": now, "updated_at": now,
        }, tid)

    ids_a = _visible_ids(engine, "users", "id", TENANT_A)
    assert uid_a in ids_a, "tenant-a user missing from tenant-a session"
    assert uid_b not in ids_a, "tenant-b user visible from tenant-a session — RLS breach"


def test_rls_users_no_tenant_returns_empty(engine):
    """
    Pre:  at least one user row exists
    Post: session with empty tenant sees no users — fails closed
    """
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = ''"))
            rows = conn.execute(text("SELECT id FROM users")).fetchall()
    assert rows == []


# ── agents ────────────────────────────────────────────────────────────────────

def test_rls_agents_isolated(engine):
    """
    Pre:  one agent per tenant
    Post: tenant-a session sees only tenant-a agent
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    aid_a = f"agent-a-{uuid.uuid4().hex[:8]}"
    aid_b = f"agent-b-{uuid.uuid4().hex[:8]}"

    for aid, tid in [(aid_a, TENANT_A), (aid_b, TENANT_B)]:
        _insert_direct(engine, "agents", {
            "id": aid, "tenant_id": tid,
            "name": "Test Agent", "model": "gpt-4o",
            "status": "ACTIVE", "created_at": now, "updated_at": now,
        }, tid)

    ids_a = _visible_ids(engine, "agents", "id", TENANT_A)
    assert aid_a in ids_a, "tenant-a agent missing"
    assert aid_b not in ids_a, "tenant-b agent visible from tenant-a session — RLS breach"


# ── agent_run_events ──────────────────────────────────────────────────────────

def test_rls_agent_run_events_isolated(engine):
    """
    Pre:  one event per tenant (ADR-20: agent_run_events is tenant-scoped)
    Post: tenant-a session sees only tenant-a event
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    eid_a = f"ev-a-{uuid.uuid4().hex[:8]}"
    eid_b = f"ev-b-{uuid.uuid4().hex[:8]}"
    rid_a = f"run-a-{uuid.uuid4().hex[:8]}"
    rid_b = f"run-b-{uuid.uuid4().hex[:8]}"
    tid_a = f"thread-ev-a-{uuid.uuid4().hex[:8]}"
    tid_b = f"thread-ev-b-{uuid.uuid4().hex[:8]}"

    for eid, rid, thid, tenant in [(eid_a, rid_a, tid_a, TENANT_A), (eid_b, rid_b, tid_b, TENANT_B)]:
        _insert_direct(engine, "agent_run_events", {
            "id": eid, "run_id": rid, "thread_id": thid,
            "tenant_id": tenant, "event_type": "RUNNING", "created_at": now,
        }, tenant)

    ids_a = _visible_ids(engine, "agent_run_events", "id", TENANT_A)
    assert eid_a in ids_a, "tenant-a event missing"
    assert eid_b not in ids_a, "tenant-b event visible from tenant-a session — RLS breach"


def test_rls_agent_run_events_no_tenant_returns_empty(engine):
    """
    Post: session with empty tenant sees no agent_run_events — fails closed
    """
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = ''"))
            rows = conn.execute(text("SELECT id FROM agent_run_events")).fetchall()
    assert rows == []


# ── channels ──────────────────────────────────────────────────────────────────

def test_rls_channels_isolated(engine):
    """
    Pre:  one channel per tenant
    Post: tenant-a session sees only tenant-a channel
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    cid_a = f"ch-a-{uuid.uuid4().hex[:8]}"
    cid_b = f"ch-b-{uuid.uuid4().hex[:8]}"

    for cid, tid in [(cid_a, TENANT_A), (cid_b, TENANT_B)]:
        _insert_direct(engine, "channels", {
            "id": cid, "tenant_id": tid,
            "name": "Test", "channel_type": "web",
            "default_agent_id": "stub", "status": "ACTIVE",
            "created_at": now, "updated_at": now,
        }, tid)

    ids_a = _visible_ids(engine, "channels", "id", TENANT_A)
    assert cid_a in ids_a, "tenant-a channel missing"
    assert cid_b not in ids_a, "tenant-b channel visible from tenant-a session — RLS breach"


def test_rls_channels_no_tenant_returns_empty(engine):
    """Post: session with empty tenant sees no channels — fails closed."""
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = ''"))
            rows = conn.execute(text("SELECT id FROM channels")).fetchall()
    assert rows == []


# ── channel_bindings ──────────────────────────────────────────────────────────

def test_rls_channel_bindings_isolated(engine):
    """
    Pre:  one binding per tenant (requires thread + channel rows to exist first
          — we bypass FK constraint by using the same IDs directly)
    Post: tenant-a session sees only tenant-a binding
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    # Create prerequisite threads and channels
    thid_a = f"th-bind-a-{uuid.uuid4().hex[:8]}"
    thid_b = f"th-bind-b-{uuid.uuid4().hex[:8]}"
    chid_a = f"ch-bind-a-{uuid.uuid4().hex[:8]}"
    chid_b = f"ch-bind-b-{uuid.uuid4().hex[:8]}"

    for thid, chid, tid in [(thid_a, chid_a, TENANT_A), (thid_b, chid_b, TENANT_B)]:
        _insert_direct(engine, "threads", {
            "id": thid, "tenant_id": tid, "created_at": now, "updated_at": now,
        }, tid)
        _insert_direct(engine, "channels", {
            "id": chid, "tenant_id": tid,
            "name": "Bind Test", "channel_type": "web",
            "default_agent_id": "stub", "status": "ACTIVE",
            "created_at": now, "updated_at": now,
        }, tid)

    bid_a = f"bind-a-{uuid.uuid4().hex[:8]}"
    bid_b = f"bind-b-{uuid.uuid4().hex[:8]}"

    for bid, thid, chid, tid in [
        (bid_a, thid_a, chid_a, TENANT_A),
        (bid_b, thid_b, chid_b, TENANT_B),
    ]:
        _insert_direct(engine, "channel_bindings", {
            "id": bid, "tenant_id": tid,
            "thread_id": thid, "channel_id": chid, "created_at": now,
        }, tid)

    ids_a = _visible_ids(engine, "channel_bindings", "id", TENANT_A)
    assert bid_a in ids_a, "tenant-a binding missing"
    assert bid_b not in ids_a, "tenant-b binding visible from tenant-a session — RLS breach"


def test_rls_channel_bindings_no_tenant_returns_empty(engine):
    """Post: session with empty tenant sees no channel_bindings — fails closed."""
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = ''"))
            rows = conn.execute(text("SELECT id FROM channel_bindings")).fetchall()
    assert rows == []


# ── Own rows visible after insert (sanity invariant) ─────────────────────────

def test_rls_own_row_visible_after_insert(engine):
    """
    Inv:  a tenant must be able to read the rows it just wrote.
          Regression guard: over-restrictive policy would break writes.
    Pre:  thread inserted via tenant-a session
    Post: that thread is visible in the same tenant-a session
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    thid = f"own-row-{uuid.uuid4().hex[:8]}"

    _insert_direct(engine, "threads", {
        "id": thid, "tenant_id": TENANT_A,
        "created_at": now, "updated_at": now,
    }, TENANT_A)

    ids = _visible_ids(engine, "threads", "id", TENANT_A)
    assert thid in ids, "tenant cannot read its own just-written row — policy too restrictive"


# ── Cross-tenant write blocked ────────────────────────────────────────────────

def test_rls_cross_tenant_write_blocked(engine):
    """
    Inv:  USING policy on INSERT acts as WITH CHECK (Postgres default).
          A session with tenant-a cannot insert a row with tenant_id='tenant-b'.
    Pre:  session has app.tenant_id = TENANT_A
          INSERT specifies tenant_id = TENANT_B explicitly
    Post: INSERT raises an exception (policy violation)
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    thid = f"cross-{uuid.uuid4().hex[:8]}"

    with pytest.raises(Exception, match="new row violates row-level security policy|permission denied"):
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
                conn.execute(
                    text("INSERT INTO threads (id, tenant_id, created_at, updated_at) VALUES (:id, :tenant_id, :ca, :ua)"),
                    {"id": thid, "tenant_id": TENANT_B, "ca": now, "ua": now},
                )
