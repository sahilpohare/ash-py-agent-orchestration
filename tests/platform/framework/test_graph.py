"""Unit tests for ResourceGraph."""

import pytest

from ironbridge.shared.framework.relationships import belongs_to, has_many, has_one, many_to_many, references
from ironbridge.shared.framework.graph import ResourceGraph, _has_field
from ironbridge.shared.framework import registry


# ---------------------------------------------------------------------------
# Fake resources (can't use real SQLAlchemy Resources without DB)
# ---------------------------------------------------------------------------

class FakeMeta:
    """Simulate __meta__ and __relationships__ on plain classes."""
    pass


def make_resource(name, relationships=None, fields=None, meta=None):
    """Create a fake resource class with the right dunder attributes."""
    attrs = {
        "__name__": name,
        "__relationships__": relationships or {},
        "__meta__": meta or {"table": name.lower() + "s"},
        "__annotations__": {},
    }
    if fields:
        for f in fields:
            attrs["__annotations__"][f] = str
            attrs[f] = None

    cls = type(name, (), attrs)
    return cls


# ---------------------------------------------------------------------------
# Setup: register fake resources
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_registry():
    """Clear and populate registry for each test."""
    old = dict(registry._registry)
    registry._registry.clear()
    yield
    registry._registry.clear()
    registry._registry.update(old)


def register(*classes):
    for cls in classes:
        registry._registry[cls.__name__] = cls


# ---------------------------------------------------------------------------
# Tests: graph building
# ---------------------------------------------------------------------------

class TestGraphBuild:
    def test_empty_graph(self):
        graph = ResourceGraph()
        graph.build()
        assert graph.all() == {}
        assert graph.all_relationships() == []

    def test_collects_resources(self):
        Branch = make_resource("Branch", fields=["id"])
        register(Branch)

        graph = ResourceGraph()
        graph.build()

        assert "Branch" in graph.all()

    def test_resolves_belongs_to_class_ref(self):
        Branch = make_resource("Branch", fields=["id"])
        Job = make_resource("Job",
            fields=["id", "branch_id"],
            relationships={"branch": belongs_to(Branch)},
        )
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()

        rels = graph.relationships_for(Job)
        assert len(rels) == 1
        assert rels[0].kind == "belongs_to"
        assert rels[0].target is Branch
        assert rels[0].key == "branch_id"

    def test_resolves_belongs_to_string_ref(self):
        Branch = make_resource("Branch", fields=["id"])
        Job = make_resource("Job",
            fields=["id", "branch_id"],
            relationships={"branch": belongs_to("Branch")},
        )
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()

        rels = graph.relationships_for(Job)
        assert len(rels) == 1
        assert rels[0].target is Branch

    def test_resolves_has_many(self):
        Job = make_resource("Job", fields=["id"])
        Invoice = make_resource("Invoice",
            fields=["id", "job_id"],
            relationships={"job": belongs_to("Job")},
        )
        Job = make_resource("Job",
            fields=["id"],
            relationships={"invoices": has_many(Invoice, key="job_id")},
        )
        register(Job, Invoice)

        graph = ResourceGraph()
        graph.build()

        rels = graph.has_many_for(Job)
        assert len(rels) == 1
        assert rels[0].target is Invoice
        assert rels[0].key == "job_id"

    def test_infers_has_many_key(self):
        Job = make_resource("Job",
            fields=["id"],
            relationships={"invoices": has_many("Invoice")},
        )
        Invoice = make_resource("Invoice", fields=["id", "job_id"])
        register(Job, Invoice)

        graph = ResourceGraph()
        graph.build()

        rels = graph.has_many_for(Job)
        assert rels[0].key == "job_id"

    def test_resolves_has_one(self):
        Job = make_resource("Job",
            fields=["id"],
            relationships={"current_invoice": has_one("Invoice", key="job_id")},
        )
        Invoice = make_resource("Invoice", fields=["id", "job_id"])
        register(Job, Invoice)

        graph = ResourceGraph()
        graph.build()

        rels = graph.has_many_for(Job)  # has_one included in has_many_for
        assert len(rels) == 1
        assert rels[0].kind == "has_one"

    def test_resolves_many_to_many(self):
        Lead = make_resource("Lead", fields=["id"])
        Call = make_resource("Call", fields=["id"])
        LeadInteraction = make_resource("LeadInteraction",
            fields=["id", "lead_id", "call_id"],
        )
        Lead = make_resource("Lead",
            fields=["id"],
            relationships={
                "calls": many_to_many(Call, through=LeadInteraction,
                                      source_key="lead_id", target_key="call_id"),
            },
        )
        register(Lead, Call, LeadInteraction)

        graph = ResourceGraph()
        graph.build()

        rels = graph.relationships_for(Lead)
        assert len(rels) == 1
        assert rels[0].kind == "many_to_many"
        assert rels[0].target is Call
        assert rels[0].through is LeadInteraction


