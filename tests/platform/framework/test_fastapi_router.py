"""
Unit tests for FastAPI router derivation.

Tests route generation from Resource actions, NOT actual HTTP calls
(those need a running DB). These verify the router shape is correct.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ironbridge.shared.framework.actions import ActionKind, action
from ironbridge.shared.framework.guards import guard, in_state
from ironbridge.shared.framework.policies import policy, role_is, same_tenant
from ironbridge.shared.derive.fastapi_router import _serialize, _snake_plural, derive_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# We can't create actual SQLAlchemy-backed Resources without a DB engine,
# so we test route generation logic and serialization separately.


class TestSnakePlural:
    def test_simple(self):
        assert _snake_plural("MaintenanceJob") == "maintenance_jobs"

    def test_single_word(self):
        assert _snake_plural("Call") == "calls"

    def test_two_words(self):
        assert _snake_plural("AgentConfig") == "agent_configs"

    def test_three_words(self):
        assert _snake_plural("CalendarConnection") == "calendar_connections"


class TestSerialize:
    def test_none(self):
        assert _serialize(None) is None

    def test_dict(self):
        assert _serialize({"a": 1}) == {"a": 1}

    def test_list(self):
        assert _serialize([{"a": 1}, {"b": 2}]) == [{"a": 1}, {"b": 2}]

    def test_datetime(self):
        dt = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
        assert _serialize(dt) == "2026-07-03T12:00:00+00:00"

    def test_string(self):
        assert _serialize("hello") == "hello"

    def test_int(self):
        assert _serialize(42) == 42


class TestDeriveRouterShape:
    """Test that derive_router produces routes with correct paths and methods.

    Uses a minimal mock to avoid needing real SQLAlchemy models.
    """

    def _fake_resource_cls(self, actions_dict, tablename="widgets"):
        """Build a fake resource class with __actions__ and __tablename__."""
        cls = type("FakeResource", (), {
            "__actions__": actions_dict,
            "__tablename__": tablename,
            "__meta__": {"tenant_scoped": True},
            "__name__": "Widget",
        })
        return cls

    def test_create_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "create": ActionMeta(name="create", kind=ActionKind.CREATE, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets", {"POST"}) in paths

    def test_get_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "get": ActionMeta(name="get", kind=ActionKind.READ, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets/{id}", {"GET"}) in paths

    def test_list_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "list": ActionMeta(name="list", kind=ActionKind.READ, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets", {"GET"}) in paths

    def test_update_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "update": ActionMeta(name="update", kind=ActionKind.UPDATE, fn=lambda self, **kw: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets/{id}", {"PATCH"}) in paths

    def test_delete_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "delete": ActionMeta(name="delete", kind=ActionKind.DESTROY, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets/{id}", {"DELETE"}) in paths

    def test_custom_action_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "approve_quote": ActionMeta(name="approve_quote", kind=ActionKind.ACTION, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets/{id}/approve_quote", {"POST"}) in paths

    def test_custom_read_action_route(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "summary": ActionMeta(name="summary", kind=ActionKind.READ, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))

        paths = [(r.path, r.methods) for r in router.routes]
        assert ("/widgets/{id}/summary", {"GET"}) in paths

    def test_full_crud_plus_custom(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "create": ActionMeta(name="create", kind=ActionKind.CREATE, fn=lambda self: self),
            "get": ActionMeta(name="get", kind=ActionKind.READ, fn=lambda self: self),
            "list": ActionMeta(name="list", kind=ActionKind.READ, fn=lambda self: self),
            "update": ActionMeta(name="update", kind=ActionKind.UPDATE, fn=lambda self, **kw: self),
            "delete": ActionMeta(name="delete", kind=ActionKind.DESTROY, fn=lambda self: self),
            "approve": ActionMeta(name="approve", kind=ActionKind.ACTION, fn=lambda self: self),
            "stats": ActionMeta(name="stats", kind=ActionKind.READ, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions))
        paths = [(r.path, r.methods) for r in router.routes]

        assert ("/widgets", {"POST"}) in paths       # create
        assert ("/widgets/{id}", {"GET"}) in paths    # get
        assert ("/widgets", {"GET"}) in paths         # list
        assert ("/widgets/{id}", {"PATCH"}) in paths  # update
        assert ("/widgets/{id}", {"DELETE"}) in paths # delete
        assert ("/widgets/{id}/approve", {"POST"}) in paths  # custom action
        assert ("/widgets/{id}/stats", {"GET"}) in paths     # custom read

    def test_custom_prefix(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "get": ActionMeta(name="get", kind=ActionKind.READ, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions), prefix="/custom_prefix")

        # Router prefix is set, routes are relative
        assert router.prefix == "/custom_prefix"

    def test_custom_tablename(self):
        from ironbridge.shared.framework.actions import ActionMeta

        actions = {
            "get": ActionMeta(name="get", kind=ActionKind.READ, fn=lambda self: self),
        }
        router = derive_router(self._fake_resource_cls(actions, tablename="maintenance_jobs"))

        assert router.prefix == "/maintenance_jobs"
