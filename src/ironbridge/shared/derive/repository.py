"""
Generic SQLAlchemy repository. Works directly with Resource subclasses
(which ARE SQLAlchemy models). All writes are upserts — safe for Restate replay.

Idempotency (Meta.idempotent = True):
  The framework adds a hidden _idempotency_key column and uses
  ON CONFLICT (_idempotency_key) DO NOTHING. The key is derived from a
  SHA-256 hash of the row's non-pk, non-timestamp column values — the
  caller never sees or supplies it.

Non-idempotent resources:
  ON CONFLICT (pk) DO UPDATE — last write wins, safe for Restate replay.
"""
from __future__ import annotations

import builtins
import hashlib
import json
from typing import Any, TypeVar

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ironbridge.shared.framework.resource import Resource

R = TypeVar("R", bound=Resource)

_IDEMPOTENCY_COL = "_idempotency_key"


class PaginatedResult[R]:
    """Paginated query result."""
    def __init__(self, data: list[R], page: int, per_page: int, total: int):
        self.data = data
        self.page = page
        self.per_page = per_page
        self.total = total

    @property
    def pages(self) -> int:
        if self.per_page == 0:
            return 0
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    def to_dict(self, serialize_fn=None) -> dict:
        items = [serialize_fn(item) if serialize_fn else item for item in self.data]
        return {
            "data": items,
            "meta": {
                "page": self.page,
                "per_page": self.per_page,
                "total": self.total,
                "pages": self.pages,
                "has_next": self.has_next,
                "has_prev": self.has_prev,
            },
        }


class SqlAlchemyRepository[R: Resource]:
    def __init__(self, session: Session, resource_cls: type[R]) -> None:
        self._session = session
        self._cls = resource_cls
        mapper = sa_inspect(resource_cls)
        self._pk = mapper.primary_key[0].name
        self._idempotent = resource_cls.__meta__.get("idempotent", False)

    @property
    def session(self):
        """Raw SQLAlchemy session for complex queries."""
        return self._session

    def execute(self, sql: str, **params) -> Any:
        """Execute raw SQL. Returns the result proxy."""
        from sqlalchemy import text
        return self._session.execute(text(sql), params)

    # ── Queries ──────────────────────────────────────────────────────────────

    def find_by_id(self, id: str) -> R | None:
        return self._session.get(self._cls, id)

    def find_by(self, **kwargs: Any) -> R | None:
        return self._session.query(self._cls).filter_by(**kwargs).first()

    def list(self, **filters: Any) -> builtins.list[R]:
        q = self._session.query(self._cls)
        if filters:
            q = q.filter_by(**filters)
        return q.all()

    def paginate(
        self,
        page: int = 1,
        per_page: int = 25,
        sort: str | None = None,
        order: str = "asc",
        filters: dict[str, Any] | None = None,
        query_modifier: Any = None,
    ) -> "PaginatedResult[R]":
        q = self._session.query(self._cls)

        # Apply read policy filter
        if query_modifier is not None:
            q = query_modifier(q)

        # Apply filters
        if filters:
            from sqlalchemy import and_, or_
            for field, value in filters.items():
                col = getattr(self._cls, field, None)
                if col is None:
                    continue
                if isinstance(value, dict):
                    # Operator-based: {"gt": 5, "lt": 10, "in": [...], "like": "%foo%"}
                    for op, val in value.items():
                        if op == "gt":
                            q = q.filter(col > val)
                        elif op == "gte":
                            q = q.filter(col >= val)
                        elif op == "lt":
                            q = q.filter(col < val)
                        elif op == "lte":
                            q = q.filter(col <= val)
                        elif op == "in":
                            q = q.filter(col.in_(val))
                        elif op == "not_in":
                            q = q.filter(~col.in_(val))
                        elif op == "like":
                            q = q.filter(col.like(val))
                        elif op == "ilike":
                            q = q.filter(col.ilike(val))
                        elif op == "ne":
                            q = q.filter(col != val)
                        elif op == "is_null":
                            q = q.filter(col.is_(None) if val else col.isnot(None))
                else:
                    q = q.filter(col == value)

        # Count before pagination
        total = q.count()

        # Sort
        if sort:
            col = getattr(self._cls, sort, None)
            if col is not None:
                if order == "desc":
                    q = q.order_by(col.desc())
                else:
                    q = q.order_by(col.asc())

        # Paginate
        offset = (page - 1) * per_page
        items = q.offset(offset).limit(per_page).all()

        return PaginatedResult(
            data=items,
            page=page,
            per_page=per_page,
            total=total,
        )

    # ── Writes ───────────────────────────────────────────────────────────────

    def save(self, instance: R) -> R:
        mapper = sa_inspect(type(instance))
        values = {}
        for col in mapper.mapper.column_attrs:
            val = getattr(instance, col.key)
            # Fire SQLAlchemy column defaults for None values (e.g. created_at, updated_at)
            if val is None:
                col_obj = mapper.mapper.columns.get(col.key)
                if col_obj is not None and col_obj.default is not None:
                    d = col_obj.default
                    if d.is_callable:
                        val = d.arg(None)
                    elif d.is_scalar:
                        val = d.arg
            values[col.key] = val
        # Populate tenancy columns from session GUCs — domain never touches them.
        # Single:    tenancy_key = ("tenant_id",)       → app.tenant_id
        # Composite: tenancy_key = ("tenant_id","org_id") → app.tenant_id, app.org_id
        meta = type(instance).__meta__
        if meta.get("tenant_scoped"):
            from sqlalchemy import text
            for col in meta["tenancy_key"]:
                if not values.get(col):
                    values[col] = self._session.execute(
                        text(f"SELECT current_setting('app.{col}', true)")
                    ).scalar()

        conflict_cols = meta.get("conflict_columns")
        conflict_action = meta.get("conflict_action", "update")

        if conflict_cols and conflict_action == "nothing":
            stmt = (
                pg_insert(type(instance))
                .values(**values)
                .on_conflict_do_nothing(index_elements=list(conflict_cols))
            )
        elif self._idempotent:
            values[_IDEMPOTENCY_COL] = _content_key(values, self._pk)
            stmt = (
                pg_insert(type(instance))
                .values(**values)
                .on_conflict_do_nothing(index_elements=[_IDEMPOTENCY_COL])
            )
        else:
            stmt = (
                pg_insert(type(instance))
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[self._pk],
                    set_={k: v for k, v in values.items() if k != self._pk},
                )
            )

        self._session.execute(stmt)
        # Write computed values back onto instance so callers see correct state
        for k, v in values.items():
            if k != _IDEMPOTENCY_COL:
                setattr(instance, k, v)
        return instance

    def delete(self, id: str) -> None:
        row = self._session.get(self._cls, id)
        if row:
            self._session.delete(row)


# ── Helpers ───────────────────────────────────────────────────────────────────

_SKIP_IN_HASH = {"id", "created_at", "updated_at", "_idempotency_key"}


def _content_key(values: dict[str, Any], pk: str) -> str:
    """
    Derive a stable idempotency key from the row's meaningful content.
    Excludes pk, timestamps, and the key column itself.
    The same logical message always produces the same key — duplicate
    inserts silently do nothing at the DB layer.
    """
    stable = {
        k: v.value if hasattr(v, "value") else v
        for k, v in values.items()
        if k not in _SKIP_IN_HASH and k != pk
    }
    payload = json.dumps(stable, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
