from datetime import UTC, datetime

from cuid2 import cuid_wrapper
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.db import tenant_session
from ironbridge.shared.derive.repository import SqlAlchemyRepository
from ironbridge.shared.framework import ActionKind, Resource, action

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class Channel(Resource):
    """
    A tenant-owned channel — the configuration for one inbound/outbound
    integration (cli, telegram, whatsapp, webhook, …).

    channel_type     — adapter slug, matches ChannelDelivery adapter registry
    config           — provider credentials / settings (bot token, etc.)
    default_agent_id — agent kicked off for inbound messages on this channel
    """

    class Meta:
        tenant_scoped = True
        restate_object = True

    __tablename__ = "channels"

    id               : Mapped[str]            = mapped_column(String, primary_key=True, default=_cuid)
    name             : Mapped[str]            = mapped_column(String, nullable=False)
    channel_type     : Mapped[str]            = mapped_column(String, nullable=False)
    config           : Mapped[dict | None] = mapped_column(JSON, nullable=True)
    default_agent_id : Mapped[str]            = mapped_column(String, nullable=False, default="stub")
    status           : Mapped[str]            = mapped_column(String, nullable=False, default="ACTIVE")
    created_at       : Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at       : Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @action(kind=ActionKind.CREATE)
    def create(
        self,
        name: str,
        channel_type: str,
        default_agent_id: str = "stub",
        config: dict | None = None,
    ) -> "Channel":
        self.name = name
        self.channel_type = channel_type
        self.default_agent_id = default_agent_id
        self.config = config or {}
        self.status = "ACTIVE"
        return self

    @action(kind=ActionKind.UPDATE)
    def update(
        self,
        name: str | None = None,
        default_agent_id: str | None = None,
        config: dict | None = None,
    ) -> "Channel":
        if name is not None:
            self.name = name
        if default_agent_id is not None:
            self.default_agent_id = default_agent_id
        if config is not None:
            self.config = config
        return self

    @action(kind=ActionKind.UPDATE)
    def deactivate(self) -> "Channel":
        self.status = "INACTIVE"
        return self

    @action(kind=ActionKind.READ)
    def get(self) -> "Channel":
        return self


def resolve_agent_for_channel(channel_id: str, tenant_id: str | None) -> str:
    """Return default_agent_id for this channel, or 'stub'."""
    if not channel_id or not tenant_id:
        return "stub"
    with tenant_session(tenant_id) as db:
        repo = SqlAlchemyRepository(db, Channel)
        channel = repo.find_by_id(channel_id)
        return channel.default_agent_id if channel else "stub"
