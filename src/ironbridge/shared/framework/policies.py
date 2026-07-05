"""
Policies -- authorization checks on actions.

A policy answers: "Is this actor allowed to perform this action on this resource?"
Policies run BEFORE guards. A denied policy short-circuits.

Policies are pure functions. No DB queries, no I/O. They inspect the Actor
and the Resource instance and return ALLOW or DENY.

Usage:

    @action(kind=ActionKind.UPDATE)
    @policy(role_is("admin", "operator"))
    @policy(same_tenant())
    def approve_quote(self) -> "MaintenanceJob":
        ...

Stacking: multiple @policy decorators are AND-ed. All must ALLOW.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PolicyVerdict(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyDef:
    """A named authorization check."""
    name: str
    check: Callable[..., PolicyVerdict]  # (actor, resource) -> PolicyVerdict
    message: str = "Not authorized"


def policy(*policies: PolicyDef) -> Callable:
    """Decorator: attach policies to an action method."""
    def decorator(fn: Callable) -> Callable:
        existing = list(getattr(fn, "_policies", []))
        fn._policies = existing + list(policies)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        # Carry forward all metadata
        wrapper._policies = fn._policies
        for attr in ("__action__", "_guards"):
            if hasattr(fn, attr):
                setattr(wrapper, attr, getattr(fn, attr))
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Built-in policies
# ---------------------------------------------------------------------------

def role_is(*roles: str, message: str = "Insufficient role") -> PolicyDef:
    """Allow if actor has one of the given roles. System actors always pass."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        if actor.has_role(*roles):
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(name=f"role_is({','.join(roles)})", check=check, message=message)


def same_tenant(message: str = "Tenant mismatch") -> PolicyDef:
    """Allow if actor's tenant matches the resource's tenant."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        resource_tenant = getattr(resource, "tenant_id", None)
        if resource_tenant is None:
            return PolicyVerdict.ALLOW
        if actor.tenant_id == resource_tenant:
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(name="same_tenant", check=check, message=message)


def system_only(message: str = "System action only") -> PolicyDef:
    """Allow only system/agent actors."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(name="system_only", check=check, message=message)


def has_scope(*scopes: str, message: str = "Missing scope") -> PolicyDef:
    """Allow if actor has all of the given scopes."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        if actor.is_system:
            return PolicyVerdict.ALLOW
        if all(actor.has_scope(s) for s in scopes):
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(
        name=f"has_scope({','.join(scopes)})",
        check=check,
        message=message,
    )


def anyone() -> PolicyDef:
    """Allow any authenticated actor."""
    return PolicyDef(
        name="anyone",
        check=lambda actor, resource: PolicyVerdict.ALLOW,
    )


def initiator_is(*roles: str, message: str = "Initiator lacks role") -> PolicyDef:
    """Allow if the original initiator (walking on_behalf_of chain) has the role.
    Useful for agent actions that need to verify the human who started the flow."""
    def check(actor: Any, resource: Any) -> PolicyVerdict:
        init = actor.initiator
        if init.has_role(*roles):
            return PolicyVerdict.ALLOW
        return PolicyVerdict.DENY

    return PolicyDef(
        name=f"initiator_is({','.join(roles)})",
        check=check,
        message=message,
    )
