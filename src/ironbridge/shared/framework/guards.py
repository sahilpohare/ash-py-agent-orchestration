"""
Guards -- precondition checks on actions.

A guard answers: "Is this action valid given the current state?"
Guards run AFTER policies pass. A failed guard means the actor is authorized
but the precondition isn't met.

Guards can check the resource itself (default) or a related resource via on=:

    @guard(in_state("quote_approval"))                        # checks self.status
    @guard(field_equals("active", True, on="branch"))         # checks self.branch.active
    @guard(field_set("landlord_id", on="property"))           # checks self.property.landlord_id

Dual-mode: guards work as decorators (framework checks) AND direct calls (workflow logic):

    # Decorator (framework enforces before action runs)
    @guard(in_state("quote_approval"))
    def approve(self): ...

    # Direct call (workflow logic)
    if in_state(self, "quote_approval"):
        ...
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

        wrapper._guards = fn._guards
        for attr in ("__action__", "_policies"):
            if hasattr(fn, attr):
                setattr(wrapper, attr, getattr(fn, attr))
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Resolve target: self or a related resource via on=
# ---------------------------------------------------------------------------

def _resolve(resource: Any, on: str | None) -> Any:
    """Resolve guard target. None = self. String = relationship name."""
    if on is None:
        return resource
    target = getattr(resource, on, None)
    return target


def _on_suffix(on: str | None) -> str:
    return f", on={on}" if on else ""


# ---------------------------------------------------------------------------
# Built-in guards (all support on= and dual-mode)
# ---------------------------------------------------------------------------

def in_state(*args, field: str = "state", on: str | None = None) -> GuardDef | bool:
    """
    Check state field equals one of the given values.

    Decorator mode:  @guard(in_state("quote_approval"))
    Direct mode:     in_state(resource, "quote_approval")
    Related:         @guard(in_state("active", field="status", on="branch"))
    """
    # Dual-mode: if first arg isn't a string, it's a resource instance (direct call)
    if args and not isinstance(args[0], str):
        resource, *states = args
        target = _resolve(resource, on)
        if target is None:
            return False
        return getattr(target, field, None) in states

    # Decorator mode: all args are state strings
    states = args
    def check(resource, **kw):
        target = _resolve(resource, on)
        if target is None:
            return False
        return getattr(target, field, None) in states
    return GuardDef(
        name=f"in_state({','.join(states)}{_on_suffix(on)})",
        check=check,
        message=f"Must be in state: {', '.join(states)}",
    )


def not_in_state(*args, field: str = "state", on: str | None = None) -> GuardDef | bool:
    """Block if state field is one of the given values."""
    if args and not isinstance(args[0], str):
        resource, *states = args
        target = _resolve(resource, on)
        if target is None:
            return True
        return getattr(target, field, None) not in states

    states = args
    def check(resource, **kw):
        target = _resolve(resource, on)
        if target is None:
            return True
        return getattr(target, field, None) not in states
    return GuardDef(
        name=f"not_in_state({','.join(states)}{_on_suffix(on)})",
        check=check,
        message=f"Must not be in state: {', '.join(states)}",
    )


def not_deleted(on: str | None = None) -> GuardDef:
    """Block if soft-deleted."""
    def check(resource, **kw):
        target = _resolve(resource, on)
        if target is None:
            return False
        return not getattr(target, "is_deleted", False)
    return GuardDef(
        name=f"not_deleted{_on_suffix(on)}",
        check=check,
        message="Resource is deleted",
    )


def field_set(*args, on: str | None = None) -> GuardDef | bool:
    """Block if any of the named fields are None."""
    if args and hasattr(args[0], "__dict__") and not isinstance(args[0], str):
        resource = args[0]
        fields = args[1:]
        target = _resolve(resource, on)
        if target is None:
            return False
        return all(getattr(target, f, None) is not None for f in fields)

    fields = args
    def check(resource, **kw):
        target = _resolve(resource, on)
        if target is None:
            return False
        return all(getattr(target, f, None) is not None for f in fields)
    return GuardDef(
        name=f"field_set({','.join(fields)}{_on_suffix(on)})",
        check=check,
        message=f"Required fields missing: {', '.join(fields)}",
    )


def field_equals(*args, on: str | None = None) -> GuardDef | bool:
    """Block unless field has the expected value."""
    # Direct mode: field_equals(resource, "field", value)
    if len(args) == 3 and hasattr(args[0], "__dict__") and not isinstance(args[0], str):
        resource, f, value = args
        target = _resolve(resource, on)
        if target is None:
            return False
        return getattr(target, f, None) == value

    # Decorator mode: field_equals("field", value)
    f, value = args[0], args[1]
    def check(resource, **kw):
        target = _resolve(resource, on)
        if target is None:
            return False
        return getattr(target, f, None) == value
    return GuardDef(
        name=f"field_equals({f}={value}{_on_suffix(on)})",
        check=check,
        message=f"{f} must be {value}",
    )


def field_true(*args, on: str | None = None) -> GuardDef | bool:
    """Block unless field is truthy."""
    if args and hasattr(args[0], "__dict__") and not isinstance(args[0], str):
        resource, f = args[0], args[1]
        target = _resolve(resource, on)
        if target is None:
            return False
        return bool(getattr(target, f, False))

    f = args[0]
    def check(resource, **kw):
        target = _resolve(resource, on)
        if target is None:
            return False
        return bool(getattr(target, f, False))
    return GuardDef(
        name=f"field_true({f}{_on_suffix(on)})",
        check=check,
        message=f"{f} must be true",
    )


def custom(name: str, check: Callable[..., bool], message: str = "Guard failed") -> GuardDef:
    """Arbitrary guard. check(resource, **action_kwargs) -> bool."""
    return GuardDef(name=name, check=check, message=message)
