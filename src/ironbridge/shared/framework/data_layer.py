"""
Data layer abstraction. Resources declare which data layer to use.
The derive layer picks the right repository implementation.

    class Job(Resource, Workflow):
        class Meta:
            data_layer = "postgres"   # default
            table = "jobs"

    class TestJob(Resource):
        class Meta:
            data_layer = "memory"     # for testing

Data layers:
    postgres  - SqlAlchemyRepository (default)
    memory    - dict-backed, no DB needed
"""
from __future__ import annotations

from typing import Any


class InMemoryRepository:
    """Dict-backed repository for testing. No DB needed."""

    # Class-level storage: {resource_class_name: {id: instance}}
    _stores: dict[str, dict[str, Any]] = {}

    def __init__(self, resource_cls: type) -> None:
        self._cls = resource_cls
        self._name = resource_cls.__name__
        if self._name not in self._stores:
            self._stores[self._name] = {}

    @property
    def _store(self) -> dict[str, Any]:
        if self._name not in self._stores:
            self._stores[self._name] = {}
        return self._stores[self._name]

    def find_by_id(self, id: str) -> Any | None:
        return self._store.get(id)

    def find_by(self, **kwargs: Any) -> Any | None:
        for instance in self._store.values():
            if all(getattr(instance, k, None) == v for k, v in kwargs.items()):
                return instance
        return None

    def list(self, **filters: Any) -> list:
        results = list(self._store.values())
        if filters:
            results = [
                r for r in results
                if all(getattr(r, k, None) == v for k, v in filters.items())
            ]
        return results

    def save(self, instance: Any) -> Any:
        id_val = getattr(instance, "id", None)
        if id_val is None:
            raise ValueError("Cannot save instance without id")
        self._store[id_val] = instance
        return instance

    def delete(self, id: str) -> None:
        self._store.pop(id, None)

    def count(self, **filters: Any) -> int:
        return len(self.list(**filters))

    def execute(self, sql: str, **params) -> None:
        """Not supported for in-memory. Raises."""
        raise RuntimeError("Raw SQL not supported on in-memory data layer")

    def paginate(
        self,
        page: int = 1,
        per_page: int = 25,
        sort: str | None = None,
        order: str = "asc",
        filters: dict[str, Any] | None = None,
    ) -> dict:
        """Paginated list with filtering and sorting."""
        items = list(self._store.values())

        # Filter
        if filters:
            for field, value in filters.items():
                if isinstance(value, dict):
                    for op, val in value.items():
                        if op == "gt":
                            items = [r for r in items if getattr(r, field, None) is not None and getattr(r, field) > val]
                        elif op == "lt":
                            items = [r for r in items if getattr(r, field, None) is not None and getattr(r, field) < val]
                        elif op == "in":
                            items = [r for r in items if getattr(r, field, None) in val]
                        elif op == "ne":
                            items = [r for r in items if getattr(r, field, None) != val]
                else:
                    items = [r for r in items if getattr(r, field, None) == value]

        total = len(items)

        # Sort
        if sort:
            reverse = order == "desc"
            items.sort(key=lambda r: getattr(r, sort, ""), reverse=reverse)

        # Paginate
        offset = (page - 1) * per_page
        page_items = items[offset:offset + per_page]
        pages = (total + per_page - 1) // per_page if per_page > 0 else 0

        return {
            "data": page_items,
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": pages,
                "has_next": page < pages,
                "has_prev": page > 1,
            },
        }

    @classmethod
    def clear_all(cls) -> None:
        """Clear all in-memory stores. Call between tests."""
        cls._stores.clear()

    @classmethod
    def clear(cls, resource_cls: type) -> None:
        """Clear store for a specific resource."""
        cls._stores.pop(resource_cls.__name__, None)


class DataLayer:
    """Base class for custom data layers. Implement the methods you need."""

    def find_by_id(self, id: str) -> Any:
        raise NotImplementedError

    def find_by(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    def list(self, **filters: Any) -> list:
        raise NotImplementedError

    def paginate(self, page: int = 1, per_page: int = 25, sort: str | None = None,
                 order: str = "asc", filters: dict | None = None) -> dict:
        raise NotImplementedError

    def save(self, instance: Any) -> Any:
        raise NotImplementedError

    def delete(self, id: str) -> None:
        raise NotImplementedError

    def execute(self, sql: str, **params) -> Any:
        raise NotImplementedError("Raw SQL not supported on this data layer")


def get_repo(cls: type, session: Any = None) -> Any:
    """Get the right repository for a resource based on its data_layer."""
    layer = getattr(cls, "__meta__", {}).get("data_layer", "postgres")

    # Custom data layer instance
    if isinstance(layer, DataLayer):
        return layer

    # Built-in string layers
    if layer == "memory":
        return InMemoryRepository(cls)

    if layer == "postgres":
        from ironbridge.shared.derive.repository import SqlAlchemyRepository
        if session is None:
            raise RuntimeError(
                f"{cls.__name__} uses data_layer='postgres' but no session provided"
            )
        return SqlAlchemyRepository(session, cls)

    raise ValueError(f"Unknown data_layer: {layer!r} on {cls.__name__}")
