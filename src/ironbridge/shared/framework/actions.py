from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ActionKind(StrEnum):
    """
    Ash-inspired action types. Infrastructure derives DB behaviour and
    Restate handler concurrency from the kind — no need to specify both.

    CREATE  — builds a new record (INSERT). Exclusive Restate handler.
    READ    — queries, no writes. Shared Restate handler.
    UPDATE  — modifies existing record (UPSERT). Exclusive Restate handler.
    DESTROY — deletes record. Exclusive Restate handler.
    ACTION  — custom, no implicit DB op. Domain controls writes + effects
              via ActionContext. Exclusive Restate handler.
    STREAM  — SSE / long-poll. Shared Restate handler, no write.

    Restate handler concurrency:
        exclusive → CREATE, UPDATE, DESTROY, ACTION
        shared    → READ, STREAM
    """
    CREATE  = "create"
    READ    = "read"
    UPDATE  = "update"
    DESTROY = "destroy"
    ACTION  = "action"
    STREAM  = "stream"

    @property
    def is_shared(self) -> bool:
        return self in (ActionKind.READ, ActionKind.STREAM)

    @property
    def is_exclusive(self) -> bool:
        return not self.is_shared

    @property
    def implicit_save(self) -> bool:
        """True if infra should call repo.save() on the returned Resource."""
        return self in (ActionKind.CREATE, ActionKind.UPDATE)

    @property
    def implicit_delete(self) -> bool:
        return self == ActionKind.DESTROY


@dataclass
class ActionMeta:
    name: str
    kind: ActionKind
    fn: Callable
    input_fields: dict[str, Any] = field(default_factory=dict)
    streams: bool = False


def action(
    kind: ActionKind = ActionKind.ACTION,
    name: str | None = None,
) -> Callable:
    """
    Decorator that marks a Resource method as a typed action.

    Examples:
        @action(kind=ActionKind.CREATE)
        def create(self, name: str) -> "MyResource": ...

        @action(kind=ActionKind.READ)
        def get(self) -> "MyResource": ...

        @action(kind=ActionKind.ACTION)
        def add_message(self, action_ctx: ActionContext, ...) -> Message: ...
    """
    def decorator(fn: Callable) -> Callable:
        action_name = name or fn.__name__
        fn.__action__ = ActionMeta(
            name=action_name,
            kind=kind,
            fn=fn,
            streams=(kind == ActionKind.STREAM),
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.__action__ = fn.__action__
        return wrapper

    return decorator
