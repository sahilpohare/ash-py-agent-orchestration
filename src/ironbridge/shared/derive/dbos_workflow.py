"""
DBOS derive layer.

    from ironbridge.shared.derive.dbos_workflow import derive_all

    derive_all(graph)

Wires:
    @step functions           -> @DBOS.step(retries, max_attempts)
    Workflow handlers         -> registered as DBOS workflows (not dynamically generated)
    Signal.send()             -> DBOS.start_workflow / DBOS.send_async
    self.save()               -> @DBOS.step persist to DB
    await self.signal         -> DBOS.recv_async
    @effect(fn, durable=True) -> wrapped as @DBOS.step
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

from dbos import DBOS

from ironbridge.shared.framework import (
    ActionKind, Actor, Origin, ResourceGraph,
    register_signal_transport, registry,
)
from ironbridge.shared.framework.effects import get_effects
from ironbridge.shared.framework.signal import SignalDef
from ironbridge.shared.framework.step import is_step_fn, get_step_config
from ironbridge.shared.framework.workflow import SignalMessage, WorkflowContext


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Maps resource_id -> DBOS workflow_id for mid-workflow signal routing.
# Persisted in the resource's workflow_id column; this dict is a runtime cache.
_active_workflows: dict[str, str] = {}

# Maps resource class name -> DBOS workflow function
_workflow_fns: dict[str, Any] = {}


def derive_all(graph: ResourceGraph) -> None:
    """Wire everything to DBOS. Call once at startup after graph.build()."""
    _derive_steps(graph)
    _derive_effects(graph)
    _derive_workflows(graph)
    _register_transport()


# ---------------------------------------------------------------------------
# 1. @step -> @DBOS.step
# ---------------------------------------------------------------------------

def _derive_steps(graph: ResourceGraph) -> None:
    """Wrap all @step-decorated functions with @DBOS.step."""
    seen: set[int] = set()
    for cls in graph.all().values():
        mod = sys.modules.get(cls.__module__)
        if not mod or id(mod) in seen:
            continue
        seen.add(id(mod))
        for name in dir(mod):
            fn = getattr(mod, name, None)
            if callable(fn) and is_step_fn(fn):
                cfg = get_step_config(fn)
                wrapped = DBOS.step(
                    retries_allowed=cfg["retries"] > 0,
                    max_attempts=1 + cfg["retries"],
                )(fn)
                setattr(mod, name, wrapped)


# ---------------------------------------------------------------------------
# 2. @effect(fn, durable=True) -> wrap fn with @DBOS.step
# ---------------------------------------------------------------------------

def _derive_effects(graph: ResourceGraph) -> None:
    """Wrap durable effect functions with @DBOS.step."""
    wrapped_fns: set[int] = set()
    for cls in graph.all().values():
        for action_name, action_meta in getattr(cls, "__actions__", {}).items():
            for effect_def in get_effects(action_meta.fn):
                if effect_def.durable and id(effect_def.fn) not in wrapped_fns:
                    wrapped_fns.add(id(effect_def.fn))
                    original = effect_def.fn
                    effect_def.fn = DBOS.step()(original)


# ---------------------------------------------------------------------------
# 3. Workflow handlers -> DBOS workflows
# ---------------------------------------------------------------------------

@DBOS.step()
def _persist(cls_name: str, state: dict) -> None:
    """Durable save. Exactly-once via DBOS journal."""
    from ironbridge.shared.db import SessionLocal
    from ironbridge.shared.derive.repository import SqlAlchemyRepository

    cls = registry.get(cls_name)
    if not cls:
        return
    db = SessionLocal()
    try:
        repo = SqlAlchemyRepository(db, cls)
        instance = cls()
        for k, v in state.items():
            if hasattr(instance, k):
                setattr(instance, k, v)
        repo.save(instance)
        db.commit()
    finally:
        db.close()


def _serialize(resource: Any) -> dict:
    state = {}
    for key in vars(resource):
        if not key.startswith("_"):
            val = getattr(resource, key)
            if not callable(val):
                if isinstance(val, datetime):
                    val = val.isoformat()
                state[key] = val
    return state


def _make_workflow_fn(cls: type, handler: Any) -> Any:
    """
    Create a named DBOS workflow function for a resource class.

    Each resource gets its own module-level-style function with a unique name.
    No closures in loops. No naming collisions.
    """
    cls_name = cls.__name__

    async def workflow_fn(payload: dict) -> None:
        instance = cls()

        # Wire self.save()
        def _save(resource=None):
            _persist(cls_name, _serialize(instance))
            rid = getattr(instance, "id", None)
            if rid:
                instance.workflow_id = DBOS.workflow_id
                _active_workflows[rid] = DBOS.workflow_id

        instance.save = _save

        # Wire self.signal.recv() via BoundSignal
        async def _recv_fn(names, timeout):
            secs = timeout.total_seconds() if timeout else 86400
            for n in names:
                r = await DBOS.recv_async(n, timeout_seconds=secs)
                if r is not None:
                    return SignalMessage(signal=n, payload=r, actor=None)
            return None

        instance._workflow_recv_fn = _recv_fn
        instance._open_signals = set()

        # Build ctx for backwards compat
        ctx = WorkflowContext(
            actor=Actor(id="system", tenant_id="system", role="system", origin=Origin()),
            resource=instance,
            save_fn=_save,
            recv_fn=_recv_fn,
        )

        # Run handler (auto-detect ctx param)
        import inspect as _inspect
        _sig = _inspect.signature(handler)
        if "ctx" in _sig.parameters:
            await handler(instance, ctx, **payload)
        else:
            await handler(instance, **payload)

        # Cleanup
        _active_workflows.pop(getattr(instance, "id", ""), None)

    # Unique name prevents DBOS duplicate registration
    workflow_fn.__name__ = f"{cls_name}__workflow"
    workflow_fn.__qualname__ = f"{cls_name}__workflow"

    # Register with DBOS
    return DBOS.workflow()(workflow_fn)


def _derive_workflows(graph: ResourceGraph) -> None:
    """Register DBOS workflow for each Resource with a CREATE signal."""
    for cls_name, cls in graph.all().items():
        if not (hasattr(cls, "get_entry_handler") and cls.get_entry_handler()):
            continue
        handler = cls.get_handler(cls.get_entry_handler())
        if not handler:
            continue

        wf_fn = _make_workflow_fn(cls, handler)
        _workflow_fns[cls_name] = wf_fn


# ---------------------------------------------------------------------------
# 4. Signal transport
# ---------------------------------------------------------------------------

def _register_transport() -> None:
    register_signal_transport(_transport)


def _transport(signal_def: SignalDef, resource_id: str | None, payload: Any, actor: Any = None) -> None:
    cls_name = signal_def.owner_cls.__name__

    if signal_def.kind == ActionKind.CREATE:
        wf = _workflow_fns.get(cls_name)
        if wf:
            DBOS.start_workflow(wf, payload or {})
    else:
        # Mid-workflow signal: look up workflow_id from runtime cache,
        # fall back to DB if not in cache (process restarted)
        wf_id = _active_workflows.get(resource_id) if resource_id else None
        if not wf_id and resource_id:
            wf_id = _load_workflow_id(signal_def.owner_cls, resource_id)
        if wf_id:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(DBOS.send_async(wf_id, payload or {}, signal_def.name))
            except RuntimeError:
                # No running loop (called from sync context)
                DBOS.send(wf_id, payload or {}, signal_def.name)


def _load_workflow_id(cls: type, resource_id: str) -> str | None:
    """Load workflow_id from DB. Fallback when runtime cache misses (after restart)."""
    try:
        from ironbridge.shared.db import SessionLocal
        from ironbridge.shared.derive.repository import SqlAlchemyRepository

        db = SessionLocal()
        try:
            repo = SqlAlchemyRepository(db, cls)
            resource = repo.find_by_id(resource_id)
            if resource and hasattr(resource, "workflow_id"):
                wf_id = resource.workflow_id
                if wf_id:
                    _active_workflows[resource_id] = wf_id  # cache for next time
                return wf_id
        finally:
            db.close()
    except Exception:
        pass
    return None
