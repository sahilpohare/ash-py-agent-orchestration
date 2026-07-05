"""
Guards -- precondition checks on actions.

A guard answers: "Is this action valid given the current state of the resource?"
Guards run AFTER policies pass. A failed guard means the actor is authorized
but the resource isn't in the right state.

Guards are pure functions. No DB queries, no I/O. They inspect the Resource
instance (and optionally the action kwargs) and return True/False.

Usage:

    @action(kind=ActionKind.UPDATE)
    @guard(in_state("quote_approval"))
    @guard(field_set("quote_amount"))
    def approve_quote(self) -> "MaintenanceJob":
        ...

Stacking: multiple @guard decorators are AND-ed. All must pass.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuardDef:
    """A named precondition check."""
    name: str
    check: Callable[..., bool]  # (resource, **action_kwargs) -> bool
    message: str = "Action not allowed"


def guard(*guards: GuardDef) -> Callable:
    """Decorator: attach guards to an action method."""
    def decorator(fn: Callable) -> Callable:
        existing = list(getattr(fn, "_guards", []))
        fn._guards = existing + list(guards)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        # Carry forward all metadata
        wrapper._guards = fn._guards
        for attr in ("__action__", "_policies"):
            if hasattr(fn, attr):
                setattr(wrapper, attr, getattr(fn, attr))
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Built-in guards
# ---------------------------------------------------------------------------

def in_state(*states: str, field: str = "state") -> GuardDef:
    """Allow only if resource's state field is one of the given values."""
    return GuardDef(
        name=f"in_state({','.join(states)})",
        check=lambda resource, **kw: getattr(resource, field, None) in states,
        message=f"Must be in state: {', '.join(states)}",
    )


def not_in_state(*states: str, field: str = "state") -> GuardDef:
    """Block if resource's state field is one of the given values."""
    return GuardDef(
        name=f"not_in_state({','.join(states)})",
        check=lambda resource, **kw: getattr(resource, field, None) not in states,
        message=f"Must not be in state: {', '.join(states)}",
    )


def not_deleted() -> GuardDef:
    """Block if soft-deleted."""
    return GuardDef(
        name="not_deleted",
        check=lambda resource, **kw: not getattr(resource, "is_deleted", False),
        message="Resource is deleted",
    )


def field_set(*fields: str) -> GuardDef:
    """Block if any of the named fields are None."""
    return GuardDef(
        name=f"field_set({','.join(fields)})",
        check=lambda resource, **kw: all(
            getattr(resource, f, None) is not None for f in fields
        ),
        message=f"Required fields missing: {', '.join(fields)}",
    )


def field_equals(field: str, value: Any) -> GuardDef:
    """Block unless field has the expected value."""
    return GuardDef(
        name=f"field_equals({field}={value})",
        check=lambda resource, **kw: getattr(resource, field, None) == value,
        message=f"{field} must be {value}",
    )


def field_true(field: str) -> GuardDef:
    """Block unless field is truthy."""
    return GuardDef(
        name=f"field_true({field})",
        check=lambda resource, **kw: bool(getattr(resource, field, False)),
        message=f"{field} must be true",
    )


def custom(name: str, check: Callable[..., bool], message: str = "Guard failed") -> GuardDef:
    """Arbitrary guard. check(resource, **action_kwargs) -> bool."""
    return GuardDef(name=name, check=check, message=message)
