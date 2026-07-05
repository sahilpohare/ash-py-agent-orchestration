"""Unit tests for Actor, Origin, and constructor helpers."""

import pytest

from ironbridge.shared.framework.actor import (
    Actor,
    Origin,
    from_cron,
    from_request,
    from_webhook,
)


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------

class TestOrigin:
    def test_defaults(self):
        o = Origin()
        assert o.channel == "unknown"
        assert o.source_type is None
        assert o.source_id is None
        assert o.ip is None
        assert o.idempotency_key is None

    def test_full(self):
        o = Origin(
            channel="twilio",
            source_type="call",
            source_id="call-1",
            ip="1.2.3.4",
            idempotency_key="msg-sid-1",
        )
        assert o.channel == "twilio"
        assert o.source_id == "call-1"

    def test_frozen(self):
        o = Origin()
        with pytest.raises(AttributeError):
            o.channel = "changed"


# ---------------------------------------------------------------------------
# Actor basics
# ---------------------------------------------------------------------------

class TestActor:
    def test_fields(self):
        a = Actor(id="u-1", tenant_id="t-1", role="admin")
        assert a.id == "u-1"
        assert a.tenant_id == "t-1"
        assert a.role == "admin"
        assert a.scopes == frozenset()
        assert a.on_behalf_of is None

    def test_frozen(self):
        a = Actor(id="u-1", tenant_id="t-1", role="admin")
        with pytest.raises(AttributeError):
            a.role = "viewer"

    def test_is_system_for_system_role(self):
        assert Actor(id="sys", tenant_id="t", role="system").is_system is True

    def test_is_system_for_agent_role(self):
        assert Actor(id="bot", tenant_id="t", role="agent").is_system is True

    def test_is_system_false_for_human(self):
        assert Actor(id="u", tenant_id="t", role="operator").is_system is False
        assert Actor(id="u", tenant_id="t", role="admin").is_system is False
        assert Actor(id="u", tenant_id="t", role="viewer").is_system is False

    def test_has_role(self):
        a = Actor(id="u", tenant_id="t", role="operator")
        assert a.has_role("admin", "operator") is True
        assert a.has_role("admin", "superadmin") is False

    def test_has_scope(self):
        a = Actor(id="u", tenant_id="t", role="admin", scopes=frozenset({"read", "write"}))
        assert a.has_scope("read") is True
        assert a.has_scope("delete") is False

    def test_metadata(self):
        a = Actor(id="u", tenant_id="t", role="admin", metadata={"branch_id": "b-1"})
        assert a.metadata["branch_id"] == "b-1"


# ---------------------------------------------------------------------------
# Initiator chain
# ---------------------------------------------------------------------------

class TestInitiatorChain:
    def test_self_is_initiator_when_no_chain(self):
        a = Actor(id="u-1", tenant_id="t", role="operator")
        assert a.initiator is a

    def test_one_level_chain(self):
        human = Actor(id="u-1", tenant_id="t", role="operator")
        agent = human.as_agent("scheduling")
        assert agent.initiator is human

    def test_two_level_chain(self):
        human = Actor(id="u-1", tenant_id="t", role="admin")
        agent = human.as_agent("scheduling")
        sub_system = agent.as_system("effect")
        assert sub_system.initiator is human

    def test_three_level_chain(self):
        human = Actor(id="u-1", tenant_id="t", role="admin")
        a1 = human.as_agent("supervisor")
        a2 = a1.as_agent("sub-agent")
        a3 = a2.as_system("side-effect")
        assert a3.initiator is human


# ---------------------------------------------------------------------------
# Derivation methods
# ---------------------------------------------------------------------------

