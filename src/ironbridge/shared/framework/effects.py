"""
Effects -- post-action hooks.

    @effect(notify_contractor, push_to_crm, update_lead)
    def approve_quote(self) -> "MaintenanceJob":
        self.status = "approved"
        return self

Effects are functions: (resource, actor) -> None
Durability is the function's concern (@step), not the effect's.
"""
from __future__ import annotations

import functools
from typing import Any, Callable


def effect(*fns: Callable) -> Callable:
    """Decorator: attach post-action hooks to an action.

        @effect(notify_contractor, push_to_crm)
        def approve_quote(self): ...
    """
    def decorator(action_fn: Callable) -> Callable:
        existing = list(getattr(action_fn, "_effects", []))
        action_fn._effects = existing + list(fns)

        @functools.wraps(action_fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return action_fn(*args, **kwargs)

        wrapper._effects = action_fn._effects
        for attr in ("__action__", "_policies", "_guards"):
            if hasattr(action_fn, attr):
                setattr(wrapper, attr, getattr(action_fn, attr))
        return wrapper

    return decorator


def get_effects(action_fn: Callable) -> list[Callable]:
    """Get effect functions attached to an action."""
    return list(getattr(action_fn, "_effects", []))


def run_effects(action_fn: Callable, resource: Any, actor: Any) -> None:
    """Execute all effects for an action. Called by derive layer after save."""
    for fn in get_effects(action_fn):
        try:
            fn(resource, actor)
        except Exception:
            import logging
            logging.getLogger("ironbridge.effects").exception(
                f"Effect {fn.__name__} failed for {type(resource).__name__}"
            )
