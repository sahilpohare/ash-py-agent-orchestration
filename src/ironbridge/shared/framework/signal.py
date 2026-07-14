"""
Signal -- typed, awaitable entry points for Workflows.

    class Job(Resource, Workflow):
        start = Signal(kind=ActionKind.CREATE, policies=[role("admin")])
        submit_quote = Signal(policies=[system()], input=QuotePayload)
        approve_quote = Signal(policies=[role("admin")], input=ApprovalPayload)

        async def on_start(self, description: str):
            self.status = "opened"
            self.save()

            quote: QuotePayload = await self.submit_quote          # typed, awaitable
            self.quote_amount = quote.amount                        # autocomplete

            async with self.approve_quote(timeout=timedelta(days=7)) as approval:
                if not approval:
                    return
                self.status = "approved"
                self.save()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
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
    guards: list  # list[GuardDef]
    input_model: type[BaseModel] | None
    input_style: str  # "none" | "model" | "fields"
    owner_cls: type | None = None
    _handler_fn: Callable | None = field(default=None, repr=False)
    _send_fn: Callable | None = field(default=None, repr=False)

    def send(self, resource_id: str | None, payload: Any = None, *, actor: Any = None) -> None:
        if self._send_fn is not None:
            self._send_fn(self, resource_id, payload, actor=actor)
        else:
            raise RuntimeError(
                f"Signal '{self.name}' has no send function registered. "
                f"Did you register a signal transport?"
            )


class BoundSignal:
    """
    A Signal bound to a resource instance. Awaitable and callable.

        quote = await self.submit_quote                    # simple await
        async with self.approve_quote(timeout=...) as a:   # with timeout
    """

    def __init__(self, signal: Signal, instance: Any):
        self._signal = signal
        self._instance = instance
        self._timeout: timedelta | None = None

    def __call__(self, timeout: timedelta | None = None) -> BoundSignal:
        """Set timeout. Returns self for use as async context manager."""
        self._timeout = timeout
        return self

    def __await__(self):
        """Simple await: quote = await self.submit_quote"""
        return self._do_recv().__await__()

    async def _do_recv(self) -> Any:
        """Execute the receive via the workflow's recv_fn."""
        instance = self._instance
        recv_fn = getattr(instance, "_workflow_recv_fn", None)
        if recv_fn is None:
            raise RuntimeError("No recv function set. Is this running inside a workflow?")

        name = self._signal.name
        timeout = self._timeout

        # Track open signals
        open_signals = getattr(instance, "_open_signals", None)
        if open_signals is not None:
            open_signals.add(name)

        try:
            result = await recv_fn((name,), timeout)
        finally:
            if open_signals is not None:
                open_signals.discard(name)

        if result is None:
            return _make_handle(name, None, None, self._signal)

        # Convert raw result to SignalHandle with typed payload
        from .workflow import SignalMessage
        if isinstance(result, SignalMessage):
            return _make_handle(name, result.payload, result.actor, self._signal)

        return _make_handle(name, result, None, self._signal)

    # Async context manager support
    async def __aenter__(self) -> Any:
        return await self._do_recv()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def send(self, resource_id: str | None = None, payload: Any = None, *, actor: Any = None) -> None:
        """Send this signal to a resource."""
        self._signal.send(resource_id, payload, actor=actor)


def _make_handle(signal_name: str, payload: Any, actor: Any, signal: Signal) -> Any:
    """Create a SignalHandle with typed payload if input model is declared."""
    from .workflow import SignalHandle

    # Validate and convert payload to Pydantic model if input= is set
    input_model = signal._explicit_input
    if input_model and payload is not None and isinstance(payload, dict):
        try:
            typed_payload = input_model(**payload)
        except Exception:
            typed_payload = payload
    else:
        typed_payload = payload

    return SignalHandle(
        signal=signal_name,
        payload=typed_payload,
        actor=actor,
    )


class Signal:
    """
    Descriptor that declares a signal on a Workflow class.

    On the class: Signal declaration (introspectable, collects metadata).
    On an instance: returns BoundSignal (awaitable, callable for timeout).

        submit_quote = Signal(policies=[system()], input=QuotePayload)

        # In workflow handler:
        quote: QuotePayload = await self.submit_quote
        quote.amount     # typed, autocomplete works
    """

    def __init__(
        self,
        kind: ActionKind | None = None,
        policies: list | None = None,
        guards: list | None = None,
        input: type[BaseModel] | None = None,
        name: str | None = None,
        handler: Callable | None = None,
    ):
        self.kind = kind
        self.policies = policies or []
        self.guards = guards or []
        self._explicit_input = input
        self._name_override = name
        self._handler_fn: Callable | None = handler
        self.name: str | None = None
        self._def: SignalDef | None = None
        self._attr_name: str | None = None

        if handler is not None:
            handler._is_workflow = True

    def __set_name__(self, owner: type, name: str) -> None:
        """Called when the descriptor is assigned to a class attribute."""
        self._attr_name = name
        if self.name is None:
            self.name = self._name_override or name

    def __get__(self, obj: Any, objtype: type | None = None) -> Signal | BoundSignal:
        """Descriptor protocol: class access returns Signal, instance access returns BoundSignal."""
        if obj is None:
            return self  # class-level access: Job.submit_quote -> Signal
        return BoundSignal(self, obj)  # instance-level: self.submit_quote -> BoundSignal

    def handler(self, fn: Callable) -> Callable:
        """Decorator: register fn as this signal's handler."""
        self._handler_fn = fn
        fn._is_workflow = True
        return fn

    def to_def(self, attr_name: str, owner_cls: type) -> SignalDef:
        """Convert to a SignalDef. Called by Workflow.__init_subclass__."""
        self.name = self._name_override or attr_name

        handler = self._handler_fn
        if handler is None:
            convention_name = f"on_{attr_name}"
            convention_fn = getattr(owner_cls, convention_name, None)
            if convention_fn is not None and callable(convention_fn):
                handler = convention_fn

        input_model = self._explicit_input
        input_style = "model" if input_model else "none"

        if input_model is None and handler is not None:
            input_model, input_style = _inspect_input(handler, f"{self.name}_signal")

        self._def = SignalDef(
            name=self.name,
            kind=self.kind,
            policies=self.policies,
            guards=self.guards,
            input_model=input_model,
            input_style=input_style,
            owner_cls=owner_cls,
            _handler_fn=handler,
        )
        return self._def

    def send(self, resource_id: str | None = None, payload: Any = None, *, actor: Any = None) -> None:
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
