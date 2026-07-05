"""
Signal -- a message a Workflow can receive.

Input schema is derived from the on_ handler's signature, same as @action:
    - No params (besides self/ctx)  -> no input
    - Single BaseModel param        -> validate through that model
    - Plain typed params             -> auto-generate a Pydantic model
    - Explicit input= on Signal()   -> overrides handler introspection

    class MaintenanceJob(Workflow):
        approval = Signal(policies=[role_is("admin", "operator")])

        async def on_approval(self, ctx, action: str):
            ...
        # -> auto-generated input model: {action: str}

        quote = Signal(policies=[system_only()])

        async def on_quote(self, ctx, input: QuoteInput):
            ...
        # -> validates through QuoteInput
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
    input_model: type[BaseModel] | None  # generated or explicit
    input_style: str  # "none" | "model" | "fields"
    owner_cls: type | None = None

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

    Input schema is resolved in order:
    1. Explicit input= parameter on Signal()
    2. Introspected from the on_ handler's signature (when Workflow class is created)
    3. None (no validation)
    """

    def __init__(
        self,
        kind: ActionKind | None = None,
        policies: list | None = None,
        input: type[BaseModel] | None = None,
        name: str | None = None,
    ):
        self.kind = kind
        self.policies = policies or []
        self._explicit_input = input
        self._name_override = name
        self.name: str | None = None
        self._def: SignalDef | None = None

    def to_def(self, attr_name: str, owner_cls: type) -> SignalDef:
        """Convert to a SignalDef. Called by Workflow.__init_subclass__."""
        self.name = self._name_override or attr_name
        route_name = self.name  # used for URL path
        handler_lookup = attr_name  # used to find on_{attr_name}

        # Start with explicit input if provided
        input_model = self._explicit_input
        input_style = "model" if input_model else "none"

        # If no explicit input, try to introspect the handler
        if input_model is None:
            on_handler_name = f"on_{handler_lookup}"
            handler = getattr(owner_cls, on_handler_name, None)
            if handler is not None:
                input_model, input_style = _inspect_input(handler, f"{route_name}_signal")

        self._def = SignalDef(
            name=route_name,
            kind=self.kind,
            policies=self.policies,
            input_model=input_model,
            input_style=input_style,
            owner_cls=owner_cls,
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
