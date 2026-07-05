"""Unit tests for Extension system."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.extension import (
    Extension,
    resolve_extensions,
    apply_extensions,
    run_before_action,
    run_after_action,
)
from ironbridge.shared.framework.graph import ResourceGraph
from ironbridge.shared.framework.relationships import belongs_to
from ironbridge.shared.framework import registry


# ---------------------------------------------------------------------------
# Test extensions
# ---------------------------------------------------------------------------

class TimestampsExt(Extension):
    def __init__(self):
        self.applied_to = []

    def on_resource(self, cls):
        self.applied_to.append(cls.__name__)


class SoftDeleteExt(Extension):
    def __init__(self, field="is_deleted"):
        self.field = field
        self.applied_to = []

    def on_resource(self, cls):
        self.applied_to.append(cls.__name__)


class AuditExt(Extension):
    def __init__(self, actions="*"):
        self.actions = actions
        self.log = []

    def after_action(self, actor, resource, action_name, result):
        if self.actions == "*" or action_name in self.actions:
            self.log.append((action_name, getattr(resource, "id", None)))


class RateLimitExt(Extension):
    def __init__(self, limit="100/min"):
        self.limit = limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_resource(name, extensions=None, relationships=None, fields=None):
    attrs = {
        "__name__": name,
        "__meta__": {
            "extensions": extensions or [],
            "table": name.lower() + "s",
        },
        "__relationships__": relationships or {},
        "__actions__": {},
        "__signals__": {},
        "__annotations__": {},
    }
    if fields:
        for f in fields:
            attrs["__annotations__"][f] = str
            attrs[f] = None
    return type(name, (), attrs)


@pytest.fixture(autouse=True)
def clean_registry():
    old = dict(registry._registry)
    registry._registry.clear()
    yield
    registry._registry.clear()
    registry._registry.update(old)


def register(*classes):
    for cls in classes:
        registry._registry[cls.__name__] = cls


# ---------------------------------------------------------------------------
# Tests: resolve_extensions
# ---------------------------------------------------------------------------

class TestResolveExtensions:
    def test_resource_level_only(self):
        ts = TimestampsExt()
        Job = make_resource("Job", extensions=[ts])

        result = resolve_extensions(Job)
        assert ts in result

    def test_module_level(self):
        ts = TimestampsExt()
        Job = make_resource("Job")

        result = resolve_extensions(Job, module_extensions=[ts])
        assert ts in result

    def test_resource_overrides_module(self):
        mod_audit = AuditExt(actions="*")
        res_audit = AuditExt(actions=["create"])

        Job = make_resource("Job", extensions=[res_audit])
        result = resolve_extensions(Job, module_extensions=[mod_audit])

        audit_exts = [e for e in result if isinstance(e, AuditExt)]
        assert len(audit_exts) == 1
        assert audit_exts[0].actions == ["create"]  # resource wins

    def test_different_types_accumulate(self):
        ts = TimestampsExt()
        sd = SoftDeleteExt()
        Job = make_resource("Job", extensions=[sd])

        result = resolve_extensions(Job, module_extensions=[ts])
        assert ts in result
        assert sd in result

    def test_graph_inherited(self):
        ts = TimestampsExt()
        Branch = make_resource("Branch", extensions=[ts], fields=["id"])
        Job = make_resource("Job", fields=["id", "branch_id"],
                           relationships={"branch": belongs_to(Branch)})
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()

        result = resolve_extensions(Job, graph=graph)
        assert ts in result

    def test_resource_overrides_graph(self):
        parent_audit = AuditExt(actions="*")
        child_audit = AuditExt(actions=["approve"])

        Branch = make_resource("Branch", extensions=[parent_audit], fields=["id"])
        Job = make_resource("Job", extensions=[child_audit],
                           fields=["id", "branch_id"],
                           relationships={"branch": belongs_to(Branch)})
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()

        result = resolve_extensions(Job, graph=graph)
        audit_exts = [e for e in result if isinstance(e, AuditExt)]
        assert len(audit_exts) == 1
        assert audit_exts[0].actions == ["approve"]

    def test_deep_graph_inheritance(self):
        ts = TimestampsExt()
        Branch = make_resource("Branch", extensions=[ts], fields=["id"])
        Job = make_resource("Job", fields=["id", "branch_id"],
                           relationships={"branch": belongs_to(Branch)})
        Invoice = make_resource("Invoice", fields=["id", "job_id"],
                               relationships={"job": belongs_to("Job")})
        register(Branch, Job, Invoice)

        graph = ResourceGraph()
        graph.build()

        result = resolve_extensions(Invoice, graph=graph)
        assert ts in result

    def test_all_three_levels(self):
        graph_ts = TimestampsExt()
        mod_rl = RateLimitExt("50/min")
        res_sd = SoftDeleteExt()

        Branch = make_resource("Branch", extensions=[graph_ts], fields=["id"])
        Job = make_resource("Job", extensions=[res_sd],
                           fields=["id", "branch_id"],
                           relationships={"branch": belongs_to(Branch)})
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()

        result = resolve_extensions(Job, module_extensions=[mod_rl], graph=graph)
        types = [type(e).__name__ for e in result]
        assert "TimestampsExt" in types
        assert "RateLimitExt" in types
        assert "SoftDeleteExt" in types

    def test_no_extensions(self):
        Job = make_resource("Job")
        result = resolve_extensions(Job)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: apply_extensions
# ---------------------------------------------------------------------------

class TestApplyExtensions:
    def test_on_resource_called(self):
        ts = TimestampsExt()
        sd = SoftDeleteExt()
        Job = make_resource("Job")

        apply_extensions(Job, [ts, sd])

        assert "Job" in ts.applied_to
        assert "Job" in sd.applied_to

    def test_stores_extensions_on_class(self):
        ts = TimestampsExt()
        Job = make_resource("Job")

        apply_extensions(Job, [ts])
        assert Job.__extensions__ == [ts]

    def test_on_action_called(self):
        called = []

        class ActionTracker(Extension):
            def on_action(self, cls, action_name, meta):
                called.append((cls.__name__, action_name))

        Job = make_resource("Job")
        Job.__actions__ = {"create": "meta1", "update": "meta2"}

        apply_extensions(Job, [ActionTracker()])
        assert ("Job", "create") in called
        assert ("Job", "update") in called

    def test_on_signal_called(self):
        called = []

        class SignalTracker(Extension):
            def on_signal(self, cls, signal_name, sdef):
                called.append((cls.__name__, signal_name))

        Job = make_resource("Job")
        Job.__signals__ = {"open": "sdef1", "approval": "sdef2"}

        apply_extensions(Job, [SignalTracker()])
        assert ("Job", "open") in called
        assert ("Job", "approval") in called


# ---------------------------------------------------------------------------
# Tests: per-request hooks
# ---------------------------------------------------------------------------

class TestPerRequestHooks:
    def test_before_action(self):
        log = []

        class Logger(Extension):
            def before_action(self, actor, resource, action_name, **kwargs):
                log.append(("before", action_name))

        Job = make_resource("Job")
        Job.__extensions__ = [Logger()]

        job = Job()
        job.id = "j-1"
        run_before_action(job, None, "create")

        assert log == [("before", "create")]

    def test_after_action(self):
        audit = AuditExt(actions="*")
        Job = make_resource("Job")
        Job.__extensions__ = [audit]

        job = Job()
        job.id = "j-1"
        run_after_action(job, None, "approve", None)

        assert ("approve", "j-1") in audit.log

    def test_after_action_filtered(self):
        audit = AuditExt(actions=["create"])
        Job = make_resource("Job")
        Job.__extensions__ = [audit]

        job = Job()
        job.id = "j-1"
        run_after_action(job, None, "update", None)

        assert len(audit.log) == 0  # "update" not in actions

    def test_multiple_extensions_all_run(self):
        log = []

        class Ext1(Extension):
            def before_action(self, actor, resource, action_name, **kwargs):
                log.append("ext1")

        class Ext2(Extension):
            def before_action(self, actor, resource, action_name, **kwargs):
                log.append("ext2")

        Job = make_resource("Job")
        Job.__extensions__ = [Ext1(), Ext2()]

        run_before_action(Job(), None, "create")
        assert log == ["ext1", "ext2"]

    def test_no_extensions(self):
        Job = make_resource("Job")
        job = Job()
        # Should not raise
        run_before_action(job, None, "create")
        run_after_action(job, None, "create", None)


# ---------------------------------------------------------------------------
# Tests: extension_type dedup
# ---------------------------------------------------------------------------

class TestExtensionType:
    def test_same_class_same_type(self):
        a = AuditExt(actions="*")
        b = AuditExt(actions=["create"])
        assert a.extension_type == b.extension_type

    def test_different_class_different_type(self):
        a = AuditExt()
        b = TimestampsExt()
        assert a.extension_type != b.extension_type
