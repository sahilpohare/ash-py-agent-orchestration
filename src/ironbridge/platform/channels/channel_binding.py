"""
ChannelBinding — maps a thread to the channel it arrived from.

Written on the first inbound message from a channel. Used by add_message
to route ASSISTANT responses back to the originating channel via Restate.

No restate_object — pure DB record, written directly in channel inbound handler.
"""

from datetime import UTC, datetime

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.db import tenant_session
from ironbridge.shared.framework import Resource

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class ChannelBinding(Resource):
    class Meta:
        tenant_scoped = True
        restate_object = False

    __tablename__ = "channel_bindings"
    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_channel_bindings_thread_id"),
    )

    id         : Mapped[str]      = mapped_column(String, primary_key=True, default=_cuid)
    thread_id  : Mapped[str]      = mapped_column(String, nullable=False, unique=True, index=True)
    channel_id : Mapped[str]      = mapped_column(String, nullable=False, index=True)
    created_at : Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def resolve_channel_for_thread(thread_id: str, tenant_id: str | None) -> str | None:
    """Return channel_id bound to this thread, or None."""
    if not tenant_id:
        return None
    with tenant_session(tenant_id) as db:
        row = db.execute(
            text("SELECT channel_id FROM channel_bindings WHERE thread_id = :tid LIMIT 1"),
            {"tid": thread_id},
        ).fetchone()
    return row[0] if row else None
