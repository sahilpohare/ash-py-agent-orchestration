"""Unit tests for Workflow mixin, @workflow decorator, signal lifecycle, and WorkflowContext."""

import asyncio
import pytest
from datetime import timedelta

from ironbridge.shared.framework.actor import from_request, from_webhook
from ironbridge.shared.framework.actions import ActionKind, action
from ironbridge.shared.framework.policies import role_is, system_only, anyone
from ironbridge.shared.framework.signal import Signal
from ironbridge.shared.framework.workflow import (
    Effect,
    SignalHandle,
    SignalMessage,
    SignalReceiver,
    Workflow,
    WorkflowContext,
    workflow,
    is_workflow_fn,
)


# ---------------------------------------------------------------------------
# @workflow decorator
# ---------------------------------------------------------------------------

class TestWorkflowDecorator:
    def test_marks_function(self):
        @workflow
        async def handler(self, ctx):
            pass
        assert is_workflow_fn(handler)

    def test_unmarked(self):
        async def handler(self, ctx):
            pass
        assert not is_workflow_fn(handler)

    def test_preserves_behavior(self):
        @workflow
        def add(a, b):
            return a + b
        assert add(2, 3) == 5

    def test_preserves_action(self):
        @action(kind=ActionKind.ACTION)
        @workflow
        async def act(self, ctx):
            pass
        assert is_workflow_fn(act)
        assert hasattr(act, "__action__")

    def test_stacks_all(self):
        from ironbridge.shared.framework.policies import policy
        from ironbridge.shared.framework.guards import guard, in_state

        @action(kind=ActionKind.ACTION)
        @policy(role_is("admin"))
        @guard(in_state("opened"))
        @workflow
        async def reassign(self, ctx):
            pass

        assert is_workflow_fn(reassign)
        assert hasattr(reassign, "__action__")
        assert len(reassign._policies) == 1
        assert len(reassign._guards) == 1


# ---------------------------------------------------------------------------
# SignalHandle
# ---------------------------------------------------------------------------

class TestSignalHandle:
    def test_payload_access(self):
        h = SignalHandle(signal="approval", payload={"action": "approve", "reason": "ok"})
        assert h["action"] == "approve"
        assert h.get("reason") == "ok"
        assert h.get("missing", "default") == "default"

    def test_bool_true(self):
        h = SignalHandle(signal="x", payload={"data": 1})
        assert bool(h) is True

    def test_bool_false_on_timeout(self):
        h = SignalHandle(signal="x", payload=None)
        assert bool(h) is False

    def test_signal_name(self):
        h = SignalHandle(signal="approval", payload={})
        assert h.signal == "approval"

    def test_actor(self):
        actor = from_request("u-1", "t-1", "admin")
        h = SignalHandle(signal="x", payload={}, actor=actor)
        assert h.actor is actor

    def test_respond(self):
        received = []
        h = SignalHandle(signal="x", payload={}, respond_fn=lambda d: received.append(d))
        h.respond({"state": "booking"})
        assert received == [{"state": "booking"}]

    def test_respond_only_once(self):
        count = [0]
        h = SignalHandle(signal="x", payload={}, respond_fn=lambda d: count.__setitem__(0, count[0] + 1))
        h.respond("first")
        h.respond("second")
        assert count[0] == 1

    def test_respond_without_fn(self):
        h = SignalHandle(signal="x", payload={})
        h.respond("data")  # should not raise

    def test_repr(self):
        h = SignalHandle(signal="approval", payload={"ok": True})
        assert "approval" in repr(h)


# ---------------------------------------------------------------------------
# Workflow mixin: signal collection
# ---------------------------------------------------------------------------

