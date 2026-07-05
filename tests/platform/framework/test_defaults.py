"""Unit tests for default actions and default_action()."""

import pytest

from ironbridge.shared.framework.actions import ActionKind
from ironbridge.shared.framework.defaults import default_action, inject_defaults
from ironbridge.shared.framework.guards import in_state, field_set
from ironbridge.shared.framework.policies import system_only, role_is


# ---------------------------------------------------------------------------
# default_action() — explicit declaration
# ---------------------------------------------------------------------------

class TestDefaultAction:
    def test_returns_callable(self):
        fn = default_action(ActionKind.CREATE)
        assert callable(fn)

    def test_has_action_meta(self):
        fn = default_action(ActionKind.CREATE)
        assert hasattr(fn, "__action__")
        assert fn.__action__.kind == ActionKind.CREATE

    def test_read_kind(self):
        fn = default_action(ActionKind.READ)
        assert fn.__action__.kind == ActionKind.READ

    def test_update_kind(self):
        fn = default_action(ActionKind.UPDATE)
        assert fn.__action__.kind == ActionKind.UPDATE

    def test_destroy_kind(self):
        fn = default_action(ActionKind.DESTROY)
        assert fn.__action__.kind == ActionKind.DESTROY

    def test_custom_policies(self):
        fn = default_action(ActionKind.DESTROY, policies=[system_only()])
        policies = getattr(fn, "_policies", [])
        names = [p.name for p in policies]
        assert "system_only" in names

    def test_custom_guards(self):
        fn = default_action(ActionKind.DESTROY, guards=[in_state("closed")])
        guards = getattr(fn, "_guards", [])
        names = [g.name for g in guards]
        assert "in_state(closed)" in names

    def test_policies_and_guards_together(self):
        fn = default_action(
            ActionKind.DESTROY,
            policies=[system_only()],
            guards=[in_state("closed"), field_set("reason")],
        )
        assert len(fn._policies) == 1
        assert len(fn._guards) == 2

    def test_marked_as_default(self):
        fn = default_action(ActionKind.CREATE)
        assert fn._is_default_action is True

    def test_create_body_sets_fields(self):
        fn = default_action(ActionKind.CREATE)

        class Fake:
            name = None
            status = None

        obj = Fake()
        result = fn(obj, name="Test", status="active")
        assert result.name == "Test"
        assert result.status == "active"

    def test_create_ignores_unknown_fields(self):
        fn = default_action(ActionKind.CREATE)

        class Fake:
            name = None

        obj = Fake()
        result = fn(obj, name="Test", nonexistent="ignored")
        assert result.name == "Test"
        assert not hasattr(result, "nonexistent")

    def test_update_body_partial(self):
        fn = default_action(ActionKind.UPDATE)

        class Fake:
            name = "old"
            status = "active"

        obj = Fake()
        result = fn(obj, name="new")
        assert result.name == "new"
        assert result.status == "active"

    def test_delete_body_soft_deletes(self):
        fn = default_action(ActionKind.DESTROY)

        class Fake:
            is_deleted = False

        obj = Fake()
        result = fn(obj)
        assert result.is_deleted is True

    def test_delete_body_no_is_deleted(self):
        fn = default_action(ActionKind.DESTROY)

        class Fake:
            pass

        obj = Fake()
        result = fn(obj)  # should not raise
        assert result is obj

    def test_get_body_returns_self(self):
        fn = default_action(ActionKind.READ)

        class Fake:
            pass

        obj = Fake()
        assert fn(obj) is obj


# ---------------------------------------------------------------------------
# inject_defaults — Meta.default_actions = True
# ---------------------------------------------------------------------------

