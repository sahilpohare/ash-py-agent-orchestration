"""
Signal -- a message a Workflow can receive.

Signals declare handler explicitly via handler= arg or @signal.handler decorator.
No naming convention. No auto-discovery.

    class Job(Resource, Workflow):
        start = Signal(kind=ActionKind.CREATE, policies=[role_is("admin")])

        @start.handler
        async def handle_start(self, ctx, description: str):
            ...

    # Or:
        async def my_handler(self, ctx, description: str):
            ...

        start = Signal(kind=ActionKind.CREATE, handler=my_handler)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from .actions import ActionKind, _inspect_input

if TYPE_CHECKING:
    from .actor import Actor
    from .policies import PolicyDef


@dataclass
class SignalDef:
    """Metadata for a declared Signal."""
    name: str
    kind: ActionKind | None
    policies: list[PolicyDef]
    input_model: type[BaseModel] | None
    input_style: str  # "none" | "model" | "fields"
    owner_cls: type | None = None
    _handler_fn: Callable | None = field(default=None, repr=False)

    def send(self, resource_id: str | None, payload: Any = None, *, actor: Any = None) -> None:
        if self._send_fn is not None:
            self._send_fn(self, resource_id, payload, actor=actor)
        else:
            raise RuntimeError(
                f"Signal '{self.name}' has no send function registered. "
                f"Did you register a signal transport?"
            )

    _send_fn: Callable | None = field(default=None, repr=False)


class Signal:
    """
    Descriptor that declares a signal on a Workflow class.

    Handler binding (explicit, no convention):
        # Option 1: decorator
        start = Signal(kind=ActionKind.CREATE)
        @start.handler
        async def handle_start(self, ctx): ...

        # Option 2: reference
        async def handle_start(self, ctx): ...
        start = Signal(kind=ActionKind.CREATE, handler=handle_start)
    """

    def __init__(
        self,
        kind: ActionKind | None = None,
        policies: list | None = None,
        input: type[BaseModel] | None = None,
        name: str | None = None,
        handler: Callable | None = None,
    ):
        self.kind = kind
        self.policies = policies or []
        self._explicit_input = input
        self._name_override = name
        self._handler_fn: Callable | None = handler
        self.name: str | None = None
        self._def: SignalDef | None = None

        # Mark handler as workflow if provided
        if handler is not None:
            handler._is_workflow = True

    def handler(self, fn: Callable) -> Callable:
        """Decorator: register fn as this signal's handler."""
        self._handler_fn = fn
        fn._is_workflow = True
        return fn

    def to_def(self, attr_name: str, owner_cls: type) -> SignalDef:
        """Convert to a SignalDef. Called by Workflow.__init_subclass__."""
        self.name = self._name_override or attr_name

        # Resolve handler: explicit first, then on_ convention
        handler = self._handler_fn
        if handler is None:
            convention_name = f"on_{attr_name}"
            convention_fn = getattr(owner_cls, convention_name, None)
            if convention_fn is not None and callable(convention_fn):
                handler = convention_fn

        # Resolve input model from handler signature
        input_model = self._explicit_input
        input_style = "model" if input_model else "none"

        if input_model is None and handler is not None:
            input_model, input_style = _inspect_input(handler, f"{self.name}_signal")

        self._def = SignalDef(
            name=self.name,
            kind=self.kind,
            policies=self.policies,
            input_model=input_model,
            input_style=input_style,
            owner_cls=owner_cls,
            _handler_fn=handler,
        )
        return self._def

    def send(self, resource_id: str | None, payload: Any = None, *, actor: Any = None) -> None:
        if self._def is None:
            raise RuntimeError(f"Signal not yet bound to a Workflow class")
        self._def.send(resource_id, payload, actor=actor)


# ---------------------------------------------------------------------------
# Signal transport registration
# ---------------------------------------------------------------------------

_signal_transport: Callable | None = None


def register_signal_transport(fn: Callable) -> None:
    global _signal_transport
    _signal_transport = fn

    from . import registry as reg
    for cls in reg.all_resources().values():
        for sdef in getattr(cls, "__signals__", {}).values():
            sdef._send_fn = fn


def get_signal_transport() -> Callable | None:
    return _signal_transport
