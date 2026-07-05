"""Unit tests for data layer abstraction."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.data_layer import InMemoryRepository, get_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeJob:
    id: str
    state: str = "opened"
    branch_id: str = "b-1"
    description: str = ""


def make_resource_cls(name, data_layer="postgres"):
    return type(name, (), {
        "__name__": name,
        "__meta__": {"data_layer": data_layer},
    })


@pytest.fixture(autouse=True)
def clean_stores():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()


# ---------------------------------------------------------------------------
# InMemoryRepository
# ---------------------------------------------------------------------------

class TestInMemoryRepo:
    def test_save_and_find(self):
        repo = InMemoryRepository(FakeJob)
        job = FakeJob(id="j-1", state="opened")
        repo.save(job)
        found = repo.find_by_id("j-1")
        assert found is job

    def test_find_missing(self):
        repo = InMemoryRepository(FakeJob)
        assert repo.find_by_id("nonexistent") is None

    def test_find_by(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1", state="opened", branch_id="b-1"))
        repo.save(FakeJob(id="j-2", state="closed", branch_id="b-1"))

        found = repo.find_by(state="closed")
        assert found.id == "j-2"

    def test_find_by_no_match(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1", state="opened"))
        assert repo.find_by(state="nonexistent") is None

    def test_list_all(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1"))
        repo.save(FakeJob(id="j-2"))
        assert len(repo.list()) == 2

    def test_list_filtered(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1", state="opened"))
        repo.save(FakeJob(id="j-2", state="closed"))
        repo.save(FakeJob(id="j-3", state="opened"))

        opened = repo.list(state="opened")
        assert len(opened) == 2

    def test_list_multiple_filters(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1", state="opened", branch_id="b-1"))
        repo.save(FakeJob(id="j-2", state="opened", branch_id="b-2"))

        result = repo.list(state="opened", branch_id="b-1")
        assert len(result) == 1
        assert result[0].id == "j-1"

    def test_list_empty(self):
        repo = InMemoryRepository(FakeJob)
        assert repo.list() == []

    def test_delete(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1"))
        repo.delete("j-1")
        assert repo.find_by_id("j-1") is None

    def test_delete_missing(self):
        repo = InMemoryRepository(FakeJob)
        repo.delete("nonexistent")  # should not raise

    def test_count(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1", state="opened"))
        repo.save(FakeJob(id="j-2", state="closed"))
        repo.save(FakeJob(id="j-3", state="opened"))

        assert repo.count() == 3
        assert repo.count(state="opened") == 2

    def test_save_updates(self):
        repo = InMemoryRepository(FakeJob)
        job = FakeJob(id="j-1", state="opened")
        repo.save(job)
        job.state = "closed"
        repo.save(job)
        found = repo.find_by_id("j-1")
        assert found.state == "closed"

    def test_save_without_id_raises(self):
        repo = InMemoryRepository(FakeJob)
        job = FakeJob(id=None)
        with pytest.raises(ValueError, match="without id"):
            repo.save(job)

    def test_clear(self):
        repo = InMemoryRepository(FakeJob)
        repo.save(FakeJob(id="j-1"))
        InMemoryRepository.clear(FakeJob)
        assert repo.list() == []

    def test_clear_all(self):
        repo1 = InMemoryRepository(FakeJob)
        repo1.save(FakeJob(id="j-1"))

        @dataclass
        class Other:
            id: str

        repo2 = InMemoryRepository(Other)
        repo2.save(Other(id="o-1"))

        InMemoryRepository.clear_all()
        assert repo1.list() == []
        assert repo2.list() == []

    def test_isolation_between_classes(self):
        @dataclass
        class TypeA:
            id: str

        @dataclass
        class TypeB:
            id: str

        repo_a = InMemoryRepository(TypeA)
        repo_b = InMemoryRepository(TypeB)

        repo_a.save(TypeA(id="a-1"))
        repo_b.save(TypeB(id="b-1"))

        assert len(repo_a.list()) == 1
        assert len(repo_b.list()) == 1
        assert repo_a.find_by_id("b-1") is None


# ---------------------------------------------------------------------------
# get_repo
# ---------------------------------------------------------------------------

class TestGetRepo:
    def test_memory_layer(self):
        cls = make_resource_cls("TestRes", data_layer="memory")
        repo = get_repo(cls)
        assert isinstance(repo, InMemoryRepository)

    def test_postgres_without_session_raises(self):
        cls = make_resource_cls("TestRes", data_layer="postgres")
        with pytest.raises(RuntimeError, match="no session"):
            get_repo(cls)

    def test_unknown_layer_raises(self):
        cls = make_resource_cls("TestRes", data_layer="redis")
        with pytest.raises(ValueError, match="Unknown data_layer"):
            get_repo(cls)

    def test_default_is_postgres(self):
        cls = type("TestRes", (), {"__name__": "TestRes", "__meta__": {}})
        with pytest.raises(RuntimeError, match="no session"):
            get_repo(cls)  # defaults to postgres, which needs session


# ---------------------------------------------------------------------------
# WorkflowContext.repo()
# ---------------------------------------------------------------------------

class TestContextRepo:
    def test_repo_via_ctx(self):
        from ironbridge.shared.framework.workflow import WorkflowContext
        from ironbridge.shared.framework.actor import from_request

        @dataclass
        class Item:
            id: str
            name: str = ""

        Item.__meta__ = {"data_layer": "memory"}

        def repo_factory(cls):
            return InMemoryRepository(cls)

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            repo_fn=repo_factory,
        )

        # Save via repo
        repo = ctx.repo(Item)
        repo.save(Item(id="i-1", name="test"))

        # Find via repo
        found = ctx.repo(Item).find_by_id("i-1")
        assert found.name == "test"

    def test_repo_fallback_to_data_layer(self):
        from ironbridge.shared.framework.workflow import WorkflowContext
        from ironbridge.shared.framework.actor import from_request

        @dataclass
        class MemItem:
            id: str
            val: str = ""

        MemItem.__meta__ = {"data_layer": "memory"}
        MemItem.__name__ = "MemItem"

        ctx = WorkflowContext(
            actor=from_request("u-1", "t-1", "admin"),
            resource=None,
            # no repo_fn - falls back to get_repo
        )

        repo = ctx.repo(MemItem)
        assert isinstance(repo, InMemoryRepository)
