from datetime import datetime, UTC
from enum import StrEnum

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework import (
    Resource, action, ActionKind, default_action,
    policy, guard,
    role_is, same_tenant, in_state,
)

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)


class AgentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Agent(Resource):
    """Agent definition. Owns config, not execution."""

    class Meta:
        tenant_scoped = True
        default_actions = ["get", "list"]

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    instructions: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    tools: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[AgentStatus] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @action(kind=ActionKind.CREATE)
    @policy(role_is("admin", "system"))
    @policy(same_tenant())
    def create(self, name: str, model: str, instructions: str | None = None, tools: dict | None = None) -> "Agent":
        self.name = name
        self.model = model
        self.instructions = instructions
        self.tools = tools or {}
        self.status = AgentStatus.ACTIVE
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(role_is("admin", "system"))
    @policy(same_tenant())
    def update(self, name: str | None = None, instructions: str | None = None, model: str | None = None, tools: dict | None = None) -> "Agent":
        if name is not None:
            self.name = name
        if instructions is not None:
            self.instructions = instructions
        if model is not None:
            self.model = model
        if tools is not None:
            self.tools = tools
        return self

    @action(kind=ActionKind.UPDATE)
    @policy(role_is("admin", "system"))
    @guard(in_state("ACTIVE", field="status"))
    def deactivate(self) -> "Agent":
        self.status = AgentStatus.INACTIVE
        return self
