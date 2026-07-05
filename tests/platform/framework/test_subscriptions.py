"""Unit tests for @on subscriptions."""

import asyncio
import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.subscriptions import (
    on,
    notify,
    get_subscriptions,
    clear_subscriptions,
)
from ironbridge.shared.framework.actor import from_request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeJob:
    __name__ = "FakeJob"
    def __init__(self, id="j-1"):
        self.id = id


class FakeCall:
    __name__ = "FakeCall"
    def __init__(self, id="c-1"):
        self.id = id


@pytest.fixture(autouse=True)
def clean():
    clear_subscriptions()
    yield
    clear_subscriptions()


# ---------------------------------------------------------------------------
# Tests: registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_specific_action(self):
        @on(FakeJob, "create")
        def handler(resource):
            pass

        subs = get_subscriptions("FakeJob", "create")
        assert handler in subs

    def test_register_wildcard(self):
        @on(FakeJob, "*")
        def handler(resource):
            pass

        subs = get_subscriptions("FakeJob", "any_action")
        assert handler in subs

    def test_multiple_handlers(self):
        @on(FakeJob, "create")
        def h1(resource):
            pass

        @on(FakeJob, "create")
        def h2(resource):
            pass

        subs = get_subscriptions("FakeJob", "create")
        assert len(subs) == 2

    def test_different_resources(self):
        @on(FakeJob, "create")
        def h1(resource):
            pass

        @on(FakeCall, "create")
        def h2(resource):
            pass

        assert len(get_subscriptions("FakeJob", "create")) == 1
        assert len(get_subscriptions("FakeCall", "create")) == 1

    def test_no_subscriptions(self):
        assert get_subscriptions("FakeJob", "create") == []


# ---------------------------------------------------------------------------
# Tests: notify
# ---------------------------------------------------------------------------

class TestNotify:
    @pytest.mark.asyncio
    async def test_handler_called(self):
        received = []

        @on(FakeJob, "create")
        def handler(resource):
            received.append(resource.id)

        await notify(FakeJob("j-1"), "create")
        assert received == ["j-1"]

    @pytest.mark.asyncio
    async def test_async_handler(self):
        received = []

        @on(FakeJob, "create")
        async def handler(resource):
            received.append(resource.id)

        await notify(FakeJob("j-1"), "create")
        assert received == ["j-1"]

    @pytest.mark.asyncio
    async def test_wildcard_handler(self):
        received = []

        @on(FakeJob, "*")
        def handler(resource, action_name):
            received.append(action_name)

        await notify(FakeJob(), "create")
        await notify(FakeJob(), "update")
        await notify(FakeJob(), "delete")

        assert received == ["create", "update", "delete"]

    @pytest.mark.asyncio
    async def test_both_specific_and_wildcard(self):
        received = []

        @on(FakeJob, "create")
        def specific(resource):
            received.append("specific")

        @on(FakeJob, "*")
        def wildcard(resource):
            received.append("wildcard")

        await notify(FakeJob(), "create")
        assert "specific" in received
        assert "wildcard" in received

    @pytest.mark.asyncio
    async def test_handler_receives_actor(self):
        received = []

        @on(FakeJob, "create")
        def handler(resource, actor):
            received.append(actor.id)

        actor = from_request("u-1", "t-1", "admin")
        await notify(FakeJob(), "create", actor=actor)
        assert received == ["u-1"]

    @pytest.mark.asyncio
    async def test_handler_receives_result(self):
        received = []

        @on(FakeJob, "update")
        def handler(resource, result):
            received.append(result)

        await notify(FakeJob(), "update", result={"state": "closed"})
        assert received == [{"state": "closed"}]

    @pytest.mark.asyncio
    async def test_handler_receives_payload(self):
        received = []

        @on(FakeJob, "approval")
        def handler(resource, payload):
            received.append(payload)

        await notify(FakeJob(), "approval", payload={"action": "approve"})
        assert received == [{"action": "approve"}]

    @pytest.mark.asyncio
    async def test_handler_receives_event_name(self):
        received = []

        @on(FakeJob, "*")
        def handler(resource, event_name):
            received.append(event_name)

        await notify(FakeJob(), "approve_quote")
        assert received == ["approve_quote"]

    @pytest.mark.asyncio
    async def test_handler_selective_kwargs(self):
        """Handler only gets the params it declares."""
        received = {}

        @on(FakeJob, "create")
        def handler(resource, actor):
            received["resource_id"] = resource.id
            received["actor_id"] = actor.id

        actor = from_request("u-1", "t-1", "admin")
        await notify(FakeJob("j-1"), "create", actor=actor, result="ignored", payload="ignored")

        assert received == {"resource_id": "j-1", "actor_id": "u-1"}

    @pytest.mark.asyncio
    async def test_handler_error_does_not_break_flow(self):
        """A failing handler should not prevent other handlers from running."""
        received = []

        @on(FakeJob, "create")
        def bad_handler(resource):
            raise ValueError("boom")

        @on(FakeJob, "create")
        def good_handler(resource):
            received.append("ok")

        await notify(FakeJob(), "create")
        assert received == ["ok"]

    @pytest.mark.asyncio
    async def test_no_handlers_no_error(self):
        """Notifying with no subscribers should not raise."""
        await notify(FakeJob(), "nonexistent")

    @pytest.mark.asyncio
    async def test_different_resources_isolated(self):
        job_received = []
        call_received = []

        @on(FakeJob, "create")
        def job_handler(resource):
            job_received.append(resource.id)

        @on(FakeCall, "create")
        def call_handler(resource):
            call_received.append(resource.id)

        await notify(FakeJob("j-1"), "create")

        assert job_received == ["j-1"]
        assert call_received == []


# ---------------------------------------------------------------------------
# Tests: clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear(self):
        @on(FakeJob, "create")
        def handler(resource):
            pass

        assert len(get_subscriptions("FakeJob", "create")) == 1

        clear_subscriptions()

        assert len(get_subscriptions("FakeJob", "create")) == 0
