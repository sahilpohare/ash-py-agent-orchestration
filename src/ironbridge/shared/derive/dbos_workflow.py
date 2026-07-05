"""
DBOS derive layer. Thin wiring between ironbridge primitives and DBOS.

    derive_dbos(MaintenanceJob)

Wires:
    ctx.receive(name)   ->  DBOS.recv(name, timeout)
    ctx.save()          ->  @DBOS.step save to DB
    ctx.sleep(duration) ->  DBOS.sleep(seconds)
    ctx.emit(fn, ...)   ->  @DBOS.step(fn, ...) post-handler
    Signal.send(id, p)  ->  DBOS.send(workflow_id, payload, topic)
    on_ handlers        ->  @DBOS.workflow() applied
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from dbos import DBOS

from ironbridge.shared.framework.actor import Actor
from ironbridge.shared.framework.signal import SignalDef, register_signal_transport
from ironbridge.shared.framework.workflow import SignalMessage, Workflow, WorkflowContext


def derive_dbos(workflow_cls: type[Workflow]) -> None:
    """Wire a Workflow class to DBOS. Call once at app startup."""

    # Decorate each on_ handler as a DBOS workflow
    for signal_name, handler_name in workflow_cls.__handlers__.items():
        handler = getattr(workflow_cls, handler_name)
        wrapped = DBOS.workflow()(handler)
        setattr(workflow_cls, handler_name, wrapped)

    # Register signal transport
    register_signal_transport(_send_signal)


def make_ctx(actor: Actor, resource: Any, initiating_actor: Actor | None = None) -> WorkflowContext:
    """Build a WorkflowContext wired to DBOS primitives."""
    return WorkflowContext(
        actor=actor,
        resource=resource,
        initiating_actor=initiating_actor,
        save_fn=_save,
        recv_fn=_recv,
        sleep_fn=_sleep,
    )


# ---------------------------------------------------------------------------
# DBOS-backed implementations of WorkflowContext methods
# ---------------------------------------------------------------------------

@DBOS.step()
def _save(resource: Any) -> None:
    """Persist resource. Durable step - won't re-execute on replay."""
    from ironbridge.shared.db import tenant_session
    from ironbridge.shared.derive.repository import SqlAlchemyRepository

    tenant_id = getattr(resource, "tenant_id", None)
    if not tenant_id:
        return
    with tenant_session(tenant_id) as db:
        repo = SqlAlchemyRepository(db, type(resource))
        repo.save(resource)


async def _recv(signal_names: tuple[str, ...], timeout: timedelta | None) -> SignalMessage | None:
    """Receive a signal. Maps to DBOS.recv()."""
    timeout_secs = timeout.total_seconds() if timeout else None

    if len(signal_names) == 1:
        result = DBOS.recv(signal_names[0], timeout_seconds=timeout_secs)
        if result is None:
            return None
        return _to_signal_message(signal_names[0], result)

    # Multiple: DBOS doesn't have native multi-topic recv.
    # Use short-poll across topics.
    import asyncio
    elapsed = 0.0
    limit = timeout_secs or 86400
    interval = 0.5
    while elapsed < limit:
        for name in signal_names:
            result = DBOS.recv(name, timeout_seconds=0)
            if result is not None:
                return _to_signal_message(name, result)
        await asyncio.sleep(interval)
        elapsed += interval
    return None


async def _sleep(until: datetime | None = None, duration: timedelta | None = None) -> None:
    """Durable sleep. Maps to DBOS.sleep()."""
    if duration:
        DBOS.sleep(duration.total_seconds())
    elif until:
        delta = (until - datetime.now(timezone.utc)).total_seconds()
        if delta > 0:
            DBOS.sleep(delta)


def _send_signal(signal_def: SignalDef, resource_id: str | None, payload: Any, actor: Any = None) -> None:
    """Send a signal. Maps to DBOS.send() for existing workflows, DBOS.start_workflow() for CREATE."""
    from ironbridge.shared.framework.actions import ActionKind

    message = {"payload": payload}
    if actor and hasattr(actor, "to_dict"):
        message["actor"] = actor.to_dict()

    if signal_def.kind == ActionKind.CREATE:
        # Start a new workflow
        cls = signal_def.owner_cls
        handler_name = cls.get_handler(signal_def.name)
        if handler_name:
            handler = getattr(cls, handler_name)
            DBOS.start_workflow(handler, message)
    else:
        # Send to running workflow
        if resource_id is None:
            raise ValueError(f"resource_id required for signal '{signal_def.name}'")
        # workflow_id stored on the resource
        workflow_id = _get_workflow_id(signal_def.owner_cls, resource_id)
        DBOS.send(workflow_id, message, signal_def.name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_signal_message(signal_name: str, result: Any) -> SignalMessage:
    if isinstance(result, dict):
        return SignalMessage(
            signal=signal_name,
            payload=result.get("payload", result),
            actor=_dict_to_actor(result.get("actor")),
        )
    return SignalMessage(signal=signal_name, payload=result, actor=None)


def _dict_to_actor(data: dict | None) -> Actor | None:
    if not data:
        return None
    from ironbridge.shared.framework.actor import Origin
    return Actor(
        id=data["id"],
        tenant_id=data["tenant_id"],
        role=data["role"],
        origin=Origin(
            channel=data.get("origin", {}).get("channel", "unknown"),
            source_type=data.get("origin", {}).get("source_type"),
            source_id=data.get("origin", {}).get("source_id"),
        ),
        on_behalf_of=_dict_to_actor(data.get("on_behalf_of")),
    )


def _get_workflow_id(cls: type, resource_id: str) -> str:
    """Look up workflow_id from the resource. The resource stores it."""
    from ironbridge.shared.db import SessionLocal
    from ironbridge.shared.derive.repository import SqlAlchemyRepository

    db = SessionLocal()
    try:
        repo = SqlAlchemyRepository(db, cls)
        resource = repo.find_by_id(resource_id)
        if resource and hasattr(resource, "workflow_id"):
            return resource.workflow_id
        raise RuntimeError(f"No workflow_id on {cls.__name__} id={resource_id}")
    finally:
        db.close()