# ---------------------------------------------------------------------------
# Tests: queries
# ---------------------------------------------------------------------------

class TestGraphQueries:
    @pytest.fixture
    def populated_graph(self):
        Branch = make_resource("Branch", fields=["id"])
        Job = make_resource("Job",
            fields=["id", "branch_id"],
            relationships={
                "branch": belongs_to(Branch),
                "invoices": has_many("Invoice", key="job_id"),
                "messages": has_many("JobMessage", key="job_id"),
            },
        )
        Invoice = make_resource("Invoice",
            fields=["id", "job_id"],
            relationships={"job": belongs_to("Job")},
        )
        JobMessage = make_resource("JobMessage",
            fields=["id", "job_id"],
            relationships={"job": belongs_to("Job")},
        )
        register(Branch, Job, Invoice, JobMessage)

        graph = ResourceGraph()
        graph.build()
        return graph, Branch, Job, Invoice, JobMessage

    def test_children_of(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        children = graph.children_of(Job)
        assert Invoice in children
        assert JobMessage in children

    def test_children_of_leaf(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        assert graph.children_of(Invoice) == []

    def test_parent_of(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        assert graph.parent_of(Invoice) is Job
        assert graph.parent_of(Job) is Branch

    def test_parent_of_root(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        assert graph.parent_of(Branch) is None

    def test_parents_of(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        parents = graph.parents_of(Job)
        assert Branch in parents

    def test_ancestry(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        chain = graph.ancestry(Invoice)
        assert chain == [Job, Branch]

    def test_ancestry_of_root(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        assert graph.ancestry(Branch) == []

    def test_roots(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        roots = graph.roots()
        assert Branch in roots
        assert Job not in roots
        assert Invoice not in roots

    def test_relationships_for(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        rels = graph.relationships_for(Job)
        names = [r.name for r in rels]
        assert "branch" in names
        assert "invoices" in names
        assert "messages" in names

    def test_belongs_to_for(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        rels = graph.belongs_to_for(Invoice)
        assert len(rels) == 1
        assert rels[0].target is Job

    def test_nesting_for(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        nesting = graph.nesting_for(Job)
        assert "invoices" in nesting
        assert "jobmessages" in nesting  # make_resource lowercases without underscores

    def test_get(self, populated_graph):
        graph, Branch, Job, Invoice, JobMessage = populated_graph
        assert graph.get("Job") is Job
        assert graph.get("Nonexistent") is None


# ---------------------------------------------------------------------------
# Tests: validation
# ---------------------------------------------------------------------------

class TestGraphValidation:
    def test_valid_graph_no_errors(self):
        Branch = make_resource("Branch", fields=["id"])
        Job = make_resource("Job",
            fields=["id", "branch_id"],
            relationships={"branch": belongs_to(Branch)},
        )
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()
        assert graph.validate() == []

    def test_missing_fk_field(self):
        Branch = make_resource("Branch", fields=["id"])
        Job = make_resource("Job",
            fields=["id"],  # missing branch_id!
            relationships={"branch": belongs_to(Branch)},
        )
        register(Branch, Job)

        graph = ResourceGraph()
        graph.build()
        errors = graph.validate()
        assert len(errors) == 1
        assert "branch_id" in errors[0]

    def test_unresolved_string_target(self):
        Job = make_resource("Job",
            fields=["id", "branch_id"],
            relationships={"branch": belongs_to("NonexistentResource")},
        )
        register(Job)

        graph = ResourceGraph()
        graph.build()
        # Unresolved target -> relationship not added (returns None from _resolve)
        rels = graph.relationships_for(Job)
        assert len(rels) == 0

    def test_has_many_missing_fk_on_target(self):
        Job = make_resource("Job",
            fields=["id"],
            relationships={"invoices": has_many("Invoice", key="job_id")},
        )
        Invoice = make_resource("Invoice",
            fields=["id"],  # missing job_id!
        )
        register(Job, Invoice)

        graph = ResourceGraph()
        graph.build()
        errors = graph.validate()
        assert len(errors) == 1
        assert "job_id" in errors[0]
        assert "Invoice" in errors[0]

    def test_references_valid(self):
        Thread = make_resource("Thread", fields=["id"])
        Job = make_resource("Job",
            fields=["id", "thread_id"],
            relationships={"thread": references(Thread)},
        )
        register(Thread, Job)

        graph = ResourceGraph()
        graph.build()
        assert graph.validate() == []

    def test_references_missing_fk(self):
        Thread = make_resource("Thread", fields=["id"])
        Job = make_resource("Job",
            fields=["id"],  # missing thread_id!
            relationships={"thread": references(Thread)},
        )
        register(Thread, Job)

        graph = ResourceGraph()
        graph.build()
        errors = graph.validate()
        assert len(errors) == 1
        assert "thread_id" in errors[0]


# ---------------------------------------------------------------------------
# Tests: references queries
# ---------------------------------------------------------------------------

class TestGraphReferences:
    @pytest.fixture
    def graph_with_refs(self):
        Thread = make_resource("Thread", fields=["id"])
        Message = make_resource("Message",
            fields=["id", "thread_id"],
            relationships={"thread": belongs_to(Thread)},
        )
        Job = make_resource("Job",
            fields=["id", "thread_id", "branch_id"],
            relationships={
                "thread": references(Thread),
                "branch": belongs_to("Branch"),
            },
        )
        Branch = make_resource("Branch", fields=["id"])
        register(Thread, Message, Job, Branch)

        graph = ResourceGraph()
        graph.build()
        return graph, Thread, Message, Job, Branch

    def test_references_for(self, graph_with_refs):
        graph, Thread, Message, Job, Branch = graph_with_refs
        refs = graph.references_for(Job)
        assert len(refs) == 1
        assert refs[0].target is Thread
        assert refs[0].key == "thread_id"

    def test_references_not_in_belongs_to(self, graph_with_refs):
        """references should not appear in belongs_to_for."""
        graph, Thread, Message, Job, Branch = graph_with_refs
        bt = graph.belongs_to_for(Job)
        targets = [r.target for r in bt]
        assert Thread not in targets
        assert Branch in targets

    def test_references_not_in_children(self, graph_with_refs):
        """A referenced resource is not a child."""
        graph, Thread, Message, Job, Branch = graph_with_refs
        children = graph.children_of(Thread)
        # Message belongs_to Thread, so it IS a child
        assert Message in children
        # Job references Thread, so it is NOT a child
        assert Job not in children

    def test_references_doesnt_affect_roots(self, graph_with_refs):
        """A resource with only references (no belongs_to) is still a root... but Job has belongs_to Branch."""
        graph, Thread, Message, Job, Branch = graph_with_refs
        roots = graph.roots()
        # Thread and Branch are roots (no belongs_to)
        assert Thread in roots
        assert Branch in roots
        # Job belongs_to Branch, not a root
        assert Job not in roots

    def test_references_with_mount(self):
        Thread = make_resource("Thread", fields=["id"])
        Message = make_resource("Message", fields=["id", "thread_id"])
        Reaction = make_resource("Reaction", fields=["id", "thread_id"])
        Job = make_resource("Job",
            fields=["id", "thread_id"],
            relationships={"thread": references(Thread, mount=[Message])},
        )
        register(Thread, Message, Reaction, Job)

        graph = ResourceGraph()
        graph.build()

        refs = graph.references_for(Job)
        assert refs[0].mount == [Message]
        assert Reaction not in refs[0].mount


# ---------------------------------------------------------------------------
# Tests: _has_field helper
# ---------------------------------------------------------------------------

class TestHasField:
    def test_annotation(self):
        class Foo:
            x: str
        assert _has_field(Foo, "x") is True

    def test_attribute(self):
        class Foo:
            x = "hello"
        assert _has_field(Foo, "x") is True

    def test_missing(self):
        class Foo:
            pass
        assert _has_field(Foo, "x") is False

    def test_inherited_annotation(self):
        class Base:
            x: str
        class Child(Base):
            pass
        assert _has_field(Child, "x") is True
