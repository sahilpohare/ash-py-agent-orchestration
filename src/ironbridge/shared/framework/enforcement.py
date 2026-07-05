"""
Enforcement -- runs policies then guards before an action executes.

Called by whatever invocation layer you use (FastAPI route handler,
Restate derived handler, DBOS workflow, plain function call).

    enforce(actor, resource, SomeResource.some_action, **kwargs)

Raises PolicyDenied (-> 403) or GuardFailed (-> 409) on first failure.
"""
from __future__ import annotations

from typing import Any, Callable

from .actor import Actor
from .guards import GuardDef
from .policies import PolicyDef, PolicyVerdict


class PolicyDenied(Exception):
    """Actor is not authorized for this action. Maps to HTTP 403."""
    def __init__(self, policy: PolicyDef, actor: Actor) -> None:
        self.policy_name = policy.name
        self.actor_id = actor.id
        self.actor_role = actor.role
        super().__init__(policy.message)


class GuardFailed(Exception):
    """Resource is not in the right state for this action. Maps to HTTP 409."""
    def __init__(self, guard: GuardDef) -> None:
        self.guard_name = guard.name
        super().__init__(guard.message)


def enforce(actor: Actor, resource: Any, action_fn: Callable, **kwargs: Any) -> None:
    """
    Run all policies then all guards for an action.

    Policies (authorization): checked first. Any DENY stops immediately.
    Guards (preconditions): checked second. Any failure stops immediately.

    action_fn: the decorated method (has _policies and _guards attributes).
    resource: the Resource instance being acted on.
    kwargs: the action's keyword arguments (passed to guard checks).
    """
    # Policies: who
    for p in _get_policies(action_fn):
        if p.check(actor, resource) == PolicyVerdict.DENY:
            raise PolicyDenied(p, actor)

    # Guards: what state
    for g in _get_guards(action_fn):
        if not g.check(resource, **kwargs):
            raise GuardFailed(g)


def check_policies(actor: Actor, resource: Any, action_fn: Callable) -> list[PolicyDef]:
    """Return list of failed policies without raising. Empty = all passed."""
    return [
        p for p in _get_policies(action_fn)
        if p.check(actor, resource) == PolicyVerdict.DENY
    ]


def check_guards(resource: Any, action_fn: Callable, **kwargs: Any) -> list[GuardDef]:
    """Return list of failed guards without raising. Empty = all passed."""
    return [
        g for g in _get_guards(action_fn)
        if not g.check(resource, **kwargs)
    ]


def can(actor: Actor, resource: Any, action_fn: Callable, **kwargs: Any) -> bool:
    """Check if an action would be allowed without raising."""
    return (
        not check_policies(actor, resource, action_fn)
        and not check_guards(resource, action_fn, **kwargs)
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _get_policies(action_fn: Callable) -> list[PolicyDef]:
    return getattr(action_fn, "_policies", [])


def _get_guards(action_fn: Callable) -> list[GuardDef]:
    return getattr(action_fn, "_guards", [])
