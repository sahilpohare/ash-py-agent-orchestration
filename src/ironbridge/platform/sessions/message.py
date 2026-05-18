from datetime import UTC, datetime
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import Resource

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class ParticipantType(StrEnum):
    """
    What kind of actor sent this message.
    participant_id is a free string — "alice", "agent-run-xyz", "system".
    No FK. Humans and agents are equal participants.
    """

    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class Message(Resource):
    class Meta:
        tenant_scoped = True
        restate_object = False

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("thread_id", "idempotency_key", name="uq_messages_thread_idempotency"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    thread_id: Mapped[str] = mapped_column(
        String, ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    participant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    participant_type: Mapped[ParticipantType] = mapped_column(String, nullable=False)
    role: Mapped[MessageRole] = mapped_column(String, nullable=False)
    # Normalized display model — provider-agnostic parts array.
    # Format: {"version": 1, "parts": [{"type": "text", "text": "..."}, ...]}
    # Part types: text | tool_call | reasoning | event | response_request | response_reply
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Full LLM API response, stored as-is. Null for human messages and system events.
    # Preserves token usage, finish reason, tool call IDs, model version.
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Monotonic per thread. Assigned by derive/restate.py via ctx position counter.
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Caller-supplied dedup key. UNIQUE(thread_id, idempotency_key) in DB.
    # ON CONFLICT DO NOTHING — storage-layer dedup, safe for Restate replay.
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
