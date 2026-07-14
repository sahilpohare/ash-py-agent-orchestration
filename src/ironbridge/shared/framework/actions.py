from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, get_type_hints

from pydantic import BaseModel, create_model


class ActionKind(StrEnum):
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
        return self in (ActionKind.CREATE, ActionKind.UPDATE)

    @property
    def implicit_delete(self) -> bool:
        return self == ActionKind.DESTROY


@dataclass
class ActionMeta:
    name: str
    kind: ActionKind
    fn: Callable
    input_model: type[BaseModel] | None = None   # generated or explicit Pydantic model for input
    output_model: type | None = None              # return type (Pydantic model or plain type)
    input_style: str = "none"                     # "none" | "model" | "fields"
    streams: bool = False


# Skip these params when building input schema from signature
_SKIP_PARAMS = {"self", "ctx", "return"}


def _inspect_input(fn: Callable, action_name: str) -> tuple[type[BaseModel] | None, str]:
    """
    Inspect a function's signature and build an input model.

    Returns (model, style):
        (None, "none")              -> no input params
        (SomeModel, "model")        -> single param that IS a BaseModel subclass
        (GeneratedModel, "fields")  -> plain typed params, auto-generated model
    """
    sig = inspect.signature(fn)
    params = {
        k: v for k, v in sig.parameters.items()
        if k not in _SKIP_PARAMS
        and v.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
    }

    if not params:
        return None, "none"

    # Single param that's a Pydantic model
    if len(params) == 1:
        param = next(iter(params.values()))
        annotation = param.annotation
        if annotation is not inspect.Parameter.empty:
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                return annotation, "model"

    # Multiple params or single non-model param -> build a model from the sig
    fields: dict[str, Any] = {}
    for param_name, param in params.items():
        annotation = param.annotation
        if annotation is inspect.Parameter.empty:
            annotation = Any

        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model = create_model(f"{action_name}_input", **fields)
    return model, "fields"


def _inspect_output(fn: Callable) -> type | None:
    """
    Inspect a function's return annotation.

    Returns the type if it's a concrete Pydantic BaseModel subclass.
    Returns None for forward refs, plain types, or missing annotations.
    """
    sig = inspect.signature(fn)
    ret = sig.return_annotation

    if ret is inspect.Parameter.empty:
        return None

    # Skip string annotations (forward refs like "MaintenanceJob")
    if isinstance(ret, str):
        return None

    if isinstance(ret, type) and issubclass(ret, BaseModel):
        return ret

    return None


def action(
    kind: ActionKind = ActionKind.ACTION,
    name: str | None = None,
) -> Callable:
    """
    Decorator that marks a Resource/Workflow method as a typed action.

    Input schema is derived from the method signature:
        - No params (besides self/ctx)  -> no input
        - Single BaseModel param        -> validate through that model
        - Plain typed params             -> auto-generate a Pydantic model

    Output schema is derived from the return annotation:
        - BaseModel subclass            -> serialize through it
        - Anything else                 -> serialize all fields

    Examples:
        @action(kind=ActionKind.CREATE)
        def open(self, description: str, urgency: str) -> "MaintenanceJob":
            ...
        # -> auto-generated input model: {description: str, urgency: str}

        @action(kind=ActionKind.ACTION)
        def record_quote(self, input: RecordQuoteInput) -> "MaintenanceJob":
            ...
        # -> validates through RecordQuoteInput

        @action(kind=ActionKind.READ)
        def summary(self) -> JobSummary:
            ...
        # -> output serialized through JobSummary
    """
    def decorator(fn: Callable) -> Callable:
        action_name = name or fn.__name__
        input_model, input_style = _inspect_input(fn, action_name)
        output_model = _inspect_output(fn)

        fn.__action__ = ActionMeta(
            name=action_name,
            kind=kind,
            fn=fn,
            input_model=input_model,
            output_model=output_model,
            input_style=input_style,
            streams=(kind == ActionKind.STREAM),
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.__action__ = fn.__action__
        return wrapper

    return decorator
