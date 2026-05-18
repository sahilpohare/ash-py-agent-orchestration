"""
Scenario 2: Concurrent-safe ordering.

Twenty clients POST concurrently to the same thread.
All twenty messages are persisted with strictly monotonic positions —
no gaps, no duplicates, no ties.
"""

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import pytest
from sqlalchemy import text

from tests.conftest import IRONBRIDGE_URL, TENANT_A, add_message, create_thread


@pytest.fixture
def client():
    return httpx.Client(base_url=IRONBRIDGE_URL, timeout=30)


def test_concurrent_messages_have_monotonic_positions(client, raw_db):
    thread_id = f"order-test-{uuid.uuid4().hex[:8]}"
    create_thread(client, thread_id, TENANT_A)

    n = 20

    def post(i: int) -> int:
        c = httpx.Client(base_url=IRONBRIDGE_URL, timeout=60)
        add_message(
            c, thread_id, TENANT_A,
            participant_id="alice",
            participant_type="HUMAN",
            role="USER",
            text=f"message {i}",
            idempotency_key=f"order-{thread_id}-{i}",
        )
        return i

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(post, i) for i in range(n)]
        for f in as_completed(futures, timeout=120):
            f.result()  # raise on error

    # Give agent responses time to land
    time.sleep(3)

    raw_db.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})  # within fixture transaction
    rows = raw_db.execute(
        text("SELECT position FROM messages WHERE thread_id = :tid ORDER BY position"),
        {"tid": thread_id},
    ).fetchall()

    positions = [r[0] for r in rows]
    assert len(positions) >= n, f"expected at least {n} messages, got {len(positions)}"

    # Strictly monotonic — no gaps, no duplicates
    for i, pos in enumerate(positions):
        assert pos == i + 1, f"position {pos} at index {i}, expected {i + 1}"
