"""
Subscriptions -- cross-domain reactions to resource events.

Subscribe to actions/signals on any resource without coupling domains.

    @on(MaintenanceJob, "open")
    async def notify_on_job_open(resource, actor):
        await notify_staff(resource.id, "job_opened")

    @on(Call, "*")  # all actions
    async def audit_all_call_actions(resource, action_name, actor):
        await write_audit(actor, action_name, resource.id)

    @on(MaintenanceJob, "approval")  # signal
    async def log_approval(resource, actor, payload):
        log.info(f"Job {resource.id} approval signal from {actor.id}")

Subscriptions run post-commit. With DBOS, they're durable steps.
Without DBOS, fire-and-forget via asyncio.create_task.
"""
from __future__ import annotations

import asyncio
import inspect
from collections import defaultdict
from typing import Any, Callable


# Registry: {(resource_class_name, action_or_signal_name): [handler, ...]}
_subscriptions: dict[tuple[str, str], list[Callable]] = defaultdict(list)

# Wildcard subscriptions: {resource_class_name: [handler, ...]}
_wildcard_subscriptions: dict[str, list[Callable]] = defaultdict(list)


def on(resource_cls: type, action_or_signal: str) -> Callable:
    """
    Decorator: subscribe to an action or signal on a resource.

    @on(MaintenanceJob, "open")        # specific action/signal
    @on(MaintenanceJob, "*")           # all actions and signals
    """
    def decorator(fn: Callable) -> Callable:
        cls_name = resource_cls.__name__
        if action_or_signal == "*":
            _wildcard_subscriptions[cls_name].append(fn)
        else:
            _subscriptions[(cls_name, action_or_signal)].append(fn)
        return fn
    return decorator


async def notify(
    resource: Any,
    event_name: str,
    actor: Any = None,
    result: Any = None,
    payload: Any = None,
) -> None:
    """
    Fire all subscriptions for a resource event.
    Called by the framework after an action completes or a signal dispatches.
    """
    cls_name = type(resource).__name__

    handlers = list(_subscriptions.get((cls_name, event_name), []))
    handlers.extend(_wildcard_subscriptions.get(cls_name, []))

    for handler in handlers:
        kwargs = _build_kwargs(handler, resource, event_name, actor, result, payload)
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(**kwargs)
            else:
                handler(**kwargs)
        except Exception:
            # Subscriptions should not break the main flow
            # Log and continue
            import logging
            logging.getLogger("ironbridge.subscriptions").exception(
                f"Subscription handler {handler.__name__} failed for {cls_name}.{event_name}"
            )


def _build_kwargs(
    handler: Callable,
    resource: Any,
    event_name: str,
    actor: Any,
    result: Any,
    payload: Any,
) -> dict:
    """Build kwargs based on what the handler accepts."""
    sig = inspect.signature(handler)
    params = set(sig.parameters.keys())

    kwargs: dict[str, Any] = {}

    if "resource" in params:
        kwargs["resource"] = resource
    if "actor" in params:
        kwargs["actor"] = actor
    if "action_name" in params:
        kwargs["action_name"] = event_name
    if "signal_name" in params:
        kwargs["signal_name"] = event_name
    if "event_name" in params:
        kwargs["event_name"] = event_name
    if "result" in params:
        kwargs["result"] = result
    if "payload" in params:
        kwargs["payload"] = payload

    return kwargs


def get_subscriptions(cls_name: str, event_name: str) -> list[Callable]:
    """Get all handlers for a specific event (for testing/introspection)."""
    handlers = list(_subscriptions.get((cls_name, event_name), []))
    handlers.extend(_wildcard_subscriptions.get(cls_name, []))
    return handlers


def clear_subscriptions() -> None:
    """Clear all subscriptions (for testing)."""
    _subscriptions.clear()
    _wildcard_subscriptions.clear()
