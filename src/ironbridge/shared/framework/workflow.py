"""
Workflow mixin + @workflow decorator + signal lifecycle.

    class Job(Resource, Workflow):
        start = Signal(kind=ActionKind.CREATE)
        approval = Signal(timeout=timedelta(days=7))

        @workflow
        async def on_start(self, ctx, description: str):
            self.state = "sourcing"
            ctx.save()

            # with block: signal open on enter, closed on exit
            async with ctx.receive("quote_received") as quote:
                if not quote:  # timed out
                    self.state = "expired"
                    ctx.save()
                    return
                self.quote_amount = quote["amount"]
                ctx.save()
                quote.respond({"state": self.state})

            # also works without with:
            decision = await ctx.receive("approval")
"""
from __future__ import annotations

import functools
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any, Callable, ClassVar

from .actor import Actor
from .signal import Signal, SignalDef, get_signal_transport


# ---------------------------------------------------------------------------
# @workflow decorator
# ---------------------------------------------------------------------------

def workflow(fn: Callable) -> Callable:
    """Mark a function as a durable workflow. DBOS wraps it at derive time.
    Does not wrap - just marks. Preserves async nature."""
    fn._is_workflow = True
    return fn


def is_workflow_fn(fn: Any) -> bool:
    return getattr(fn, "_is_workflow", False)


# ---------------------------------------------------------------------------
# SignalHandle - returned by ctx.receive(), carries payload + response
# ---------------------------------------------------------------------------

class SignalHandle:
    """
    Handle to a received signal. Carries payload and response channel.

    Used as the value in `async with ctx.receive(...) as handle:`.
    Also returned by plain `await ctx.receive(...)`.
    """

    def __init__(
        self,
        signal: str,
        payload: Any,
        actor: Actor | None = None,
        respond_fn: Callable | None = None,
    ):
        self.signal = signal
        self.payload = payload
        self.actor = actor
        self._respond_fn = respond_fn
        self._responded = False

    def respond(self, data: Any) -> None:
        """Send response back to the signal sender."""
        if self._respond_fn and not self._responded:
            self._respond_fn(data)
            self._responded = True

    def __getitem__(self, key: str) -> Any:
        if isinstance(self.payload, dict):
            return self.payload[key]
        raise TypeError(f"Signal payload is {type(self.payload)}, not dict")

    def get(self, key: str, default: Any = None) -> Any:
        if isinstance(self.payload, dict):
            return self.payload.get(key, default)
        return default

    def __bool__(self) -> bool:
        """False if timed out (payload is None)."""
        return self.payload is not None

    def __repr__(self) -> str:
        return f"SignalHandle(signal={self.signal!r}, payload={self.payload!r})"


# ---------------------------------------------------------------------------
# SignalReceiver - async context manager for signal lifecycle
# ---------------------------------------------------------------------------

class SignalReceiver:
    """
    Async context manager for receiving a signal with explicit lifecycle.

    On enter: signal channel opens, DBOS.recv() starts.
    On exit: signal channel closes, stale messages discarded.
    """

    def __init__(self, ctx: WorkflowContext, signal_names: tuple[str, ...], timeout: timedelta | None):
        self._ctx = ctx
        self._signal_names = signal_names
        self._timeout = timeout
        self._handle: SignalHandle | None = None

    async def __aenter__(self) -> SignalHandle:
        for name in self._signal_names:
            self._ctx._open_signals.add(name)

        result = await self._ctx._do_receive(self._signal_names, self._timeout)
        self._handle = result

        for name in self._signal_names:
            self._ctx._open_signals.discard(name)

        return result

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # Signal closed. Any further sends to these topics should be rejected.
        # The _open_signals set is already updated in __aenter__.
        pass


# ---------------------------------------------------------------------------
# SignalMessage (internal, from transport layer)
# ---------------------------------------------------------------------------

@dataclass
class SignalMessage:
    """Raw message from transport. Converted to SignalHandle by WorkflowContext."""
    signal: str
    payload: Any
    actor: Actor | None


