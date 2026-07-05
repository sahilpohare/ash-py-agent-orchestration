"""
ResourceGraph -- the relationship graph of all registered resources.

Built at startup from Meta.relationships and relationship class attributes.
Resolves string references, validates FK fields, enables auto-nesting.

    graph = ResourceGraph()
    graph.build()
    errors = graph.validate()

    graph.children_of(MaintenanceJob)   # [Invoice, JobMessage]
    graph.parent_of(Invoice)            # MaintenanceJob
    graph.ancestry(Invoice)             # [MaintenanceJob, Branch]
    graph.roots()                       # [Branch, ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .relationships import BelongsTo, HasMany, HasOne, ManyToMany, References, _infer_has_key


@dataclass(frozen=True)
class ResolvedRelationship:
    """A relationship with both sides resolved to actual classes."""
    name: str           # attribute name on the source (e.g. "branch", "invoices")
    source: type        # the resource that declared it
    target: type        # resolved target class
    kind: str           # belongs_to, has_many, has_one, many_to_many, references
    key: str            # FK field
    optional: bool = False
    through: type | None = None   # for many_to_many
    mount: list[type] | None = None  # for references: which sub-resources to mount


class ResourceGraph:
    def __init__(self) -> None:
        self._resources: dict[str, type] = {}
        self._relationships: list[ResolvedRelationship] = []
        self._built = False

    def build(self) -> None:
        """Build the graph from all registered resources."""
        from . import registry

        self._resources = dict(registry.all_resources())
        self._relationships = []

        for cls_name, cls in self._resources.items():
            rels = getattr(cls, "__relationships__", {})
            for rel_name, rel in rels.items():
                resolved = self._resolve(cls, rel_name, rel)
                if resolved:
                    self._relationships.append(resolved)

        self._built = True

    def _resolve(self, source: type, name: str, rel: Any) -> ResolvedRelationship | None:
        """Resolve a relationship declaration to concrete classes."""
        target = self._resolve_target(rel.target)
        if target is None:
            return None

        if isinstance(rel, BelongsTo):
            return ResolvedRelationship(
                name=name, source=source, target=target,
                kind="belongs_to", key=rel.key, optional=rel.optional,
            )

        if isinstance(rel, HasMany):
            key = rel.key or _infer_has_key(source.__name__)
            return ResolvedRelationship(
                name=name, source=source, target=target,
                kind="has_many", key=key,
            )

        if isinstance(rel, HasOne):
            key = rel.key or _infer_has_key(source.__name__)
            return ResolvedRelationship(
                name=name, source=source, target=target,
                kind="has_one", key=key,
            )

        if isinstance(rel, ManyToMany):
            through = self._resolve_target(rel.through)
            if through is None:
                return None
            source_key = rel.source_key or _infer_has_key(source.__name__)
            target_key = rel.target_key or _infer_has_key(target.__name__)
            return ResolvedRelationship(
                name=name, source=source, target=target,
                kind="many_to_many", key=source_key,
                through=through,
            )

        if isinstance(rel, References):
            mount = None
            if rel.mount:
                mount = [self._resolve_target(m) for m in rel.mount]
                mount = [m for m in mount if m is not None]
            return ResolvedRelationship(
                name=name, source=source, target=target,
                kind="references", key=rel.key,
                mount=mount,
            )

        return None

    def _resolve_target(self, target: type | str) -> type | None:
        if isinstance(target, type):
            return target
        return self._resources.get(target)

    # --- Queries ---

    def children_of(self, parent: type) -> list[type]:
        """Resources that belong_to this parent."""
        return list({
            r.source for r in self._relationships
            if r.target is parent and r.kind == "belongs_to"
        })

    def parent_of(self, child: type) -> type | None:
        """First belongs_to target of this resource."""
        for r in self._relationships:
            if r.source is child and r.kind == "belongs_to":
                return r.target
        return None

    def parents_of(self, child: type) -> list[type]:
        """All belongs_to targets of this resource."""
        return [r.target for r in self._relationships
                if r.source is child and r.kind == "belongs_to"]

    def ancestry(self, cls: type) -> list[type]:
        """Walk up the belongs_to chain. Returns [parent, grandparent, ...]."""
        chain: list[type] = []
        visited: set[type] = set()
        current = cls
        while current and current not in visited:
            visited.add(current)
            parent = self.parent_of(current)
            if parent:
                chain.append(parent)
            current = parent
        return chain

    def roots(self) -> list[type]:
        """Resources with no belongs_to."""
        has_parent = {r.source for r in self._relationships if r.kind == "belongs_to"}
        return [cls for cls in self._resources.values() if cls not in has_parent]

    def relationships_for(self, cls: type) -> list[ResolvedRelationship]:
        """All relationships declared on this resource."""
        return [r for r in self._relationships if r.source is cls]

    def has_many_for(self, cls: type) -> list[ResolvedRelationship]:
        """has_many and has_one relationships on this resource."""
        return [r for r in self._relationships
                if r.source is cls and r.kind in ("has_many", "has_one")]

    def belongs_to_for(self, cls: type) -> list[ResolvedRelationship]:
        """belongs_to relationships on this resource."""
        return [r for r in self._relationships
                if r.source is cls and r.kind == "belongs_to"]

    def references_for(self, cls: type) -> list[ResolvedRelationship]:
        """references relationships on this resource."""
        return [r for r in self._relationships
                if r.source is cls and r.kind == "references"]

    def nesting_for(self, parent: type) -> dict[str, tuple[type, str]]:
        """Children that should be nested under this parent's routes.
        Returns {child_table_name: (child_cls, fk_field)}."""
        result = {}
        for r in self._relationships:
            if r.source is parent and r.kind in ("has_many", "has_one"):
                table = getattr(r.target, "__meta__", {}).get("table", r.target.__name__.lower() + "s")
                result[table] = (r.target, r.key)
        return result

    def get(self, name: str) -> type | None:
        """Get a resource by name."""
        return self._resources.get(name)

    def all(self) -> dict[str, type]:
        return dict(self._resources)

    def all_relationships(self) -> list[ResolvedRelationship]:
        return list(self._relationships)

    # --- Validation ---

    def validate(self) -> list[str]:
        """Check for problems. Run at startup."""
        errors: list[str] = []

        for r in self._relationships:
            # Check target was resolved
            if r.target is None:
                errors.append(
                    f"{r.source.__name__}.{r.name}: target not found in registry"
                )

            # Check FK field exists on the right side
            if r.kind in ("belongs_to", "references"):
                if not _has_field(r.source, r.key):
                    errors.append(
                        f"{r.source.__name__}.{r.name}: FK field '{r.key}' "
                        f"not found on {r.source.__name__}"
                    )
            elif r.kind in ("has_many", "has_one"):
                if r.target and not _has_field(r.target, r.key):
                    errors.append(
                        f"{r.source.__name__}.{r.name}: FK field '{r.key}' "
                        f"not found on target {r.target.__name__}"
                    )

            # Check many_to_many through resource
            if r.kind == "many_to_many" and r.through is None:
                errors.append(
                    f"{r.source.__name__}.{r.name}: through resource not found"
                )

        return errors


def _has_field(cls: type, field_name: str) -> bool:
    """Check if a class has a field (annotation or attribute)."""
    # Check annotations
    for klass in cls.__mro__:
        if field_name in getattr(klass, "__annotations__", {}):
            return True
    # Check class attributes
    if hasattr(cls, field_name):
        return True
    return False