class TestWorkflowRegistration:
    def test_collects_signals(self):
        class Wf(Workflow):
            start = Signal(kind=ActionKind.CREATE)
            approval = Signal()

            @workflow
            async def on_start(self, ctx):
                pass

        assert "start" in Wf.__signals__
        assert "approval" in Wf.__signals__

    def test_entry_handler(self):
        class Wf(Workflow):
            start = Signal(kind=ActionKind.CREATE)

            @workflow
            async def on_start(self, ctx):
                pass

        assert Wf.get_entry_handler() == "start"

    def test_mid_workflow_signal(self):
        class Wf(Workflow):
            start = Signal(kind=ActionKind.CREATE)
            approval = Signal()

            @workflow
            async def on_start(self, ctx):
                pass

        assert Wf.is_mid_workflow_signal("approval") is True
        assert Wf.is_mid_workflow_signal("start") is False

    def test_mid_signals_dont_need_handlers(self):
        class Wf(Workflow):
            start = Signal(kind=ActionKind.CREATE)
            approval = Signal()
            quote = Signal()

            @workflow
            async def on_start(self, ctx):
                pass

        assert "approval" not in Wf.__handlers__
        assert "quote" not in Wf.__handlers__
        assert "start" in Wf.__handlers__

    def test_has_workflow(self):
        class Wf(Workflow):
            s = Signal(kind=ActionKind.CREATE)

            @workflow
            async def on_s(self, ctx):
                pass

        assert Wf.has_workflow() is True

    def test_empty_no_workflow(self):
        class Empty(Workflow):
            pass
        assert Empty.has_workflow() is False

    def test_workflow_fn_detection(self):
        class Job(Workflow):
            start = Signal(kind=ActionKind.CREATE)

            @workflow
            async def on_start(self, ctx):
                pass

            @action(kind=ActionKind.DESTROY)
            def archive(self):
                pass

        assert is_workflow_fn(Job.on_start)
        assert not is_workflow_fn(Job.archive)


# ---------------------------------------------------------------------------
# WorkflowContext: save, sleep, emit
# ---------------------------------------------------------------------------

class TestWorkflowContextBasics:
    def test_actor(self):
        actor = from_request("u-1", "t-1", "operator")
        ctx = WorkflowContext(actor=actor, resource=None)
        assert ctx.actor is actor

    def test_initiating_actor(self):
        starter = from_webhook("nylas", "t-1")
        ctx = WorkflowContext(actor=starter, resource=None)
        assert ctx.initiating_actor is starter

    def test_save(self):
        saved = []
        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource="my_resource",
            save_fn=lambda r: saved.append(r),
        )
        ctx.save()
        assert saved == ["my_resource"]

    @pytest.mark.asyncio
    async def test_sleep(self):
        slept = []

        async def mock_sleep(**kw):
            slept.append(kw)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            sleep_fn=mock_sleep,
        )
        await ctx.sleep(duration=timedelta(hours=1))
        assert len(slept) == 1

    def test_emit(self):
        ctx = WorkflowContext(actor=from_request("u-1", "t-1", "op"), resource=None)
        ctx.emit(lambda: None, "arg1", key="val")
        assert len(ctx.effects) == 1
        assert ctx.effects[0].args == ("arg1",)
        assert ctx.effects[0].kwargs == {"key": "val"}

    def test_emit_carries_actor(self):
        actor = from_request("u-1", "t-1", "op")
        ctx = WorkflowContext(actor=actor, resource=None)
        ctx.emit(lambda: None)
        assert ctx.effects[0].actor is actor

    def test_effects_copy(self):
        ctx = WorkflowContext(actor=from_request("u-1", "t-1", "admin"), resource=None)
        e1 = ctx.effects
        ctx.emit(lambda: None)
        e2 = ctx.effects
        assert len(e1) == 0
        assert len(e2) == 1


# ---------------------------------------------------------------------------
# WorkflowContext: receive with async with
# ---------------------------------------------------------------------------

class TestReceiveAsyncWith:
    @pytest.mark.asyncio
    async def test_basic_receive(self):
        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal=signal_names[0], payload={"action": "approve"}, actor=None)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        async with ctx.receive("approval") as handle:
            assert handle["action"] == "approve"
            assert handle.signal == "approval"

    @pytest.mark.asyncio
    async def test_timeout_returns_falsy_handle(self):
        async def mock_recv(signal_names, timeout):
            return None

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        async with ctx.receive("approval", timeout=timedelta(seconds=1)) as handle:
            assert not handle
            assert handle.payload is None

    @pytest.mark.asyncio
    async def test_signal_open_during_with(self):
        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal="approval", payload={}, actor=None)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        assert not ctx.is_signal_open("approval")
        # Note: signal opens and closes within __aenter__ for the with block
        async with ctx.receive("approval") as handle:
            pass
        assert not ctx.is_signal_open("approval")

    @pytest.mark.asyncio
    async def test_respond_in_with(self):
        responses = []

        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal="approval", payload={"ok": True}, actor=None)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
            respond_fn=lambda d: responses.append(d),
        )

        async with ctx.receive("approval") as handle:
            handle.respond({"state": "booking"})

        assert responses == [{"state": "booking"}]

    @pytest.mark.asyncio
    async def test_actor_updates_on_receive(self):
        new_actor = from_request("u-2", "t-1", "operator")

        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal="approval", payload={}, actor=new_actor)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        async with ctx.receive("approval") as handle:
            pass

        assert ctx.actor is new_actor

    @pytest.mark.asyncio
    async def test_multiple_signals_race(self):
        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal="rejected", payload={"reason": "too expensive"}, actor=None)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        async with ctx.receive("approved", "rejected") as handle:
            assert handle.signal == "rejected"
            assert handle["reason"] == "too expensive"


