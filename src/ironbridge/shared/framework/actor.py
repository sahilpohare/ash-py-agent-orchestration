"""
Actor -- full context of who is doing what, threaded through the entire flow.

Identity + memberships (scoped roles) + origin (how this started) +
chain (on whose behalf). Flows through policies, guards, workflows, and audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .auth import Membership


@dataclass(frozen=True)
class Origin:
    """How this flow started."""
    channel: str = "unknown"
    source_type: str | None = None
    source_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class Actor:
    """Full execution context threaded through the entire flow."""

    id: str
    tenant_id: str = ""
    role: str = "viewer"

    # Scoped roles: user has role X on scope Y
    memberships: tuple = ()

    # Fine-grained permissions (legacy, still supported)
    scopes: frozenset[str] = field(default_factory=frozenset)

    origin: Origin = field(default_factory=Origin)
    on_behalf_of: "Actor | None" = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_system(self) -> bool:
        return self.role in ("system", "agent")

    @property
    def initiator(self) -> Actor:
        if self.on_behalf_of is not None:
            return self.on_behalf_of.initiator
        return self

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def role_on(self, scope: str) -> str | None:
        for m in self.memberships:
            if m.scope == scope:
                return m.role
        return None

    def roles_on(self, scope: str) -> list[str]:
        return [m.role for m in self.memberships if m.scope == scope]

    def as_agent(self, agent_id: str) -> Actor:
        return Actor(
            id=agent_id, tenant_id=self.tenant_id, role="agent",
            memberships=self.memberships, origin=self.origin, on_behalf_of=self,
        )

    def as_system(self, reason: str | None = None) -> Actor:
        return Actor(
            id=f"system:{reason}" if reason else "system",
            tenant_id=self.tenant_id, role="system",
            memberships=self.memberships, origin=self.origin, on_behalf_of=self,
        )

    def with_source(self, source_type: str, source_id: str) -> Actor:
        return Actor(
            id=self.id, tenant_id=self.tenant_id, role=self.role,
            memberships=self.memberships, scopes=self.scopes,
            origin=Origin(
                channel=self.origin.channel, source_type=source_type, source_id=source_id,
                ip=self.origin.ip, user_agent=self.origin.user_agent,
                idempotency_key=self.origin.idempotency_key,
            ),
            on_behalf_of=self.on_behalf_of, metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id, "tenant_id": self.tenant_id, "role": self.role,
            "memberships": [{"scope": m.scope, "role": m.role} for m in self.memberships],
            "origin": {"channel": self.origin.channel, "source_type": self.origin.source_type, "source_id": self.origin.source_id},
        }
        if self.on_behalf_of is not None:
            d["on_behalf_of"] = self.on_behalf_of.to_dict()
        return d


# Constructors

def from_request(user_id: str, tenant_id: str = "", role: str = "viewer",
                 memberships: tuple = (), scopes: frozenset[str] = frozenset(),
                 ip: str | None = None, user_agent: str | None = None) -> Actor:
    return Actor(id=user_id, tenant_id=tenant_id, role=role, memberships=memberships,
                 scopes=scopes, origin=Origin(channel="web_dashboard", ip=ip, user_agent=user_agent))

def from_webhook(channel: str, tenant_id: str, idempotency_key: str | None = None) -> Actor:
    return Actor(id="system", tenant_id=tenant_id, role="system",
                 origin=Origin(channel=channel, idempotency_key=idempotency_key))

def from_cron(tenant_id: str, job_name: str) -> Actor:
    return Actor(id=f"cron:{job_name}", tenant_id=tenant_id, role="system", origin=Origin(channel="cron"))
