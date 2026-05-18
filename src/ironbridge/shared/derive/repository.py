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


class SqlAlchemyRepository[R: Resource]:
    def __init__(self, session: Session, resource_cls: type[R]) -> None:
        self._session = session
        self._cls = resource_cls
        mapper = sa_inspect(resource_cls)
        self._pk = mapper.primary_key[0].name
        self._idempotent = resource_cls.__meta__.get("idempotent", False)

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

        if self._idempotent:
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
