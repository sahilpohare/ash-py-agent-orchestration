"""Unit tests for policies."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.actor import Actor, Origin, from_request, from_webhook
from ironbridge.shared.framework.policies import (
    PolicyDef,
    PolicyVerdict,
    anyone,
    has_scope,
    initiator_is,
    policy,
    role_is,
    same_tenant,
    system_only,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeResource:
    tenant_id: str = "t-1"


def operator(tenant: str = "t-1") -> Actor:
    return from_request("u-1", tenant, "operator")


def admin(tenant: str = "t-1") -> Actor:
    return from_request("u-2", tenant, "admin")


def system(tenant: str = "t-1") -> Actor:
    return from_webhook("cron", tenant)


# ---------------------------------------------------------------------------
# role_is
# ---------------------------------------------------------------------------

class TestRoleIs:
    def test_matching_role_allows(self):
        p = role_is("admin", "operator")
        assert p.check(operator(), FakeResource()) == PolicyVerdict.ALLOW

    def test_non_matching_role_denies(self):
        p = role_is("admin", "superadmin")
        assert p.check(operator(), FakeResource()) == PolicyVerdict.DENY

    def test_system_always_passes(self):
        p = role_is("admin")
        assert p.check(system(), FakeResource()) == PolicyVerdict.ALLOW

    def test_agent_always_passes(self):
        p = role_is("admin")
        agent = operator().as_agent("bot")
        assert p.check(agent, FakeResource()) == PolicyVerdict.ALLOW

    def test_custom_message(self):
        p = role_is("admin", message="Must be admin")
        assert p.message == "Must be admin"

    def test_name(self):
        p = role_is("admin", "operator")
        assert p.name == "role_is(admin,operator)"


# ---------------------------------------------------------------------------
# same_tenant
# ---------------------------------------------------------------------------

class TestSameTenant:
    def test_matching_tenant(self):
        p = same_tenant()
        assert p.check(operator("t-1"), FakeResource("t-1")) == PolicyVerdict.ALLOW

    def test_mismatched_tenant(self):
        p = same_tenant()
        assert p.check(operator("t-2"), FakeResource("t-1")) == PolicyVerdict.DENY

    def test_resource_without_tenant_allows(self):
        p = same_tenant()

        @dataclass
        class NoTenant:
            pass

        assert p.check(operator(), NoTenant()) == PolicyVerdict.ALLOW

    def test_system_with_wrong_tenant_still_denied(self):
        """same_tenant does not auto-pass for system actors."""
        p = same_tenant()
        assert p.check(system("t-2"), FakeResource("t-1")) == PolicyVerdict.DENY


# ---------------------------------------------------------------------------
# system_only
# ---------------------------------------------------------------------------

class TestSystemOnly:
    def test_system_allowed(self):
        p = system_only()
        assert p.check(system(), FakeResource()) == PolicyVerdict.ALLOW

    def test_agent_allowed(self):
        p = system_only()
        agent = operator().as_agent("bot")
        assert p.check(agent, FakeResource()) == PolicyVerdict.ALLOW

    def test_human_denied(self):
        p = system_only()
        assert p.check(operator(), FakeResource()) == PolicyVerdict.DENY


# ---------------------------------------------------------------------------
# has_scope
# ---------------------------------------------------------------------------

class TestHasScope:
    def test_has_required_scopes(self):
        p = has_scope("billing", "read")
        actor = Actor(id="u", tenant_id="t", role="admin", scopes=frozenset({"billing", "read", "write"}))
        assert p.check(actor, FakeResource()) == PolicyVerdict.ALLOW

    def test_missing_scope(self):
        p = has_scope("billing", "delete")
        actor = Actor(id="u", tenant_id="t", role="admin", scopes=frozenset({"billing"}))
        assert p.check(actor, FakeResource()) == PolicyVerdict.DENY

    def test_system_bypasses(self):
        p = has_scope("billing")
        assert p.check(system(), FakeResource()) == PolicyVerdict.ALLOW


# ---------------------------------------------------------------------------
# anyone
# ---------------------------------------------------------------------------

class TestAnyone:
    def test_any_actor_allowed(self):
        p = anyone()
        assert p.check(operator(), FakeResource()) == PolicyVerdict.ALLOW
        assert p.check(system(), FakeResource()) == PolicyVerdict.ALLOW
        assert p.check(Actor(id="x", tenant_id="t", role="viewer"), FakeResource()) == PolicyVerdict.ALLOW


# ---------------------------------------------------------------------------
# initiator_is
# ---------------------------------------------------------------------------

class TestInitiatorIs:
    def test_direct_actor_with_role(self):
        p = initiator_is("admin")
        assert p.check(admin(), FakeResource()) == PolicyVerdict.ALLOW

    def test_direct_actor_without_role(self):
        p = initiator_is("admin")
        assert p.check(operator(), FakeResource()) == PolicyVerdict.DENY

    def test_agent_of_admin(self):
        p = initiator_is("admin")
        agent = admin().as_agent("bot")
        assert p.check(agent, FakeResource()) == PolicyVerdict.ALLOW

    def test_agent_of_operator(self):
        p = initiator_is("admin")
        agent = operator().as_agent("bot")
        assert p.check(agent, FakeResource()) == PolicyVerdict.DENY

    def test_deep_chain(self):
        p = initiator_is("superadmin")
        sa = from_request("u-sa", "t", "superadmin")
        agent = sa.as_agent("supervisor")
        sub = agent.as_system("effect")
        assert p.check(sub, FakeResource()) == PolicyVerdict.ALLOW


# ---------------------------------------------------------------------------
# @policy decorator
# ---------------------------------------------------------------------------

class TestPolicyDecorator:
    def test_attaches_policies(self):
        @policy(role_is("admin"))
        def my_action():
            pass

        assert len(my_action._policies) == 1
        assert my_action._policies[0].name == "role_is(admin)"

    def test_stacking(self):
        @policy(same_tenant())
        @policy(role_is("admin"))
        def my_action():
            pass

        assert len(my_action._policies) == 2
        names = [p.name for p in my_action._policies]
        assert "role_is(admin)" in names
        assert "same_tenant" in names

    def test_preserves_function_behavior(self):
        @policy(anyone())
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_preserves_guards(self):
        """If a function already has _guards, @policy should keep them."""
        def fn():
            pass
        fn._guards = ["some_guard"]

        decorated = policy(anyone())(fn)
        assert decorated._guards == ["some_guard"]