class TestDerivation:
    def test_as_agent(self):
        human = Actor(
            id="u-1",
            tenant_id="t-1",
            role="operator",
            origin=Origin(channel="web_dashboard"),
        )
        agent = human.as_agent("scheduling")

        assert agent.id == "scheduling"
        assert agent.role == "agent"
        assert agent.tenant_id == "t-1"
        assert agent.origin.channel == "web_dashboard"
        assert agent.on_behalf_of is human

    def test_as_system(self):
        human = Actor(id="u-1", tenant_id="t-1", role="admin")
        sys = human.as_system("crm_push")

        assert sys.id == "system:crm_push"
        assert sys.role == "system"
        assert sys.on_behalf_of is human

    def test_as_system_no_reason(self):
        human = Actor(id="u-1", tenant_id="t-1", role="admin")
        sys = human.as_system()
        assert sys.id == "system"

    def test_with_source(self):
        actor = Actor(
            id="u-1",
            tenant_id="t-1",
            role="operator",
            origin=Origin(channel="web_dashboard", ip="1.2.3.4"),
        )
        narrowed = actor.with_source("call", "call-123")

        assert narrowed.id == "u-1"
        assert narrowed.role == "operator"
        assert narrowed.origin.source_type == "call"
        assert narrowed.origin.source_id == "call-123"
        assert narrowed.origin.channel == "web_dashboard"
        assert narrowed.origin.ip == "1.2.3.4"

    def test_with_source_preserves_chain(self):
        human = Actor(id="u-1", tenant_id="t", role="admin")
        agent = human.as_agent("bot")
        narrowed = agent.with_source("enquiry", "enq-1")

        assert narrowed.on_behalf_of is human
        assert narrowed.origin.source_type == "enquiry"

    def test_with_source_preserves_metadata(self):
        actor = Actor(id="u", tenant_id="t", role="admin", metadata={"key": "val"})
        narrowed = actor.with_source("call", "c-1")
        assert narrowed.metadata == {"key": "val"}


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

class TestFromRequest:
    def test_basic(self):
        a = from_request("u-1", "t-1", "operator")
        assert a.id == "u-1"
        assert a.tenant_id == "t-1"
        assert a.role == "operator"
        assert a.origin.channel == "web_dashboard"
        assert a.on_behalf_of is None

    def test_with_ip_and_user_agent(self):
        a = from_request("u-1", "t-1", "admin", ip="10.0.0.1", user_agent="Mozilla/5.0")
        assert a.origin.ip == "10.0.0.1"
        assert a.origin.user_agent == "Mozilla/5.0"

    def test_with_scopes(self):
        a = from_request("u-1", "t-1", "admin", scopes=frozenset({"billing"}))
        assert a.has_scope("billing")


class TestFromWebhook:
    def test_basic(self):
        a = from_webhook("twilio", "t-1")
        assert a.id == "system"
        assert a.role == "system"
        assert a.is_system is True
        assert a.origin.channel == "twilio"

    def test_with_idempotency_key(self):
        a = from_webhook("nylas", "t-1", idempotency_key="msg-id-abc")
        assert a.origin.idempotency_key == "msg-id-abc"


class TestFromCron:
    def test_basic(self):
        a = from_cron("t-1", "nightly-sync")
        assert a.id == "cron:nightly-sync"
        assert a.role == "system"
        assert a.origin.channel == "cron"
        assert a.tenant_id == "t-1"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

class TestToDict:
    def test_simple(self):
        a = from_request("u-1", "t-1", "operator")
        d = a.to_dict()
        assert d["id"] == "u-1"
        assert d["tenant_id"] == "t-1"
        assert d["role"] == "operator"
        assert d["origin"]["channel"] == "web_dashboard"
        assert "on_behalf_of" not in d

    def test_with_chain(self):
        human = from_request("u-1", "t-1", "admin")
        agent = human.as_agent("scheduling")
        d = agent.to_dict()

        assert d["id"] == "scheduling"
        assert d["role"] == "agent"
        assert d["on_behalf_of"]["id"] == "u-1"
        assert d["on_behalf_of"]["role"] == "admin"

    def test_deep_chain(self):
        human = from_request("u-1", "t-1", "admin")
        agent = human.as_agent("supervisor")
        sub = agent.as_system("effect")
        d = sub.to_dict()

        assert d["on_behalf_of"]["on_behalf_of"]["id"] == "u-1"
