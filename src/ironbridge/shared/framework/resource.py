from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import String
from sqlalchemy import text as sa_text
from sqlalchemy.orm import DeclarativeBase, mapped_column

from . import registry
from .actions import ActionMeta
from .defaults import inject_defaults
from .relationships import BelongsTo, HasMany, HasOne, ManyToMany, References


def _as_tuple(key: str | tuple[str, ...]) -> tuple[str, ...]:
    return (key,) if isinstance(key, str) else tuple(key)


_RELATIONSHIP_TYPES = (BelongsTo, HasMany, HasOne, ManyToMany, References)


class Base(DeclarativeBase):
    pass


class ResourceMeta(type(Base)):
    """
    Extends SQLAlchemy's DeclarativeBase metaclass to:
    - parse cls.Meta into cls.__meta__
    - inject default actions if requested
    - inject tenancy column for tenant_scoped resources
    - collect @action methods into cls.__actions__
    - collect relationship declarations into cls.__relationships__
    - register the class in the global resource registry
    """

    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> ResourceMeta:
        # --- Parse Meta first (needed by inject_defaults) ---
        raw_meta = namespace.get("Meta")
        meta = {
            "tenant_scoped": False,
            "restate_object": False,
            "idempotent": False,
            "default_actions": False,
            "terminal_errors": (ValueError,),
            "tenancy_key": ("tenant_id",),
        }
        if raw_meta:
            for key in ("tenant_scoped", "restate_object", "idempotent", "terminal_errors",
                        "conflict_columns", "conflict_action", "default_actions", "table",
                        "extensions", "data_layer", "filters"):
                if hasattr(raw_meta, key):
                    meta[key] = getattr(raw_meta, key)
            if hasattr(raw_meta, "tenancy_key"):
                meta["tenancy_key"] = _as_tuple(raw_meta.tenancy_key)

        namespace["__meta__"] = meta

        # --- Inject default actions BEFORE collecting ---
        if meta.get("default_actions"):
            inject_defaults(namespace, meta)

        # --- Inject tenancy columns ---
        if meta["tenant_scoped"]:
            for col in meta["tenancy_key"]:
                if col not in namespace:
                    namespace[col] = mapped_column(
                        String,
                        nullable=False,
                        index=True,
                        server_default=sa_text("current_setting('app.tenant_id', true)"),
                    )

        # --- NOW collect actions (inherited + this class + injected defaults) ---
        inherited_actions: dict[str, ActionMeta] = {}
        for base in bases:
            if hasattr(base, "__actions__"):
                inherited_actions.update(base.__actions__)

        actions: dict[str, ActionMeta] = {k: v for k, v in inherited_actions.items() if k not in namespace}
        for attr, value in namespace.items():
            if callable(value) and hasattr(value, "__action__"):
                actions[attr] = value.__action__

        namespace["__actions__"] = actions

        # --- Collect relationships (inherited + this class) ---
        inherited_rels: dict[str, Any] = {}
        for base in bases:
            if hasattr(base, "__relationships__"):
                inherited_rels.update(base.__relationships__)

        relationships: dict[str, Any] = {k: v for k, v in inherited_rels.items() if k not in namespace}
        for attr, value in namespace.items():
            if isinstance(value, _RELATIONSHIP_TYPES):
                relationships[attr] = value

        namespace["__relationships__"] = relationships

        cls = super().__new__(mcs, name, bases, namespace)

        is_abstract = bool(namespace.get("__abstract__"))
        if not is_abstract and name not in ("Resource", "Base"):
            registry.register(cls)

        return cls


class Resource(Base, metaclass=ResourceMeta):
    """
    Base for all domain resources. IS the SQLAlchemy model.

    Declare fields, relationships, actions, policies, guards.
    The framework derives persistence, routes, and enforcement.

    Meta options:
        tenant_scoped    bool    False    Injects tenancy column + RLS policy
        default_actions  varies  False    True, list of names, or False
        table            str     None     Override __tablename__
        extensions       list    []       Per-resource extensions
    """

    __abstract__ = True
    __actions__: ClassVar[dict[str, ActionMeta]]
    __relationships__: ClassVar[dict[str, Any]]
    __meta__: ClassVar[dict[str, Any]]
    tenant_id: str | None
