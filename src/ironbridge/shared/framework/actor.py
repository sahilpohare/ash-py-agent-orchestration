"""
Actor -- full context of who is doing what, threaded through the entire flow.

Identity (who) + tenancy (which tenant) + origin (how this started) +
chain (on whose behalf). Flows through policies, guards, agent runs,
channel deliveries, and audit logs.

Every effect in the chain knows who started it and why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Origin:
    """How this flow started."""
    channel: str = "unknown"            # web_dashboard, twilio, nylas, elevenlabs, cron, api
    source_type: str | None = None      # call, enquiry, maintenance_job, lead, viewing
    source_id: str | None = None        # the resource that triggered this flow
    ip: str | None = None
    user_agent: str | None = None
    idempotency_key: str | None = None  # caller-supplied dedup key


@dataclass(frozen=True)
class Actor:
    """Full execution context threaded through the entire flow."""

    # Identity
    id: str
    tenant_id: str
    role: str                                           # superadmin, admin, operator, viewer, system, agent
    scopes: frozenset[str] = field(default_factory=frozenset)

    # How this flow started
    origin: Origin = field(default_factory=Origin)

    # Who initiated this if actor is derived (agent on behalf of user)
    on_behalf_of: Actor | None = None

    # Arbitrary context carried through the flow
    metadata: dict[str, Any] = field(default_factory=dict)

    # ---- queries ----

    @property
    def is_system(self) -> bool:
        return self.role in ("system", "agent")

    @property
    def initiator(self) -> Actor:
        """Walk the chain to the original actor who started this flow."""
        if self.on_behalf_of is not None:
            return self.on_behalf_of.initiator
        return self

    def has_role(self, *roles: str) -> bool:
        return self.role in roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    # ---- derivation ----

    def as_agent(self, agent_id: str) -> Actor:
        """Derive an agent actor that carries this actor as initiator."""
        return Actor(
            id=agent_id,
            tenant_id=self.tenant_id,
            role="agent",
            origin=self.origin,
            on_behalf_of=self,
        )

    def as_system(self, reason: str | None = None) -> Actor:
        """Derive a system actor that carries this actor as initiator."""
        return Actor(
            id=f"system:{reason}" if reason else "system",
            tenant_id=self.tenant_id,
            role="system",
            origin=self.origin,
            on_behalf_of=self,
        )

    def with_source(self, source_type: str, source_id: str) -> Actor:
        """Same actor, narrowed to a specific resource context."""
        return Actor(
            id=self.id,
            tenant_id=self.tenant_id,
            role=self.role,
            scopes=self.scopes,
            origin=Origin(
                channel=self.origin.channel,
                source_type=source_type,
                source_id=source_id,
                ip=self.origin.ip,
                user_agent=self.origin.user_agent,
                idempotency_key=self.origin.idempotency_key,
            ),
            on_behalf_of=self.on_behalf_of,
            metadata=self.metadata,
        )

    # ---- serialization ----

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "origin": {
                "channel": self.origin.channel,
                "source_type": self.origin.source_type,
                "source_id": self.origin.source_id,
            },
        }
        if self.on_behalf_of is not None:
            d["on_behalf_of"] = self.on_behalf_of.to_dict()
        return d


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

def from_request(
    user_id: str,
    tenant_id: str,
    role: str,
    scopes: frozenset[str] = frozenset(),
    ip: str | None = None,
    user_agent: str | None = None,
) -> Actor:
    """Build from an authenticated HTTP request (JWT/session)."""
    return Actor(
        id=user_id,
        tenant_id=tenant_id,
        role=role,
        scopes=scopes,
        origin=Origin(channel="web_dashboard", ip=ip, user_agent=user_agent),
    )


def from_webhook(
    channel: str,
    tenant_id: str,
    idempotency_key: str | None = None,
) -> Actor:
    """Build from an inbound webhook (Twilio, Nylas, Stripe, ElevenLabs)."""
    return Actor(
        id="system",
        tenant_id=tenant_id,
        role="system",
        origin=Origin(channel=channel, idempotency_key=idempotency_key),
    )


def from_cron(tenant_id: str, job_name: str) -> Actor:
    """Build from a scheduled job."""
    return Actor(
        id=f"cron:{job_name}",
        tenant_id=tenant_id,
        role="system",
        origin=Origin(channel="cron"),
    )
