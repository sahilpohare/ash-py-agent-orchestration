"""Unit tests for dependency injection."""

import pytest

from ironbridge.shared.framework.depends import Providers, Deps, get_providers, set_providers
from ironbridge.shared.framework.workflow import WorkflowContext
from ironbridge.shared.framework.actor import from_request


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class TestProviders:
    def test_register_instance(self):
        p = Providers()
        p.register("messaging", "twilio_instance")
        assert p.resolve("messaging") == "twilio_instance"

    def test_register_factory(self):
        call_count = [0]
        def factory():
            call_count[0] += 1
            return f"instance_{call_count[0]}"

        p = Providers()
        p.register("service", factory)
        assert p.resolve("service") == "instance_1"
        # Lazy: factory called once, cached
        assert p.resolve("service") == "instance_1"
        assert call_count[0] == 1

    def test_resolve_missing_raises(self):
        p = Providers()
        with pytest.raises(KeyError, match="messaging"):
            p.resolve("messaging")

    def test_has(self):
        p = Providers()
        assert p.has("x") is False
        p.register("x", "val")
        assert p.has("x") is True

    def test_has_factory(self):
        p = Providers()
        p.register("x", lambda: "val")
        assert p.has("x") is True

    def test_all_resolves_everything(self):
        p = Providers()
        p.register("a", "val_a")
        p.register("b", lambda: "val_b")
        all_deps = p.all()
        assert all_deps == {"a": "val_a", "b": "val_b"}

    def test_overwrite(self):
        p = Providers()
        p.register("x", "first")
        p.register("x", "second")
        assert p.resolve("x") == "second"

    def test_register_class_as_instance(self):
        """A class (not instance) is stored as-is, not called as factory."""
        class MyService:
            pass
        p = Providers()
        p.register("svc", MyService)
        assert p.resolve("svc") is MyService


# ---------------------------------------------------------------------------
# Deps (attribute accessor)
# ---------------------------------------------------------------------------

class TestDeps:
    def test_attribute_access(self):
        p = Providers()
        p.register("messaging", "twilio")
        deps = Deps(p)
        assert deps.messaging == "twilio"

    def test_missing_raises_attribute_error(self):
        p = Providers()
        deps = Deps(p)
        with pytest.raises(AttributeError, match="messaging"):
            _ = deps.messaging

    def test_get_with_default(self):
        p = Providers()
        deps = Deps(p)
        assert deps.get("messaging", "fallback") == "fallback"

    def test_get_found(self):
        p = Providers()
        p.register("messaging", "twilio")
        deps = Deps(p)
        assert deps.get("messaging") == "twilio"

    def test_multiple_deps(self):
        p = Providers()
        p.register("messaging", "twilio")
        p.register("crm", "alto")
        p.register("llm", "anthropic")
        deps = Deps(p)
        assert deps.messaging == "twilio"
        assert deps.crm == "alto"
        assert deps.llm == "anthropic"


# ---------------------------------------------------------------------------
# Global providers
# ---------------------------------------------------------------------------

class TestGlobalProviders:
    def test_get_set(self):
        original = get_providers()
        try:
            p = Providers()
            p.register("test", "value")
            set_providers(p)
            assert get_providers().resolve("test") == "value"
        finally:
            set_providers(original)


# ---------------------------------------------------------------------------
# WorkflowContext.deps
# ---------------------------------------------------------------------------

class TestContextDeps:
    def test_deps_injected(self):
        p = Providers()
        p.register("messaging", "twilio_mock")
        deps = Deps(p)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            deps=deps,
        )
        assert ctx.deps.messaging == "twilio_mock"

    def test_deps_from_global_providers(self):
        original = get_providers()
        try:
            p = Providers()
            p.register("global_dep", "global_value")
            set_providers(p)

            ctx = WorkflowContext(
                actor=from_request("u-1", "t-1", "admin"),
                resource=None,
                # no deps= passed, uses global
            )
            assert ctx.deps.global_dep == "global_value"
        finally:
            set_providers(original)

    def test_deps_missing_raises(self):
        p = Providers()
        deps = Deps(p)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            deps=deps,
        )
        with pytest.raises(AttributeError, match="nonexistent"):
            _ = ctx.deps.nonexistent


# ---------------------------------------------------------------------------
# Module declares depends
# ---------------------------------------------------------------------------

class TestModuleDeps:
    def test_module_can_declare_depends(self):
        """Modules can declare what deps they need (documentation/validation)."""
        from ironbridge.shared.framework.module import Module

        class MaintenanceModule(Module):
            prefix = "/maintenance"
            resources = []
            depends = {"messaging": "TwilioMessaging", "contractors": "ContractorRepo"}

        assert MaintenanceModule.depends["messaging"] == "TwilioMessaging"
