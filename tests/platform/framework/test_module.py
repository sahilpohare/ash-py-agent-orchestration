"""Unit tests for Module and lifecycle hooks."""

import pytest

from ironbridge.shared.framework.module import Module, init_modules, ready_modules, shutdown_modules
from ironbridge.shared.framework.depends import Providers


class TestModule:
    def test_empty_module(self):
        class EmptyModule(Module):
            prefix = "/empty"
            resources = []
            modules = []

        assert EmptyModule.prefix == "/empty"
        assert EmptyModule.all_resources() == []

    def test_module_with_resources(self):
        class Fake:
            pass

        class MyModule(Module):
            prefix = "/my"
            resources = [Fake]

        assert MyModule.all_resources() == [Fake]

    def test_nested_modules(self):
        class A:
            pass
        class B:
            pass
        class C:
            pass

        class Child(Module):
            prefix = "/child"
            resources = [C]

        class Parent(Module):
            prefix = "/parent"
            resources = [A, B]
            modules = [Child]

        all_res = Parent.all_resources()
        assert len(all_res) == 3
        assert A in all_res and B in all_res and C in all_res

    def test_deeply_nested(self):
        class R1:
            pass
        class R2:
            pass
        class R3:
            pass

        class Level2(Module):
            prefix = "/l2"
            resources = [R3]

        class Level1(Module):
            prefix = "/l1"
            resources = [R2]
            modules = [Level2]

        class Root(Module):
            prefix = "/api"
            resources = [R1]
            modules = [Level1]

        assert len(Root.all_resources()) == 3


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

class TestLifecycleHooks:
    def test_on_init_called(self):
        log = []

        class MyModule(Module):
            prefix = "/test"

            @classmethod
            def on_init(cls, providers):
                log.append(("init", providers))

        p = Providers()
        init_modules([MyModule], p)
        assert len(log) == 1
        assert log[0] == ("init", p)

    def test_on_init_registers_deps(self):
        class MyModule(Module):
            prefix = "/test"

            @classmethod
            def on_init(cls, providers):
                providers.register("my_service", "my_value")

        p = Providers()
        init_modules([MyModule], p)
        assert p.resolve("my_service") == "my_value"

    def test_on_init_can_resolve_shared_deps(self):
        class MyModule(Module):
            prefix = "/test"

            @classmethod
            def on_init(cls, providers):
                db = providers.resolve("db")
                providers.register("repo", f"repo_with_{db}")

        p = Providers()
        p.register("db", "postgres_engine")
        init_modules([MyModule], p)
        assert p.resolve("repo") == "repo_with_postgres_engine"

    def test_on_init_nested(self):
        order = []

        class Child(Module):
            prefix = "/child"

            @classmethod
            def on_init(cls, providers):
                order.append("child")

        class Parent(Module):
            prefix = "/parent"
            modules = [Child]

            @classmethod
            def on_init(cls, providers):
                order.append("parent")

        init_modules([Parent], Providers())
        assert order == ["parent", "child"]

    def test_on_ready_called(self):
        log = []

        class MyModule(Module):
            @classmethod
            def on_ready(cls):
                log.append("ready")

        ready_modules([MyModule])
        assert log == ["ready"]

    def test_on_ready_nested(self):
        order = []

        class Child(Module):
            @classmethod
            def on_ready(cls):
                order.append("child")

        class Parent(Module):
            modules = [Child]

            @classmethod
            def on_ready(cls):
                order.append("parent")

        ready_modules([Parent])
        assert order == ["parent", "child"]

    def test_on_shutdown_reverse_order(self):
        order = []

        class Child(Module):
            modules = []

            @classmethod
            def on_shutdown(cls):
                order.append("child")

        class Parent(Module):
            modules = [Child]

            @classmethod
            def on_shutdown(cls):
                order.append("parent")

        shutdown_modules([Parent])
        # Child shuts down before parent (reverse)
        assert order == ["child", "parent"]

    def test_multiple_modules_init_order(self):
        order = []

        class ModA(Module):
            @classmethod
            def on_init(cls, providers):
                order.append("A")

        class ModB(Module):
            @classmethod
            def on_init(cls, providers):
                order.append("B")

        class ModC(Module):
            @classmethod
            def on_init(cls, providers):
                order.append("C")

        init_modules([ModA, ModB, ModC], Providers())
        assert order == ["A", "B", "C"]

    def test_default_hooks_are_noop(self):
        """Modules without hooks don't crash."""
        class Plain(Module):
            prefix = "/plain"
            resources = []

        p = Providers()
        init_modules([Plain], p)
        ready_modules([Plain])
        shutdown_modules([Plain])


# ---------------------------------------------------------------------------
# Self-contained module
# ---------------------------------------------------------------------------

class TestSelfContainedModule:
    def test_module_wires_its_own_deps(self):
        """A module registers its own deps from shared clients."""
        class MaintenanceModule(Module):
            prefix = "/maintenance"

            @classmethod
            def on_init(cls, providers):
                twilio = providers.resolve("twilio")
                providers.register("messaging", f"messaging_with_{twilio}")
                providers.register("contractors", "contractor_repo")

        p = Providers()
        p.register("twilio", "twilio_client")
        init_modules([MaintenanceModule], p)

        assert p.resolve("messaging") == "messaging_with_twilio_client"
        assert p.resolve("contractors") == "contractor_repo"

    def test_modules_share_providers(self):
        """Module A registers a dep, Module B uses it."""
        class ModA(Module):
            @classmethod
            def on_init(cls, providers):
                providers.register("shared_service", "from_A")

        class ModB(Module):
            @classmethod
            def on_init(cls, providers):
                svc = providers.resolve("shared_service")
                providers.register("b_result", f"B_got_{svc}")

        p = Providers()
        init_modules([ModA, ModB], p)
        assert p.resolve("b_result") == "B_got_from_A"
