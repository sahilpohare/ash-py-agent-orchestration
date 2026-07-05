"""
Module -- groups resources under a URL prefix with lifecycle hooks.

    class MaintenanceModule(Module):
        prefix = "/maintenance"
        resources = [Job, Invoice]
        extensions = [AuditLog()]

        @classmethod
        def on_init(cls, providers):
            providers.register("contractors", ContractorRepo(providers.resolve("db")))

        @classmethod
        def on_ready(cls):
            print("Maintenance ready")

        @classmethod
        def on_shutdown(cls):
            pass

Lifecycle:
    1. on_init(providers)  - register module-specific dependencies
    2. extensions applied  - resource extensions resolved + applied
    3. routes mounted      - derive_router for each resource
    4. on_ready()          - all modules initialized, graph built
    5. on_shutdown()       - app shutting down
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from .depends import Providers
    from .extension import Extension
    from .graph import ResourceGraph


class Module:
    prefix: str = ""
    resources: list[type] | dict[type, str] = []
    modules: list[type[Module]] = []
    extensions: list[Extension] = []
    depends: dict[str, Any] = {}
    auth: Any = None

    # --- Lifecycle hooks (override in subclass) ---

    @classmethod
    def on_init(cls, providers: Providers) -> None:
        """Called at startup. Register module-specific providers."""
        pass

    @classmethod
    def on_ready(cls) -> None:
        """Called after all modules initialized, graph built, routes mounted."""
        pass

    @classmethod
    def on_shutdown(cls) -> None:
        """Called on app shutdown."""
        pass

    # --- Mount ---

    @classmethod
    def mount(
        cls,
        app: FastAPI,
        parent_prefix: str = "",
        graph: ResourceGraph | None = None,
        parent_extensions: list[Extension] | None = None,
    ) -> None:
        """Recursively mount all resources as FastAPI routes."""
        from ironbridge_web.derive.router import derive_router
        from .extension import resolve_extensions, apply_extensions

        full_prefix = parent_prefix + cls.prefix
        all_extensions = (parent_extensions or []) + cls.extensions

        if isinstance(cls.resources, dict):
            explicit_resources = cls.resources
        else:
            explicit_resources = {r: None for r in cls.resources}

        mounted: set[type] = set()
        for resource, custom_path in explicit_resources.items():
            resolved_exts = resolve_extensions(resource, all_extensions, graph)
            apply_extensions(resource, resolved_exts)
            router = derive_router(resource, prefix=custom_path)
            app.include_router(router, prefix=full_prefix)
            mounted.add(resource)

            if graph:
                _mount_children(app, resource, full_prefix, graph, all_extensions, mounted)

        for sub_module in cls.modules:
            sub_module.mount(app, full_prefix, graph, all_extensions)

    @classmethod
    def all_resources(cls) -> list[type]:
        if isinstance(cls.resources, dict):
            result = list(cls.resources.keys())
        else:
            result = list(cls.resources)
        for sub in cls.modules:
            result.extend(sub.all_resources())
        return result


def init_modules(
    modules: list[type[Module]],
    providers: Any = None,
) -> None:
    """Initialize all modules recursively. Calls on_init for each."""
    if providers is None:
        from .depends import get_providers
        providers = get_providers()

    for module in modules:
        module.on_init(providers)
        if module.modules:
            init_modules(module.modules, providers)


def ready_modules(modules: list[type[Module]]) -> None:
    """Notify all modules that startup is complete. Calls on_ready for each."""
    for module in modules:
        module.on_ready()
        if module.modules:
            ready_modules(module.modules)


def shutdown_modules(modules: list[type[Module]]) -> None:
    """Notify all modules of shutdown. Calls on_shutdown for each (reverse order)."""
    for module in reversed(modules):
        if module.modules:
            shutdown_modules(module.modules)
        module.on_shutdown()


def _mount_children(
    app: Any,
    parent: type,
    parent_prefix: str,
    graph: Any,
    extensions: list,
    mounted: set[type],
) -> None:
    from ironbridge_web.derive.router import derive_router
    from .extension import resolve_extensions, apply_extensions

    nesting = graph.nesting_for(parent)
    parent_table = getattr(parent, "__meta__", {}).get(
        "table",
        getattr(parent, "__tablename__", parent.__name__.lower() + "s"),
    )

    for child_table, (child_cls, fk_field) in nesting.items():
        if child_cls in mounted:
            continue
        resolved_exts = resolve_extensions(child_cls, extensions, graph)
        apply_extensions(child_cls, resolved_exts)
        nested_prefix = f"{parent_prefix}/{parent_table}/{{parent_id}}"
        router = derive_router(child_cls, prefix=None)
        app.include_router(router, prefix=nested_prefix)
        mounted.add(child_cls)
        _mount_children(app, child_cls, nested_prefix, graph, extensions, mounted)
