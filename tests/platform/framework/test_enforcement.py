"""Unit tests for enforcement (enforce, can, check_policies, check_guards)."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.actor import from_request, from_webhook
from ironbridge.shared.framework.enforcement import (
    GuardFailed,
    PolicyDenied,
    can,
    check_guards,
    check_policies,
    enforce,
)
from ironbridge.shared.framework.guards import (
    field_set,
    guard,
    in_state,
    not_deleted,
)
from ironbridge.shared.framework.policies import (
    policy,
    role_is,
    same_tenant,
    system_only,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeJob:
    tenant_id: str = "t-1"
    state: str = "opened"
    quote_amount: float | None = None
    is_deleted: bool = False


def _make_action(policies=None, guards=None):
    """Create a fake action function with attached policies and guards."""
    def action_fn():
        pass
    action_fn._policies = policies or []
    action_fn._guards = guards or []
    return action_fn


def operator(tenant="t-1"):
    return from_request("u-1", tenant, "operator")


def admin(tenant="t-1"):
    return from_request("u-2", tenant, "admin")


def system(tenant="t-1"):
    return from_webhook("cron", tenant)


# ---------------------------------------------------------------------------
# enforce: policies
# ---------------------------------------------------------------------------

class TestEnforcePolicies:
    def test_allowed_role(self):
        action = _make_action(policies=[role_is("operator")])
        enforce(operator(), FakeJob(), action)  # should not raise

    def test_denied_role(self):
        action = _make_action(policies=[role_is("admin")])
        with pytest.raises(PolicyDenied) as exc_info:
            enforce(operator(), FakeJob(), action)
        assert exc_info.value.policy_name == "role_is(admin)"
        assert exc_info.value.actor_id == "u-1"
        assert exc_info.value.actor_role == "operator"

    def test_denied_tenant(self):
        action = _make_action(policies=[same_tenant()])
        with pytest.raises(PolicyDenied) as exc_info:
            enforce(operator("t-OTHER"), FakeJob("t-1"), action)
        assert "Tenant mismatch" in str(exc_info.value)

    def test_multiple_policies_all_pass(self):
        action = _make_action(policies=[role_is("operator"), same_tenant()])
        enforce(operator("t-1"), FakeJob("t-1"), action)

    def test_multiple_policies_second_fails(self):
        action = _make_action(policies=[role_is("operator"), same_tenant()])
        with pytest.raises(PolicyDenied) as exc_info:
            enforce(operator("t-2"), FakeJob("t-1"), action)
        assert exc_info.value.policy_name == "same_tenant"

    def test_system_only(self):
        action = _make_action(policies=[system_only()])
        enforce(system(), FakeJob(), action)

        with pytest.raises(PolicyDenied):
            enforce(operator(), FakeJob(), action)


# ---------------------------------------------------------------------------
# enforce: guards
# ---------------------------------------------------------------------------

class TestEnforceGuards:
    def test_passing_guard(self):
        action = _make_action(guards=[in_state("opened")])
        enforce(operator(), FakeJob(state="opened"), action)

    def test_failing_guard(self):
        action = _make_action(guards=[in_state("quote_approval")])
        with pytest.raises(GuardFailed) as exc_info:
            enforce(operator(), FakeJob(state="opened"), action)
        assert exc_info.value.guard_name == "in_state(quote_approval)"
        assert "Must be in state" in str(exc_info.value)

    def test_multiple_guards_first_fails(self):
        action = _make_action(guards=[in_state("quote_approval"), field_set("quote_amount")])
        with pytest.raises(GuardFailed) as exc_info:
            enforce(operator(), FakeJob(state="opened"), action)
        assert exc_info.value.guard_name == "in_state(quote_approval)"

    def test_multiple_guards_second_fails(self):
        action = _make_action(guards=[in_state("quote_approval"), field_set("quote_amount")])
        with pytest.raises(GuardFailed) as exc_info:
            enforce(operator(), FakeJob(state="quote_approval", quote_amount=None), action)
        assert exc_info.value.guard_name == "field_set(quote_amount)"

    def test_multiple_guards_all_pass(self):
        action = _make_action(guards=[in_state("quote_approval"), field_set("quote_amount")])
        enforce(operator(), FakeJob(state="quote_approval", quote_amount=200.0), action)


# ---------------------------------------------------------------------------
# enforce: policies run before guards
# ---------------------------------------------------------------------------

class TestPoliciesBeforeGuards:
    def test_policy_fails_before_guard_is_checked(self):
        """Even if the guard would also fail, PolicyDenied is raised first."""
        action = _make_action(
            policies=[role_is("admin")],
            guards=[in_state("quote_approval")],
        )
        with pytest.raises(PolicyDenied):
            enforce(operator(), FakeJob(state="opened"), action)

    def test_guard_checked_after_policy_passes(self):
        action = _make_action(
            policies=[role_is("admin")],
            guards=[in_state("quote_approval")],
        )
        with pytest.raises(GuardFailed):
            enforce(admin(), FakeJob(state="opened"), action)


# ---------------------------------------------------------------------------
# enforce: no policies or guards
# ---------------------------------------------------------------------------

class TestEnforceEmpty:
    def test_no_decorators(self):
        action = _make_action()
        enforce(operator(), FakeJob(), action)  # should not raise

    def test_function_without_attributes(self):
        def bare_fn():
            pass
        enforce(operator(), FakeJob(), bare_fn)  # should not raise


# ---------------------------------------------------------------------------
# can()
# ---------------------------------------------------------------------------

class TestCan:
    def test_all_pass(self):
        action = _make_action(
            policies=[role_is("operator"), same_tenant()],
            guards=[in_state("opened"), not_deleted()],
        )
        assert can(operator(), FakeJob(), action) is True

    def test_policy_fails(self):
        action = _make_action(policies=[role_is("admin")])
        assert can(operator(), FakeJob(), action) is False

    def test_guard_fails(self):
        action = _make_action(guards=[in_state("completed")])
        assert can(operator(), FakeJob(state="opened"), action) is False

    def test_both_fail(self):
        action = _make_action(
            policies=[role_is("admin")],
            guards=[in_state("completed")],
        )
        assert can(operator(), FakeJob(state="opened"), action) is False


# ---------------------------------------------------------------------------
# check_policies / check_guards
# ---------------------------------------------------------------------------

class TestCheckHelpers:
    def test_check_policies_returns_failures(self):
        action = _make_action(policies=[role_is("admin"), same_tenant()])
        failures = check_policies(operator("t-2"), FakeJob("t-1"), action)
        names = [p.name for p in failures]
        assert "role_is(admin)" in names
        assert "same_tenant" in names

    def test_check_policies_empty_on_pass(self):
        action = _make_action(policies=[role_is("operator")])
        assert check_policies(operator(), FakeJob(), action) == []

    def test_check_guards_returns_failures(self):
        action = _make_action(guards=[in_state("completed"), field_set("quote_amount")])
        failures = check_guards(FakeJob(state="opened"), action)
        names = [g.name for g in failures]
        assert "in_state(completed)" in names
        assert "field_set(quote_amount)" in names

    def test_check_guards_empty_on_pass(self):
        action = _make_action(guards=[in_state("opened")])
        assert check_guards(FakeJob(state="opened"), action) == []


# ---------------------------------------------------------------------------
# Integration: @policy + @guard decorators on a real function
# ---------------------------------------------------------------------------

class TestDecoratedFunction:
    def test_enforce_on_decorated_function(self):
        @policy(role_is("admin", "operator"))
        @policy(same_tenant())
        @guard(in_state("quote_approval"))
        @guard(field_set("quote_amount"))
        def approve_quote():
            return "approved"

        job = FakeJob(state="quote_approval", quote_amount=200.0)

        # Should pass
        enforce(operator(), job, approve_quote)

        # Function still callable
        assert approve_quote() == "approved"

    def test_can_on_decorated_function(self):
        @policy(role_is("admin"))
        @guard(in_state("opened"))
        def close():
            pass

        assert can(admin(), FakeJob(state="opened"), close) is True
        assert can(operator(), FakeJob(state="opened"), close) is False
        assert can(admin(), FakeJob(state="completed"), close) is False

    def test_actor_chain_with_initiator_is(self):
        from ironbridge.shared.framework.policies import initiator_is

        @policy(initiator_is("admin"))
        def sensitive_action():
            pass

        adm = admin()
        agent = adm.as_agent("bot")
        sub = agent.as_system("effect")

        assert can(sub, FakeJob(), sensitive_action) is True

        op = operator()
        agent_of_op = op.as_agent("bot")
        assert can(agent_of_op, FakeJob(), sensitive_action) is False
