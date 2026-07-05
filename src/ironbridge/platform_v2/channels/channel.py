from datetime import datetime, UTC

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, guard,
    role_is, same_tenant, in_state,
    has_many,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class Channel(Resource):
    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]

    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    channel_type: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    default_agent_id: Mapped[str] = mapped_column(String, nullable=False, default="stub")
    status: Mapped[str] = mapped_column(String, nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    bindings = has_many("ChannelBinding", key="channel_id")

    @action(kind=ActionKind.CREATE)
    @policy(same_tenant())
    def create(self, name: str, channel_type: str, default_agent_id: str = "stub", config: dict | None = None) -> "Channel":
        self.name = name
        self.channel_type = channel_type
        self.default_agent_id = default_agent_id
        self.config = config or {}
        self.status = "ACTIVE"
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(same_tenant())
    def update(self, name: str | None = None, default_agent_id: str | None = None, config: dict | None = None) -> "Channel":
        if name is not None:
            self.name = name
        if default_agent_id is not None:
            self.default_agent_id = default_agent_id
        if config is not None:
            self.config = config
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(same_tenant())
    @guard(in_state("ACTIVE", field="status"))
    def deactivate(self) -> "Channel":
        self.status = "INACTIVE"
        return self
