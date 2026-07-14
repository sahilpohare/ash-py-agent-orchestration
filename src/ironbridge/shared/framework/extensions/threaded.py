"""
Threaded extension -- attach a conversation thread to a resource.

    class Job(Resource):
        class Meta:
            extensions = [Threaded()]

What it does:
    - Injects thread_id column at class creation time
    - Auto-creates a Thread when the resource is created (via after_action hook)
    - Adds .thread property (returns ThreadHandle for add_text, add_event, get_history)
    - Adds .messages property (shorthand for thread.get_messages())

Configuration:
    Threaded()                           # defaults
    Threaded(fk="conversation_id")       # custom FK column name

Requires `ironbridge add threads` to scaffold Thread + Message resources + operations.

Usage in workflows:
    @workflow
    async def on_start(self, ctx, description: str):
        self.state = "sourcing"
        ctx.save()
        self.thread.add_text("Job opened: " + description)

Usage in actions:
    @action(kind=ActionKind.UPDATE)
    def approve(self) -> "Job":
        self.state = "approved"
        self.thread.add_event("state_changed", old="pending", new="approved")
        return self
"""
from __future__ import annotations

from typing import Any

from ironbridge.shared.framework.extension import Extension
from ironbridge.shared.framework.actions import ActionKind


class Threaded(Extension):

    def __init__(self, fk: str = "thread_id"):
        self.fk = fk

    def inject_columns(self, namespace: dict, meta: dict) -> None:
        """Inject thread_id column at class creation time."""
        meta["threaded"] = True
        meta["thread_fk"] = self.fk

        if self.fk not in namespace:
            from sqlalchemy import String
            from sqlalchemy.orm import mapped_column
            namespace[self.fk] = mapped_column(String, nullable=True, index=True)

    def on_resource(self, cls: type) -> None:
        """Add .thread and .messages properties."""
        fk = self.fk

        if not hasattr(cls, "thread"):
            @property
            def _thread(self_inner):
                tid = getattr(self_inner, fk, None)
                if not tid:
                    return None
                return _get_handle(tid)
            cls.thread = _thread

        if not hasattr(cls, "messages"):
            @property
            def _messages(self_inner):
                t = self_inner.thread
                return t.get_messages() if t else []
            cls.messages = _messages

    def after_action(self, actor: Any, resource: Any, action_name: str, result: Any) -> None:
        """Auto-create thread on resource creation."""
        actions = getattr(type(resource), "__actions__", {})
        action_meta = actions.get(action_name)
        if not action_meta or action_meta.kind != ActionKind.CREATE:
            return
        if getattr(resource, self.fk, None):
            return

        thread_id = _create_thread()
        if thread_id:
            setattr(resource, self.fk, thread_id)


def _get_handle(thread_id: str):
    """Lazy import to avoid circular deps with sessions component."""
    try:
        from ironbridge.shared.framework import registry
        # Try to import from the app's sessions module
        # The ThreadHandle class is framework-agnostic
        import importlib
        import sys

        # Find the sessions module in any registered app
        for mod_name, mod in sys.modules.items():
            if mod_name.endswith(".sessions.handle") and hasattr(mod, "ThreadHandle"):
                return mod.ThreadHandle(thread_id)

        # Fallback: inline minimal handle
        return _MinimalHandle(thread_id)
    except Exception:
        return _MinimalHandle(thread_id)


def _create_thread() -> str | None:
    """Create a thread via the operations module."""
    try:
        import sys
        for mod_name, mod in sys.modules.items():
            if mod_name.endswith(".sessions.operations") and hasattr(mod, "create_thread"):
                return mod.create_thread()

        # Fallback: direct insert
        from ironbridge.shared.db import SessionLocal
        from sqlalchemy import text
        from cuid2 import cuid_wrapper

        thread_id = cuid_wrapper()()
        db = SessionLocal()
        try:
            db.execute(text(
                "INSERT INTO threads (id, created_at, updated_at) VALUES (:id, now(), now())"
            ), {"id": thread_id})
            db.commit()
            return thread_id
        finally:
            db.close()
    except Exception:
        return None


class _MinimalHandle:
    """Fallback when sessions component isn't installed."""
    def __init__(self, thread_id: str):
        self._thread_id = thread_id

    @property
    def id(self) -> str:
        return self._thread_id

    def add_text(self, text: str, **kw) -> None:
        pass

    def add_event(self, event: str, **kw) -> None:
        pass

    def add_message(self, content: dict, **kw) -> None:
        pass

    def get_messages(self, limit: int = 200) -> list:
        return []

    def get_history(self, limit: int = 200) -> list:
        return []