# ---------------------------------------------------------------------------
# WorkflowContext: receive with plain await
# ---------------------------------------------------------------------------

class TestReceiveAwait:
    @pytest.mark.asyncio
    async def test_basic_await(self):
        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal="quote", payload={"amount": "200"}, actor=None)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        handle = await ctx.receive("quote")
        assert handle["amount"] == "200"

    @pytest.mark.asyncio
    async def test_await_timeout(self):
        async def mock_recv(signal_names, timeout):
            return None

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
        )

        handle = await ctx.receive("quote", timeout=timedelta(seconds=1))
        assert not handle

    @pytest.mark.asyncio
    async def test_await_respond(self):
        responses = []

        async def mock_recv(signal_names, timeout):
            return SignalMessage(signal="quote", payload={"amount": "300"}, actor=None)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            recv_fn=mock_recv,
            respond_fn=lambda d: responses.append(d),
        )

        handle = await ctx.receive("quote")
        handle.respond({"processed": True})
        assert responses == [{"processed": True}]


# ---------------------------------------------------------------------------
# Full continuous workflow
# ---------------------------------------------------------------------------

class TestContinuousWorkflow:
    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        states = []
        responses = []
        call_count = [0]

        async def mock_recv(signal_names, timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                return SignalMessage(signal="quote_received", payload={"amount": "200"}, actor=None)
            elif call_count[0] == 2:
                return SignalMessage(signal="approval", payload={"action": "approve"}, actor=None)
            return None

        class Job(Workflow):
            start = Signal(kind=ActionKind.CREATE)
            quote_received = Signal()
            approval = Signal()

            @workflow
            async def on_start(self, ctx, description: str):
                self.state = "sourcing"
                states.append(self.state)
                ctx.save()

                async with ctx.receive("quote_received") as quote:
                    self.quote_amount = quote["amount"]
                    self.state = "quote_approval"
                    states.append(self.state)
                    ctx.save()
                    quote.respond({"state": self.state})

                async with ctx.receive("approval") as decision:
                    if decision["action"] == "approve":
                        self.state = "booking"
                    else:
                        self.state = "opened"
                    states.append(self.state)
                    ctx.save()
                    decision.respond({"state": self.state})

        job = Job()
        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=job,
            save_fn=lambda r: None,
            recv_fn=mock_recv,
            respond_fn=lambda d: responses.append(d),
        )

        await job.on_start(ctx, description="Leak")

        assert states == ["sourcing", "quote_approval", "booking"]
        assert responses == [{"state": "quote_approval"}, {"state": "booking"}]

    @pytest.mark.asyncio
    async def test_timeout_in_lifecycle(self):
        async def mock_recv(signal_names, timeout):
            return None  # always timeout

        class Job(Workflow):
            start = Signal(kind=ActionKind.CREATE)
            quote = Signal()

            @workflow
            async def on_start(self, ctx):
                self.state = "sourcing"
                ctx.save()

                async with ctx.receive("quote", timeout=timedelta(days=3)) as q:
                    if not q:
                        self.state = "expired"
                        ctx.save()
                        return

                self.state = "should_not_reach"

        job = Job()
        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=job,
            save_fn=lambda r: None,
            recv_fn=mock_recv,
        )

        await job.on_start(ctx)
        assert job.state == "expired"

    @pytest.mark.asyncio
    async def test_reject_flow(self):
        call_count = [0]

        async def mock_recv(signal_names, timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                return SignalMessage(signal="quote", payload={"amount": "999"}, actor=None)
            return SignalMessage(signal="approval", payload={"action": "reject"}, actor=None)

        class Job(Workflow):
            start = Signal(kind=ActionKind.CREATE)
            quote = Signal()
            approval = Signal()

            @workflow
            async def on_start(self, ctx):
                self.state = "sourcing"

                async with ctx.receive("quote") as q:
                    self.quote_amount = q["amount"]
                    self.state = "quote_approval"

                async with ctx.receive("approval") as decision:
                    if decision["action"] == "reject":
                        self.state = "rejected"
                        return

                self.state = "should_not_reach"

        job = Job()
        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=job,
            recv_fn=mock_recv,
        )

        await job.on_start(ctx)
        assert job.state == "rejected"
        assert job.quote_amount == "999"