class TestInjectDefaultsTrue:
    def test_injects_all_five(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": True})

        assert "create" in namespace
        assert "get" in namespace
        assert "list" in namespace
        assert "update" in namespace
        assert "delete" in namespace

    def test_each_has_action_meta(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": False})

        expected_kinds = {
            "create": ActionKind.CREATE,
            "get": ActionKind.READ,
            "list": ActionKind.READ,
            "update": ActionKind.UPDATE,
            "delete": ActionKind.DESTROY,
        }
        for name, kind in expected_kinds.items():
            fn = namespace[name]
            assert hasattr(fn, "__action__"), f"{name} missing __action__"
            assert fn.__action__.kind == kind, f"{name} wrong kind"
            assert fn.__action__.name == name, f"{name} wrong name"


# ---------------------------------------------------------------------------
# inject_defaults — Meta.default_actions = [list]
# ---------------------------------------------------------------------------

class TestInjectDefaultsList:
    def test_subset(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": ["create", "get"], "tenant_scoped": False})

        assert "create" in namespace
        assert "get" in namespace
        assert "list" not in namespace
        assert "update" not in namespace
        assert "delete" not in namespace

    def test_empty_list(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": [], "tenant_scoped": False})

        assert len([k for k in namespace if not k.startswith("_")]) == 0

    def test_invalid_name_raises(self):
        namespace = {}
        with pytest.raises(ValueError, match="Unknown default action"):
            inject_defaults(namespace, {"default_actions": ["create", "bogus"], "tenant_scoped": False})


# ---------------------------------------------------------------------------
# inject_defaults — False / missing
# ---------------------------------------------------------------------------

class TestInjectDefaultsFalse:
    def test_false(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": False})
        assert "create" not in namespace

    def test_missing(self):
        namespace = {}
        inject_defaults(namespace, {})
        assert "create" not in namespace


# ---------------------------------------------------------------------------
# inject_defaults — does not override user definitions
# ---------------------------------------------------------------------------

class TestInjectDefaultsNoOverride:
    def test_user_defined_not_replaced(self):
        sentinel = object()
        namespace = {"create": sentinel}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": False})

        assert namespace["create"] is sentinel
        assert "get" in namespace  # others injected

    def test_explicit_default_action_not_replaced(self):
        custom_create = default_action(ActionKind.CREATE, policies=[system_only()])
        namespace = {"create": custom_create}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": True})

        # The explicit one is kept, not replaced by the Meta default
        assert namespace["create"] is custom_create
        # Its name is fixed up
        assert custom_create.__action__.name == "create"

    def test_explicit_default_action_policies_preserved(self):
        custom_delete = default_action(
            ActionKind.DESTROY,
            policies=[system_only()],
            guards=[in_state("closed")],
        )
        namespace = {"delete": custom_delete}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": True})

        fn = namespace["delete"]
        policy_names = [p.name for p in getattr(fn, "_policies", [])]
        guard_names = [g.name for g in getattr(fn, "_guards", [])]
        assert "system_only" in policy_names
        assert "in_state(closed)" in guard_names


# ---------------------------------------------------------------------------
# inject_defaults — tenant_scoped policies
# ---------------------------------------------------------------------------

class TestInjectDefaultsTenantPolicies:
    def test_tenant_scoped_adds_same_tenant(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": True})

        for name in ("create", "get", "list", "update", "delete"):
            fn = namespace[name]
            policies = getattr(fn, "_policies", [])
            policy_names = [p.name for p in policies]
            assert "same_tenant" in policy_names, f"{name} missing same_tenant"

    def test_non_tenant_scoped_no_same_tenant(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": False})

        fn = namespace["get"]
        policies = getattr(fn, "_policies", [])
        policy_names = [p.name for p in policies]
        assert "same_tenant" not in policy_names

    def test_write_actions_require_role(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": False})

        for name in ("create", "update", "delete"):
            fn = namespace[name]
            policies = getattr(fn, "_policies", [])
            policy_names = [p.name for p in policies]
            assert any("role_is" in n for n in policy_names), f"{name} missing role_is"

    def test_read_actions_allow_anyone(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": False})

        for name in ("get", "list"):
            fn = namespace[name]
            policies = getattr(fn, "_policies", [])
            policy_names = [p.name for p in policies]
            assert "anyone" in policy_names, f"{name} missing anyone"

    def test_update_and_delete_have_not_deleted_guard(self):
        namespace = {}
        inject_defaults(namespace, {"default_actions": True, "tenant_scoped": False})

        for name in ("update", "delete"):
            fn = namespace[name]
            guards = getattr(fn, "_guards", [])
            guard_names = [g.name for g in guards]
            assert "not_deleted" in guard_names, f"{name} missing not_deleted guard"
