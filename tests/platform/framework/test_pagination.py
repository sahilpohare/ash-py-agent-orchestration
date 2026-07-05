"""Unit tests for pagination and filtering."""

import pytest
from dataclasses import dataclass

from ironbridge.shared.framework.data_layer import InMemoryRepository


@dataclass
class Job:
    id: str
    state: str = "opened"
    urgency: str = "routine"
    branch_id: str = "b-1"
    amount: float = 0.0


@pytest.fixture(autouse=True)
def clean():
    InMemoryRepository.clear_all()
    yield
    InMemoryRepository.clear_all()


def seed(repo, n=50):
    states = ["opened", "sourcing", "quote_approval", "booking", "closed"]
    urgencies = ["routine", "urgent", "emergency"]
    for i in range(n):
        repo.save(Job(
            id=f"j-{i}",
            state=states[i % len(states)],
            urgency=urgencies[i % len(urgencies)],
            branch_id=f"b-{i % 3}",
            amount=float(i * 10),
        ))


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

class TestPagination:
    def test_default_page(self):
        repo = InMemoryRepository(Job)
        seed(repo)
        result = repo.paginate()
        assert result["meta"]["page"] == 1
        assert result["meta"]["per_page"] == 25
        assert len(result["data"]) == 25
        assert result["meta"]["total"] == 50

    def test_second_page(self):
        repo = InMemoryRepository(Job)
        seed(repo)
        result = repo.paginate(page=2)
        assert result["meta"]["page"] == 2
        assert len(result["data"]) == 25

    def test_last_page_partial(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=30)
        result = repo.paginate(page=2, per_page=25)
        assert len(result["data"]) == 5
        assert result["meta"]["total"] == 30

    def test_page_beyond_range(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=10)
        result = repo.paginate(page=5, per_page=25)
        assert len(result["data"]) == 0

    def test_pages_count(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        result = repo.paginate(per_page=25)
        assert result["meta"]["pages"] == 2

    def test_pages_count_uneven(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=51)
        result = repo.paginate(per_page=25)
        assert result["meta"]["pages"] == 3

    def test_has_next(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        r1 = repo.paginate(page=1, per_page=25)
        r2 = repo.paginate(page=2, per_page=25)
        assert r1["meta"]["has_next"] is True
        assert r2["meta"]["has_next"] is False

    def test_has_prev(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        r1 = repo.paginate(page=1)
        r2 = repo.paginate(page=2)
        assert r1["meta"]["has_prev"] is False
        assert r2["meta"]["has_prev"] is True

    def test_custom_per_page(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=100)
        result = repo.paginate(per_page=10)
        assert len(result["data"]) == 10
        assert result["meta"]["pages"] == 10

    def test_empty(self):
        repo = InMemoryRepository(Job)
        result = repo.paginate()
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        assert result["meta"]["pages"] == 0


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSorting:
    def test_sort_asc(self):
        repo = InMemoryRepository(Job)
        repo.save(Job(id="j-c", amount=30))
        repo.save(Job(id="j-a", amount=10))
        repo.save(Job(id="j-b", amount=20))
        result = repo.paginate(sort="amount", order="asc")
        amounts = [j.amount for j in result["data"]]
        assert amounts == [10, 20, 30]

    def test_sort_desc(self):
        repo = InMemoryRepository(Job)
        repo.save(Job(id="j-c", amount=30))
        repo.save(Job(id="j-a", amount=10))
        repo.save(Job(id="j-b", amount=20))
        result = repo.paginate(sort="amount", order="desc")
        amounts = [j.amount for j in result["data"]]
        assert amounts == [30, 20, 10]

    def test_sort_by_string(self):
        repo = InMemoryRepository(Job)
        repo.save(Job(id="j-1", state="closed"))
        repo.save(Job(id="j-2", state="opened"))
        repo.save(Job(id="j-3", state="booking"))
        result = repo.paginate(sort="state", order="asc")
        states = [j.state for j in result["data"]]
        assert states == ["booking", "closed", "opened"]

    def test_sort_with_pagination(self):
        repo = InMemoryRepository(Job)
        for i in range(10):
            repo.save(Job(id=f"j-{i}", amount=float(9 - i)))
        result = repo.paginate(sort="amount", order="asc", per_page=3, page=1)
        amounts = [j.amount for j in result["data"]]
        assert amounts == [0.0, 1.0, 2.0]


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

class TestFiltering:
    def test_equality(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        result = repo.paginate(filters={"state": "opened"})
        assert all(j.state == "opened" for j in result["data"])
        assert result["meta"]["total"] == 10  # 50/5 states

    def test_multiple_filters(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        result = repo.paginate(filters={"state": "opened", "branch_id": "b-0"})
        for j in result["data"]:
            assert j.state == "opened"
            assert j.branch_id == "b-0"

    def test_gt_operator(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=10)
        result = repo.paginate(filters={"amount": {"gt": 50}})
        assert all(j.amount > 50 for j in result["data"])

    def test_lt_operator(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=10)
        result = repo.paginate(filters={"amount": {"lt": 30}})
        assert all(j.amount < 30 for j in result["data"])

    def test_in_operator(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        result = repo.paginate(filters={"state": {"in": ["opened", "closed"]}})
        assert all(j.state in ("opened", "closed") for j in result["data"])

    def test_ne_operator(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        result = repo.paginate(filters={"state": {"ne": "opened"}})
        assert all(j.state != "opened" for j in result["data"])

    def test_filter_with_sort_and_pagination(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=100)
        result = repo.paginate(
            filters={"state": "opened"},
            sort="amount",
            order="desc",
            page=1,
            per_page=5,
        )
        assert len(result["data"]) == 5
        assert all(j.state == "opened" for j in result["data"])
        amounts = [j.amount for j in result["data"]]
        assert amounts == sorted(amounts, reverse=True)

    def test_filter_no_match(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=10)
        result = repo.paginate(filters={"state": "nonexistent"})
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    def test_total_reflects_filter(self):
        repo = InMemoryRepository(Job)
        seed(repo, n=50)
        result = repo.paginate(filters={"branch_id": "b-0"})
        # 50 items, 3 branches, ~17 per branch
        assert result["meta"]["total"] < 50
        assert result["meta"]["total"] > 0


# ---------------------------------------------------------------------------
# PaginatedResult (SQLAlchemy repo)
# ---------------------------------------------------------------------------

class TestPaginatedResult:
    def test_to_dict(self):
        from ironbridge.shared.derive.repository import PaginatedResult
        result = PaginatedResult(data=["a", "b", "c"], page=1, per_page=10, total=3)
        d = result.to_dict()
        assert d["data"] == ["a", "b", "c"]
        assert d["meta"]["page"] == 1
        assert d["meta"]["total"] == 3
        assert d["meta"]["pages"] == 1

    def test_to_dict_with_serializer(self):
        from ironbridge.shared.derive.repository import PaginatedResult
        result = PaginatedResult(data=[1, 2, 3], page=1, per_page=10, total=3)
        d = result.to_dict(serialize_fn=lambda x: x * 2)
        assert d["data"] == [2, 4, 6]

    def test_pages_calculation(self):
        from ironbridge.shared.derive.repository import PaginatedResult
        assert PaginatedResult([], 1, 25, 0).pages == 0
        assert PaginatedResult([], 1, 25, 1).pages == 1
        assert PaginatedResult([], 1, 25, 25).pages == 1
        assert PaginatedResult([], 1, 25, 26).pages == 2
        assert PaginatedResult([], 1, 25, 50).pages == 2
        assert PaginatedResult([], 1, 25, 51).pages == 3

    def test_has_next_prev(self):
        from ironbridge.shared.derive.repository import PaginatedResult
        r = PaginatedResult([], 1, 25, 50)
        assert r.has_next is True
        assert r.has_prev is False

        r = PaginatedResult([], 2, 25, 50)
        assert r.has_next is False
        assert r.has_prev is True
