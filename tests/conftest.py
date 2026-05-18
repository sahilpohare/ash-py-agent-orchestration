"""
Shared fixtures for integration tests.

Integration tests require a running stack:
  docker compose up -d postgres restate app

IRONBRIDGE_URL  — Restate ingress (default: http://localhost:8080)
DATABASE_URL    — Postgres (default: postgresql://ironbridge:ironbridge@localhost:5432/ironbridge)
"""

import os

import pytest
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from tests.channel_adapter_stub import RecordingAdapter

IRONBRIDGE_URL = os.environ.get("IRONBRIDGE_URL", "http://localhost:8080")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://app:app@localhost:5432/ironbridge",
)

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


@pytest.fixture(scope="session")
def engine():
    return create_engine(DATABASE_URL)


@pytest.fixture
def raw_db(engine):
    """Raw DB connection with open transaction — bypasses RLS for verification queries."""
    with engine.connect() as conn:
        with conn.begin():
            yield conn


@pytest.fixture
def tenant_a_db(engine):
    """DB session scoped to tenant-a via RLS. SET LOCAL requires active transaction."""
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_A})
            yield conn


@pytest.fixture
def tenant_b_db(engine):
    """DB session scoped to tenant-b via RLS. SET LOCAL requires active transaction."""
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET LOCAL app.tenant_id = :tid"), {"tid": TENANT_B})
            yield conn


@pytest.fixture
def client():
    return httpx.Client(base_url=IRONBRIDGE_URL, timeout=15)


USER_NAME = "test-user"


@pytest.fixture(scope="session")
def recording_adapter() -> RecordingAdapter:
    """Session-scoped RecordingAdapter. Registered once, shared across all tests."""
    return RecordingAdapter.install()


@pytest.fixture
def fresh_recording(recording_adapter: RecordingAdapter) -> RecordingAdapter:
    """Per-test RecordingAdapter — cleared before each test."""
    recording_adapter.clear()
    return recording_adapter


def add_message(
    client: httpx.Client,
    thread_id: str,
    tenant_id: str,
    participant_id: str,
    participant_type: str,
    role: str,
    text: str,
    idempotency_key: str,
) -> dict:
    resp = client.post(
        f"/Thread/{thread_id}/add_message",
        json={
            "tenant_id": tenant_id,
            "user_name": USER_NAME,
            "participant_id": participant_id,
            "participant_type": participant_type,
            "role": role,
            "content": {"version": 1, "parts": [{"type": "text", "text": text}]},
            "idempotency_key": idempotency_key,
        },
    )
    resp.raise_for_status()
    return resp.json()


def create_thread(client: httpx.Client, thread_id: str, tenant_id: str) -> dict:
    resp = client.post(
        f"/Thread/{thread_id}/create",
        json={"tenant_id": tenant_id, "user_name": USER_NAME},
    )
    resp.raise_for_status()
    return resp.json()
