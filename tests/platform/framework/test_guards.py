"""Unit tests for guards."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.guards import (
    GuardDef,
    custom,
    field_equals,
    field_set,
    field_true,
    guard,
    in_state,
    not_deleted,
    not_in_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeJob:
    state: str = "opened"
    is_deleted: bool = False
    quote_amount: float | None = None
    contractor_id: str | None = None
    approved: bool = False
    urgency: str = "routine"


# ---------------------------------------------------------------------------
# in_state
# ---------------------------------------------------------------------------

class TestInState:
    def test_matching_state(self):
        g = in_state("opened", "sourcing")
        assert g.check(FakeJob(state="opened")) is True
        assert g.check(FakeJob(state="sourcing")) is True

    def test_non_matching_state(self):
        g = in_state("quote_approval")
        assert g.check(FakeJob(state="opened")) is False

    def test_custom_field(self):
        g = in_state("emergency", field="urgency")
        assert g.check(FakeJob(urgency="emergency")) is True
        assert g.check(FakeJob(urgency="routine")) is False

    def test_name(self):
        g = in_state("a", "b")
        assert g.name == "in_state(a,b)"

    def test_message(self):
        g = in_state("opened")
        assert "opened" in g.message


# ---------------------------------------------------------------------------
# not_in_state
# ---------------------------------------------------------------------------

class TestNotInState:
    def test_blocks_matching_state(self):
        g = not_in_state("completed", "cancelled")
        assert g.check(FakeJob(state="completed")) is False

    def test_allows_other_states(self):
        g = not_in_state("completed", "cancelled")
        assert g.check(FakeJob(state="opened")) is True


# ---------------------------------------------------------------------------
# not_deleted
# ---------------------------------------------------------------------------

class TestNotDeleted:
    def test_not_deleted(self):
        g = not_deleted()
        assert g.check(FakeJob(is_deleted=False)) is True

    def test_deleted(self):
        g = not_deleted()
        assert g.check(FakeJob(is_deleted=True)) is False

    def test_missing_field_passes(self):
        """Resource without is_deleted should pass (defaults to False)."""
        g = not_deleted()

        @dataclass
        class NoDelete:
            pass

        assert g.check(NoDelete()) is True


# ---------------------------------------------------------------------------
# field_set
# ---------------------------------------------------------------------------

class TestFieldSet:
    def test_field_present(self):
        g = field_set("quote_amount")
        assert g.check(FakeJob(quote_amount=200.0)) is True

    def test_field_none(self):
        g = field_set("quote_amount")
        assert g.check(FakeJob(quote_amount=None)) is False

    def test_multiple_fields_all_set(self):
        g = field_set("quote_amount", "contractor_id")
        job = FakeJob(quote_amount=100.0, contractor_id="c-1")
        assert g.check(job) is True

    def test_multiple_fields_one_missing(self):
        g = field_set("quote_amount", "contractor_id")
        job = FakeJob(quote_amount=100.0, contractor_id=None)
        assert g.check(job) is False

    def test_name(self):
        g = field_set("a", "b")
        assert g.name == "field_set(a,b)"


# ---------------------------------------------------------------------------
# field_equals
# ---------------------------------------------------------------------------

class TestFieldEquals:
    def test_matches(self):
        g = field_equals("urgency", "emergency")
        assert g.check(FakeJob(urgency="emergency")) is True

    def test_no_match(self):
        g = field_equals("urgency", "emergency")
        assert g.check(FakeJob(urgency="routine")) is False

    def test_none_value(self):
        g = field_equals("contractor_id", None)
        assert g.check(FakeJob(contractor_id=None)) is True
        assert g.check(FakeJob(contractor_id="c-1")) is False


# ---------------------------------------------------------------------------
# field_true
# ---------------------------------------------------------------------------

class TestFieldTrue:
    def test_truthy(self):
        g = field_true("approved")
        assert g.check(FakeJob(approved=True)) is True

    def test_falsy(self):
        g = field_true("approved")
        assert g.check(FakeJob(approved=False)) is False

    def test_missing_field(self):
        g = field_true("nonexistent")

        @dataclass
        class Empty:
            pass

        assert g.check(Empty()) is False


# ---------------------------------------------------------------------------
# custom
# ---------------------------------------------------------------------------

class TestCustom:
    def test_passes(self):
        g = custom(
            "under_cap",
            lambda r, **kw: (r.quote_amount or 0) < 500,
            "Quote exceeds cap",
        )
        assert g.check(FakeJob(quote_amount=200.0)) is True

    def test_fails(self):
        g = custom(
            "under_cap",
            lambda r, **kw: (r.quote_amount or 0) < 500,
            "Quote exceeds cap",
        )
        assert g.check(FakeJob(quote_amount=600.0)) is False

    def test_with_kwargs(self):
        g = custom(
            "amount_match",
            lambda r, **kw: kw.get("amount") == r.quote_amount,
        )
        assert g.check(FakeJob(quote_amount=100.0), amount=100.0) is True
        assert g.check(FakeJob(quote_amount=100.0), amount=200.0) is False

    def test_custom_message(self):
        g = custom("test", lambda r, **kw: False, "Nope")
        assert g.message == "Nope"


# ---------------------------------------------------------------------------
# @guard decorator
# ---------------------------------------------------------------------------

class TestGuardDecorator:
    def test_attaches_guards(self):
        @guard(in_state("opened"))
        def my_action():
            pass

        assert len(my_action._guards) == 1
        assert my_action._guards[0].name == "in_state(opened)"

    def test_stacking(self):
        @guard(field_set("quote_amount"))
        @guard(in_state("quote_approval"))
        def my_action():
            pass

        assert len(my_action._guards) == 2

    def test_preserves_function_behavior(self):
        @guard(not_deleted())
        def double(x):
            return x * 2

        assert double(5) == 10

    def test_preserves_policies(self):
        def fn():
            pass
        fn._policies = ["some_policy"]

        decorated = guard(not_deleted())(fn)
        assert decorated._policies == ["some_policy"]
