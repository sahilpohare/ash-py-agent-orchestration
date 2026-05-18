"""
Scenario 4: Idempotent intake.

A client retries the same idempotency key — exactly one message persisted.
A concurrent burst of 20 identical retries also yields exactly one message.
Verified directly against DB, not via API.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import IRONBRIDGE_URL, TENANT_A, add_message, create_thread


@pytest.fixture
def client():
    return httpx.Client(base_url=IRONBRIDGE_URL, timeout=30)


def test_duplicate_sequential_is_deduplicated(client, raw_db):
    """
    DB-layer deduplication: insert same idempotency_key twice directly,
    verify exactly one row persisted. Tests ON CONFLICT DO NOTHING at the
    storage layer without going through Restate's own idempotency cache.
    """
    from sqlalchemy import text as t
    from ironbridge.shared.db import tenant_session
    from ironbridge.platform.sessions.message import Message, MessageRole, ParticipantType
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from cuid2 import cuid_wrapper
    _cuid = cuid_wrapper()

    thread_id = f"idem-seq-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)
    key = f"idem-seq-key-{thread_id}"

    # Insert same idempotency_key twice at DB layer
    with tenant_session(TENANT_A) as db:
        for _ in range(2):
            stmt = (
                pg_insert(Message)
                .values(
                    id=_cuid(),
                    thread_id=thread_id,
                    tenant_id=TENANT_A,
                    participant_id="alice",
                    participant_type=ParticipantType.HUMAN,
                    role=MessageRole.USER,
                    content={"version": 1, "parts": [{"type": "text", "text": "ping"}]},
                    idempotency_key=key,
                    position=999,
                )
                .on_conflict_do_nothing(constraint="uq_messages_thread_idempotency")
            )
            db.execute(stmt)
        db.commit()

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    count = raw_db.execute(
        text("SELECT COUNT(*) FROM messages WHERE thread_id = :tid AND idempotency_key = :key"),
        {"tid": thread_id, "key": key},
    ).scalar()

    assert count == 1, f"expected 1 message, got {count}"


def test_concurrent_duplicate_burst_is_deduplicated(raw_db):
    """
    20 concurrent DB inserts with same idempotency_key → exactly 1 row.
    Proves ON CONFLICT DO NOTHING holds under concurrent writers at storage layer.
    No window between check and insert.
    """
    from ironbridge.shared.db import tenant_session
    from ironbridge.platform.sessions.message import Message, MessageRole, ParticipantType
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from cuid2 import cuid_wrapper
    _cuid = cuid_wrapper()

    thread_id = f"idem-burst-{uuid.uuid4().hex[:8]}"
    key = f"idem-burst-key-{thread_id}"

    client = httpx.Client(base_url=IRONBRIDGE_URL, timeout=30)
    create_thread(client, thread_id, TENANT_A)

    def insert_once(_: int) -> None:
        with tenant_session(TENANT_A) as db:
            stmt = (
                pg_insert(Message)
                .values(
                    id=_cuid(),
                    thread_id=thread_id,
                    tenant_id=TENANT_A,
                    participant_id="alice",
                    participant_type=ParticipantType.HUMAN,
                    role=MessageRole.USER,
                    content={"version": 1, "parts": [{"type": "text", "text": "ping"}]},
                    idempotency_key=key,
                    position=999,
                )
                .on_conflict_do_nothing(constraint="uq_messages_thread_idempotency")
            )
            db.execute(stmt)
            db.commit()

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(insert_once, i) for i in range(20)]
        for f in as_completed(futures):
            f.result()

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
    count = raw_db.execute(
        text("SELECT COUNT(*) FROM messages WHERE thread_id = :tid AND idempotency_key = :key"),
        {"tid": thread_id, "key": key},
    ).scalar()

    assert count == 1, f"expected 1 message, got {count}"
