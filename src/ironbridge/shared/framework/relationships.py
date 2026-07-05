"""
Relationship declarations for Resources.

Declared as class attributes. The metaclass collects them into __relationships__.
The ResourceGraph resolves string references at startup.

Four relationship types:

    belongs_to  - I have a FK to parent. I'm a child. Auto-nests under parent.
    has_many    - Child has a FK to me. Children auto-nest under me.
    has_one     - Like has_many but at most one.
    many_to_many - Related through a join resource.
    references  - I link to a shared resource. Neither owns the other.
                  Mounts the target's sub-resources under me, ACL through me.

    class MaintenanceJob(Workflow):
        branch     = belongs_to(Branch)                     # child of Branch
        contractor = belongs_to(Contractor, optional=True)  # optional parent
        invoices   = has_many(Invoice)                      # Invoice is my child
        thread     = references(Thread)                     # shared, access through me

Convention: belongs_to(Foo) infers key="foo_id".
Override with key= when the convention doesn't match.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


def _snake(name: str) -> str:
    """CamelCase -> snake_case."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _target_name(target: type | str) -> str:
    """Get the string name of a target."""
    if isinstance(target, str):
        return target
    return target.__name__


def _infer_belongs_to_key(target: type | str) -> str:
    """Infer FK field name from target: User -> user_id, Branch -> branch_id."""
    return f"{_snake(_target_name(target))}_id"


def _infer_has_key(source_name: str) -> str:
    """Infer FK field name on the target: MaintenanceJob -> maintenance_job_id."""
    return f"{_snake(source_name)}_id"


@dataclass(frozen=True)
class BelongsTo:
    """This resource has a FK pointing to the target."""
    target: type | str
    key: str
    optional: bool = False

    @property
    def kind(self) -> str:
        return "belongs_to"

    @property
    def target_name(self) -> str:
        return _target_name(self.target)


@dataclass(frozen=True)
class HasMany:
    """Target resource has a FK pointing back to this resource."""
    target: type | str
    key: str | None  # FK on the target; None = infer at graph build time

    @property
    def kind(self) -> str:
        return "has_many"

    @property
    def target_name(self) -> str:
        return _target_name(self.target)


@dataclass(frozen=True)
class HasOne:
    """Target resource has a FK pointing back, at most one."""
    target: type | str
    key: str | None

    @property
    def kind(self) -> str:
        return "has_one"

    @property
    def target_name(self) -> str:
        return _target_name(self.target)


@dataclass(frozen=True)
class ManyToMany:
    """Related through a join resource."""
    target: type | str
    through: type | str
    source_key: str | None  # FK on join pointing to this resource
    target_key: str | None  # FK on join pointing to target

    @property
    def kind(self) -> str:
        return "many_to_many"

    @property
    def target_name(self) -> str:
        return _target_name(self.target)

    @property
    def through_name(self) -> str:
        return _target_name(self.through)


@dataclass(frozen=True)
class References:
    """Link to a shared resource. Neither owns the other.

    This resource has a FK to the target, but it's not a parent-child
    relationship. The target is shared infrastructure (Thread, User, etc.)
    that multiple domains reference.

    Route effect: target's sub-resources (e.g. Message) are mounted under
    this resource, with ACL checked against this resource.

    Example:
        class MaintenanceJob(Workflow):
            thread = references(Thread)

        # Generates:
        # GET /maintenance/jobs/{job_id}/messages  (Thread's Messages, scoped)
    """
    target: type | str
    key: str
    mount: list[type | str] | None = None  # sub-resources of target to mount (None = all)

    @property
    def kind(self) -> str:
        return "references"

    @property
    def target_name(self) -> str:
        return _target_name(self.target)


# ---------------------------------------------------------------------------
# Constructors
# ---------------------------------------------------------------------------

def belongs_to(target: type | str, *, key: str | None = None, optional: bool = False) -> BelongsTo:
    """Declare a belongs_to relationship. Key inferred from target name if not provided."""
    resolved_key = key or _infer_belongs_to_key(target)
    return BelongsTo(target=target, key=resolved_key, optional=optional)


def has_many(target: type | str, *, key: str | None = None) -> HasMany:
    """Declare a has_many relationship. Key inferred at graph build time if not provided."""
    return HasMany(target=target, key=key)


def has_one(target: type | str, *, key: str | None = None) -> HasOne:
    """Declare a has_one relationship. Key inferred at graph build time if not provided."""
    return HasOne(target=target, key=key)


def many_to_many(
    target: type | str,
    *,
    through: type | str,
    source_key: str | None = None,
    target_key: str | None = None,
) -> ManyToMany:
    """Declare a many_to_many relationship through a join resource."""
    return ManyToMany(target=target, through=through, source_key=source_key, target_key=target_key)


def references(
    target: type | str,
    *,
    key: str | None = None,
    mount: list[type | str] | None = None,
) -> References:
    """Declare a reference to a shared resource.

    I have a FK to target, but target is not my parent and I'm not its child.
    Target's sub-resources get mounted under me with ACL through me.

        thread = references(Thread)                    # infers key="thread_id"
        thread = references(Thread, mount=[Message])   # only mount Messages
    """
    resolved_key = key or _infer_belongs_to_key(target)
    return References(target=target, key=resolved_key, mount=mount)
