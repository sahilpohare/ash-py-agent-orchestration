from datetime import datetime, UTC
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import BigInteger, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, same_tenant,
    belongs_to, has_many,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class ParticipantType(StrEnum):
    HUMAN = "HUMAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class Message(Resource):
    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]
        conflict_columns = ("thread_id", "idempotency_key")
        conflict_action = "nothing"

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
    content: Mapped[dict] = mapped_column(JSON, nullable=False)
    raw_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    thread = belongs_to("Thread")


class Thread(Resource):
    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]

    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    messages = has_many(Message, key="thread_id")

    @action(kind=ActionKind.CREATE)
    @policy(same_tenant())
    def create(self) -> "Thread":
        if not self.id:
            self.id = _cuid()
        return self

    @action(kind=ActionKind.ACTION)
    @policy(same_tenant())
    def add_message(self, participant_id: str, participant_type: str, role: str, content: dict, idempotency_key: str) -> Message:
        return Message(
            id=_cuid(),
            thread_id=self.id,
            participant_id=participant_id,
            participant_type=ParticipantType(participant_type),
            role=MessageRole(role),
            content=content,
            idempotency_key=idempotency_key,
            position=-1,
        )