# ---------------------------------------------------------------------------
# WorkflowContext
# ---------------------------------------------------------------------------

class WorkflowContext:
    """
    Context for durable workflow functions.

        ctx.save()                                - persist resource state
        ctx.receive("signal")                     - pause (await or async with)
        ctx.sleep(duration)                       - pause for a duration
        ctx.emit(fn, ...)                         - queue a side effect
        ctx.actor                                 - who started/resumed
        ctx.is_signal_open("name")                - check if signal is being awaited
    """

    def __init__(
        self,
        actor: Actor,
        resource: Any,
        initiating_actor: Actor | None = None,
        save_fn: Any = None,
        recv_fn: Any = None,
        sleep_fn: Any = None,
        respond_fn: Any = None,
        repo_fn: Any = None,
        deps: Any = None,
    ):
        self.actor = actor
        self.resource = resource
        self.initiating_actor = initiating_actor or actor
        self._save_fn = save_fn
        self._recv_fn = recv_fn
        self._sleep_fn = sleep_fn
        self._respond_fn = respond_fn
        self._repo_fn = repo_fn
        self._effects: list[Effect] = []
        self._open_signals: set[str] = set()

        # Dependencies: injected from providers registry
        if deps is not None:
            self.deps = deps
        else:
            from .depends import Deps, get_providers
            self.deps = Deps(get_providers())

    def save(self) -> None:
        if self._save_fn:
            self._save_fn(self.resource)

    def repo(self, cls: type) -> Any:
        """Get a repository for a resource class. Tenant-scoped."""
        if self._repo_fn:
            return self._repo_fn(cls)
        # Fallback: use data_layer.get_repo
        from .data_layer import get_repo
        return get_repo(cls)

    def receive(
        self,
        *signal_names: str,
        timeout: timedelta | None = None,
    ) -> SignalReceiver:
        """
        Receive a signal. Use as async with or await.

        async with:
            async with ctx.receive("approval", timeout=timedelta(days=7)) as approval:
                if not approval: ...
                approval.respond(data)

        await:
            approval = await ctx.receive("approval")
        """
        return SignalReceiver(self, signal_names, timeout)

    async def _do_receive(
        self,
        signal_names: tuple[str, ...],
        timeout: timedelta | None,
    ) -> SignalHandle:
        """Internal: execute the actual receive via transport."""
        if self._recv_fn:
            result = await self._recv_fn(signal_names, timeout)
            if result is None:
                return SignalHandle(
                    signal=signal_names[0] if signal_names else "",
                    payload=None,
                    actor=None,
                    respond_fn=self._respond_fn,
                )
            if isinstance(result, SignalMessage):
                if result.actor:
                    self.actor = result.actor
                return SignalHandle(
                    signal=result.signal,
                    payload=result.payload,
                    actor=result.actor,
                    respond_fn=self._respond_fn,
                )
            return SignalHandle(
                signal=signal_names[0] if signal_names else "",
                payload=result,
                actor=None,
                respond_fn=self._respond_fn,
            )
        raise RuntimeError("No receive function registered on WorkflowContext")

    def __await__(self):
        """Support: result = await ctx.receive("signal")"""
        # This is called when someone does `await ctx.receive(...)` instead of `async with`
        # But ctx.receive() returns a SignalReceiver, not a coroutine.
        # We need SignalReceiver to also be awaitable.
        raise TypeError(
            "Use 'async with ctx.receive(...)' or 'await ctx.receive(...).wait()'. "
            "ctx.receive() returns a context manager."
        )

    async def sleep(self, until: datetime | None = None, duration: timedelta | None = None) -> None:
        if self._sleep_fn:
            await self._sleep_fn(until=until, duration=duration)
        else:
            raise RuntimeError("No sleep function registered on WorkflowContext")

    def emit(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        self._effects.append(Effect(fn=fn, args=args, kwargs=kwargs, actor=self.actor))

    @property
    def effects(self) -> list[Effect]:
        return list(self._effects)

    def is_signal_open(self, signal_name: str) -> bool:
        """Check if this signal is currently being awaited."""
        return signal_name in self._open_signals


# Make SignalReceiver awaitable (for plain `await ctx.receive(...)`)
async def _signal_receiver_await(self: SignalReceiver) -> SignalHandle:
    for name in self._signal_names:
        self._ctx._open_signals.add(name)
    try:
        result = await self._ctx._do_receive(self._signal_names, self._timeout)
        return result
    finally:
        for name in self._signal_names:
            self._ctx._open_signals.discard(name)


SignalReceiver.__await__ = lambda self: _signal_receiver_await(self).__await__()


# ---------------------------------------------------------------------------
# Effect
# ---------------------------------------------------------------------------

@dataclass
class Effect:
    fn: Any
    args: tuple
    kwargs: dict
    actor: Actor


# ---------------------------------------------------------------------------
# Workflow mixin
# ---------------------------------------------------------------------------

class Workflow:
    """
    Mixin that marks a class as workflow-capable.

    Signals declare their handlers explicitly:
        start = Signal(kind=ActionKind.CREATE, handler=handle_start)
        # or
        @start.handler
        async def handle_start(self, ctx): ...

    No naming conventions. No auto-discovery.
    """

    __abstract__ = True
    __signals__: ClassVar[dict[str, SignalDef]]
    __handlers__: ClassVar[dict[str, Callable]]  # signal_name -> handler function
    __workflow_entry__: ClassVar[str | None]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        signals: dict[str, SignalDef] = {}
        handlers: dict[str, Callable] = {}
        workflow_entry: str | None = None

        # Collect Signal descriptors
        for attr_name in dir(cls):
            if attr_name.startswith("_"):
                continue
            attr = getattr(cls, attr_name, None)
            if isinstance(attr, Signal):
                signal_def = attr.to_def(attr_name, cls)
                signals[attr_name] = signal_def

                transport = get_signal_transport()
                if transport:
                    signal_def._send_fn = transport

                # Resolve handler: explicit first, then on_ convention
                if signal_def._handler_fn is not None:
                    # Explicit handler (via handler= or @signal.handler)
                    handlers[attr_name] = signal_def._handler_fn
                else:
                    # Convention fallback: on_{signal_name}
                    convention_name = f"on_{attr_name}"
                    convention_fn = getattr(cls, convention_name, None)
                    if convention_fn is not None and callable(convention_fn):
                        signal_def._handler_fn = convention_fn
                        handlers[attr_name] = convention_fn

        # Determine entry handler (CREATE signal)
        from .actions import ActionKind
        for signal_name, signal_def in signals.items():
            if signal_def.kind == ActionKind.CREATE and signal_def._handler_fn is not None:
                workflow_entry = signal_name

        cls.__signals__ = signals
        cls.__handlers__ = handlers
        cls.__workflow_entry__ = workflow_entry

        # Validate at import time (skip abstract classes)
        if signals and not bool(getattr(cls, "__dict__", {}).get("__abstract__")):
            from .validation import validate_signals_at_import
            validate_signals_at_import(cls)

    @classmethod
    def get_handler(cls, signal_name: str) -> Callable | None:
        """Get the handler function for a signal."""
        return cls.__handlers__.get(signal_name)

    @classmethod
    def get_entry_handler(cls) -> str | None:
        """Get the entry signal name (CREATE signal)."""
        return getattr(cls, "__workflow_entry__", None)

    @classmethod
    def has_workflow(cls) -> bool:
        return bool(getattr(cls, "__signals__", {}))

    @classmethod
    def is_entry_signal(cls, signal_name: str) -> bool:
        from .actions import ActionKind
        sdef = cls.__signals__.get(signal_name)
        return sdef is not None and sdef.kind == ActionKind.CREATE

    @classmethod
    def is_mid_workflow_signal(cls, signal_name: str) -> bool:
        return signal_name in cls.__signals__ and not cls.is_entry_signal(signal_name)
