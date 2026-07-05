from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, same_tenant,
    belongs_to,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class ChannelBinding(Resource):
    """Maps a thread to the channels it's visible on. Many-to-many."""

    class Meta:
        tenant_scoped = True

    __tablename__ = "channel_bindings"
    __table_args__ = (
        UniqueConstraint("thread_id", "channel_id", name="uq_channel_bindings_thread_channel"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    thread_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    thread = belongs_to("Thread", key="thread_id")
    channel = belongs_to("Channel", key="channel_id")

    get = default_action(ActionKind.READ)

    @action(kind=ActionKind.CREATE)
    @policy(same_tenant())
    def bind(self, thread_id: str, channel_id: str) -> "ChannelBinding":
        self.id = _cuid()
        self.thread_id = thread_id
        self.channel_id = channel_id
        return self
