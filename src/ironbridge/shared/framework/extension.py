"""
Extension -- plugins that transform resources at registration time.

Extensions add fields, actions, policies, guards, and hooks to resources.
They're declared per-resource, per-module, or inherited through the graph.

    class MaintenanceJob(Workflow):
        class Meta:
            extensions = [
                TenantIsolation(key="branch_id"),
                SoftDelete(),
                AuditLog(actions=["approve_quote"]),
            ]

Built-in extensions handle core cross-cutting concerns. Custom extensions
follow the same pattern.
"""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .actor import Actor
    from .graph import ResourceGraph


class Extension:
    """Base class for resource extensions. Override the hooks you need."""

    # --- Startup hooks (once per resource) ---

    def on_resource(self, cls: type) -> None:
        """Transform the resource class. Add fields, policies, guards."""
        pass

    def on_action(self, cls: type, action_name: str, action_meta: Any) -> None:
        """Called for each action. Wrap, modify, or add policies."""
        pass

    def on_signal(self, cls: type, signal_name: str, signal_def: Any) -> None:
        """Called for each signal."""
        pass

    def on_route_derived(self, router: Any, cls: type) -> None:
        """Called after routes are derived. Add middleware, rate limits."""
        pass

    # --- Per-request hooks ---

    def before_action(self, actor: Actor, resource: Any, action_name: str, **kwargs: Any) -> None:
        """Called before every action. Raise to abort."""
        pass

    def after_action(self, actor: Actor, resource: Any, action_name: str, result: Any) -> None:
        """Called after every action completes."""
        pass

    def before_signal(self, actor: Actor | None, resource: Any, signal_name: str, payload: Any) -> None:
        """Called before every signal dispatch."""
        pass

    def after_signal(self, actor: Actor | None, resource: Any, signal_name: str, payload: Any) -> None:
        """Called after every signal dispatch."""
        pass

    # --- Identity (for dedup when inheriting through graph) ---

    @property
    def extension_type(self) -> str:
        """Used to dedup: same type on child overrides parent's."""
        return type(self).__name__


def resolve_extensions(
    cls: type,
    module_extensions: list[Extension] | None = None,
    graph: ResourceGraph | None = None,
) -> list[Extension]:
    """
    Resolve the full list of extensions for a resource.

    Merge order (later overrides earlier for same extension_type):
    1. Graph-inherited (walk belongs_to chain from furthest ancestor)
    2. Module-level
    3. Resource-level (from Meta.extensions)

    Same extension_type on a child overrides the parent's instance.
    Different types accumulate.
    """
    by_type: dict[str, Extension] = {}

    # 1. Graph-inherited (ancestors first, most distant first)
    if graph:
        ancestors = graph.ancestry(cls)
        for ancestor in reversed(ancestors):  # furthest ancestor first
            for ext in _get_extensions(ancestor):
                by_type[ext.extension_type] = ext

    # 2. Module-level
    if module_extensions:
        for ext in module_extensions:
            by_type[ext.extension_type] = ext

    # 3. Resource-level (highest priority)
    for ext in _get_extensions(cls):
        by_type[ext.extension_type] = ext

    return list(by_type.values())


def apply_extensions(cls: type, extensions: list[Extension]) -> None:
    """
    Apply extensions to a resource class. Run on_resource and on_action hooks.
    Called once at startup after resolve_extensions.
    """
    # Store resolved extensions on the class for per-request hooks
    cls.__extensions__ = extensions

    for ext in extensions:
        ext.on_resource(cls)

    for ext in extensions:
        for action_name, action_meta in getattr(cls, "__actions__", {}).items():
            ext.on_action(cls, action_name, action_meta)

    for ext in extensions:
        for signal_name, signal_def in getattr(cls, "__signals__", {}).items():
            ext.on_signal(cls, signal_name, signal_def)


def run_before_action(resource: Any, actor: Any, action_name: str, **kwargs: Any) -> None:
    """Run all extension before_action hooks for a resource instance."""
    for ext in getattr(type(resource), "__extensions__", []):
        ext.before_action(actor, resource, action_name, **kwargs)


def run_after_action(resource: Any, actor: Any, action_name: str, result: Any) -> None:
    """Run all extension after_action hooks for a resource instance."""
    for ext in getattr(type(resource), "__extensions__", []):
        ext.after_action(actor, resource, action_name, result)


def run_before_signal(resource: Any, actor: Any, signal_name: str, payload: Any) -> None:
    """Run all extension before_signal hooks."""
    for ext in getattr(type(resource), "__extensions__", []):
        ext.before_signal(actor, resource, signal_name, payload)


def run_after_signal(resource: Any, actor: Any, signal_name: str, payload: Any) -> None:
    """Run all extension after_signal hooks."""
    for ext in getattr(type(resource), "__extensions__", []):
        ext.after_signal(actor, resource, signal_name, payload)


def _get_extensions(cls: type) -> list[Extension]:
    """Get extensions declared on a resource's Meta."""
    return getattr(cls, "__meta__", {}).get("extensions", [])
