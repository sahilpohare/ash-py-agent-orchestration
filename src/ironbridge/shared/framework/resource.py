from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import String
from sqlalchemy import text as sa_text
from sqlalchemy.orm import DeclarativeBase, mapped_column

from . import registry
from .actions import ActionMeta


def _as_tuple(key: str | tuple[str, ...]) -> tuple[str, ...]:
    return (key,) if isinstance(key, str) else tuple(key)


class Base(DeclarativeBase):
    pass


class ResourceMeta(type(Base)):
    """
    Extends SQLAlchemy's DeclarativeBase metaclass to also:
    - inject tenancy_key column for tenant_scoped resources
    - collect @action methods into cls.__actions__
    - parse cls.Meta into cls.__meta__
    - register the class in the global resource registry
    """

    def __new__(mcs, name: str, bases: tuple, namespace: dict) -> ResourceMeta:
        inherited: dict[str, ActionMeta] = {}
        for base in bases:
            if hasattr(base, "__actions__"):
                inherited.update(base.__actions__)

        # Build actions fresh: start from inherited, apply this class's namespace.
        # If a name appears in namespace without __action__, it shadows the parent —
        # exclude it. No mutation of inherited dict; each class gets its own copy.
        actions: dict[str, ActionMeta] = {k: v for k, v in inherited.items() if k not in namespace}
        for attr, value in namespace.items():
            if callable(value) and hasattr(value, "__action__"):
                actions[attr] = value.__action__

        namespace["__actions__"] = actions

        raw_meta = namespace.get("Meta")
        meta = {
            "tenant_scoped": False,
            "restate_object": False,
            "idempotent": False,
            "terminal_errors": (ValueError,),
            "tenancy_key": ("tenant_id",),  # always a tuple internally
        }
        if raw_meta:
            for key in ("tenant_scoped", "restate_object", "idempotent", "terminal_errors",
                        "conflict_columns", "conflict_action"):
                if hasattr(raw_meta, key):
                    meta[key] = getattr(raw_meta, key)
            if hasattr(raw_meta, "tenancy_key"):
                meta["tenancy_key"] = _as_tuple(raw_meta.tenancy_key)

        namespace["__meta__"] = meta

        # Inject all tenancy columns before SQLAlchemy processes the class.
        if meta["tenant_scoped"]:
            for col in meta["tenancy_key"]:
                if col not in namespace:
                    namespace[col] = mapped_column(
                        String,
                        nullable=False,
                        index=True,
                        server_default=sa_text("current_setting('app.tenant_id', true)"),
                    )

        cls = super().__new__(mcs, name, bases, namespace)

        # Fix 3: only skip if *this class* declared __abstract__ = True.
        # getattr would walk MRO and falsely inherit True from a mixin parent.
        is_abstract = bool(namespace.get("__abstract__"))
        if not is_abstract and name not in ("Resource", "Base"):
            registry.register(cls)

        return cls


class Resource(Base, metaclass=ResourceMeta):
    """
    Base for all domain resources. IS the SQLAlchemy model.

    Columns declared with Mapped[] + mapped_column() as normal SQLAlchemy.
    For tenant_scoped resources, the tenancy column (default: tenant_id) is
    injected automatically — do not declare it in the resource class.

    Meta options:
        tenant_scoped   bool   False    Injects tenancy column + RLS policy
        restate_object  bool   False    Derives a Restate VirtualObject
        idempotent      bool   False    ON CONFLICT DO NOTHING keyed by content hash
        terminal_errors tuple  (ValueError,)  Stop Restate retries, return 400
        tenancy_key     str    "tenant_id"    Name of the injected tenancy column

    Example:

        class Thread(Resource):
            class Meta:
                tenant_scoped = True
                restate_object = True

            __tablename__ = "threads"

            id         : Mapped[str]      = mapped_column(String, primary_key=True)
            created_at : Mapped[datetime] = mapped_column(DateTime(timezone=True))

            @action(kind=ActionKind.ACTION)
            def add_message(self, body: str) -> "Message": ...

        # Thread.tenant_id is available at runtime — injected by the framework.
        # No need to declare it, no need to pass it in action bodies.
    """

    __abstract__ = True
    __actions__: ClassVar[dict[str, ActionMeta]]
    __meta__: ClassVar[dict[str, Any]]
    # Fix 2: annotation lets type checkers see tenant_id on tenant_scoped subclasses.
    # SQLAlchemy ignores annotations on abstract models with no mapped_column backing.
    tenant_id: str | None
