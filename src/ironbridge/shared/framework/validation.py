"""
Configuration validation. Catches errors at class creation and graph build time.

Phase 1 (import time): metaclass / __init_subclass__ raises ConfigurationError
Phase 2 (CLI / startup): ironbridge validate or graph.build() checks cross-resource

    $ ironbridge validate

    Resources: 11 registered
    Relationships: 14 resolved
    Signals: 8 declared

    Errors (1):
      Job.branch: belongs_to("Branch") missing key=. Add key="branch_id"
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any


class ConfigurationError(Exception):
    """Raised at import time when a resource is misconfigured."""
    pass


@dataclass
class ValidationResult:
    errors: list[str]
    warnings: list[str]
    stats: dict[str, int]

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def print(self):
        print(f"\n  Resources:     {self.stats.get('resources', 0)} registered")
        print(f"  Relationships: {self.stats.get('relationships', 0)} resolved")
        print(f"  Signals:       {self.stats.get('signals', 0)} declared")
        print(f"  Actions:       {self.stats.get('actions', 0)} declared")
        print(f"  Routes:        {self.stats.get('routes', 0)} derived")

        if self.errors:
            print(f"\n  Errors ({len(self.errors)}):")
            for err in self.errors:
                print(f"    {err}")

        if self.warnings:
            print(f"\n  Warnings ({len(self.warnings)}):")
            for warn in self.warnings:
                print(f"    {warn}")

        if self.ok and not self.warnings:
            print(f"\n  All good.")


# ---------------------------------------------------------------------------
# Phase 1: import-time validation (single resource)
# ---------------------------------------------------------------------------

def validate_resource_at_import(cls: type) -> None:
    """Called by metaclass. Raises ConfigurationError on invalid config."""
    errors = []

    # belongs_to must have explicit key
    for rel_name, rel in getattr(cls, "__relationships__", {}).items():
        if hasattr(rel, "kind"):
            if rel.kind == "belongs_to" and not getattr(rel, "key", None):
                errors.append(
                    f"{cls.__name__}.{rel_name}: belongs_to requires key=. "
                    f"Use belongs_to(\"{rel.target_name}\", key=\"...\")"
                )
            if rel.kind in ("has_many", "has_one") and getattr(rel, "key", None) is None:
                errors.append(
                    f"{cls.__name__}.{rel_name}: {rel.kind} requires key=. "
                    f"Use {rel.kind}(\"{rel.target_name}\", key=\"...\")"
                )

    if errors:
        raise ConfigurationError(
            f"\n  Configuration errors in {cls.__name__}:\n" +
            "\n".join(f"    {e}" for e in errors)
        )


def validate_signals_at_import(cls: type) -> None:
    """Called by Workflow.__init_subclass__. Raises ConfigurationError on errors.
    Warnings are printed but don't block import."""
    errors = []
    warnings = []
    signals = getattr(cls, "__signals__", {})

    if not signals:
        return

    # Collect all signal names that are awaited via ctx.receive() in any handler
    awaited_signals = _find_awaited_signals(cls, signals)

    for signal_name, signal_def in signals.items():
        handler = getattr(signal_def, "_handler_fn", None)

        if handler is None:
            if signal_name not in awaited_signals:
                warnings.append(
                    f"{cls.__name__}.{signal_name}: Signal declared but never handled or awaited"
                )
            continue

        # Handler must be async
        if not asyncio.iscoroutinefunction(handler):
            errors.append(
                f"{cls.__name__}.{signal_name}: handler '{handler.__name__}' must be async"
            )

        # Handler must accept (self, ctx, ...)
        sig = inspect.signature(handler)
        params = list(sig.parameters.keys())
        if len(params) < 2:
            errors.append(
                f"{cls.__name__}.{signal_name}: handler '{handler.__name__}' "
                f"must accept (self, ctx, ...) but has {params}"
            )
        elif params[1] != "ctx":
            errors.append(
                f"{cls.__name__}.{signal_name}: handler '{handler.__name__}' "
                f"second parameter should be 'ctx', got '{params[1]}'"
            )

    # Route collisions: signal vs signal
    routes: dict[str, str] = {}
    for signal_name, signal_def in signals.items():
        route = signal_def.name
        if route in routes:
            errors.append(
                f"{cls.__name__}: route collision '/{route}' between "
                f"signal '{signal_name}' and '{routes[route]}'. "
                f"Use Signal(name=...) to disambiguate"
            )
        routes[route] = signal_name

    # Route collisions: signal vs action
    for action_name, action_meta in getattr(cls, "__actions__", {}).items():
        route = action_meta.name
        if route in routes:
            errors.append(
                f"{cls.__name__}: route collision '/{route}' between "
                f"action '{action_name}' and signal '{routes[route]}'"
            )

    # Note: CREATE signal is NOT required. Workflows can be started programmatically.
    # Signals are optional route generators.

    # Print warnings (don't block import)
    if warnings:
        import sys
        for w in warnings:
            print(f"  Warning: {w}", file=sys.stderr)

    # Raise on errors (block import)
    if errors:
        raise ConfigurationError(
            f"\n  Configuration errors in {cls.__name__}:\n" +
            "\n".join(f"    {e}" for e in errors)
        )


# ---------------------------------------------------------------------------
# Phase 2: graph / startup validation
# ---------------------------------------------------------------------------

def validate_full(graph: Any = None) -> ValidationResult:
    """
    Full validation. Run via CLI or at startup.
    Does NOT raise - returns ValidationResult.
    """
    from . import registry

    errors = []
    warnings = []
    all_resources = registry.all_resources()

    total_signals = 0
    total_actions = 0
    total_relationships = 0
    total_routes = 0

    for cls_name, cls in all_resources.items():
        actions = getattr(cls, "__actions__", {})
        signals = getattr(cls, "__signals__", {})
        relationships = getattr(cls, "__relationships__", {})

        total_actions += len(actions)
        total_signals += len(signals)
        total_relationships += len(relationships)
        total_routes += len(actions) + len(signals)

        # belongs_to FK field exists
        for rel_name, rel in relationships.items():
            if hasattr(rel, "kind") and rel.kind in ("belongs_to", "references"):
                if not _has_field(cls, rel.key):
                    errors.append(
                        f"{cls_name}.{rel_name}: FK field '{rel.key}' "
                        f"not found on {cls_name}"
                    )

        # Warnings: no filters on list
        if "list" in actions and not getattr(cls, "__meta__", {}).get("filters"):
            warnings.append(
                f"{cls_name}: has list action but no filters= declared in Meta"
            )

    # Graph-level validation
    graph_relationship_count = 0
    if graph:
        graph_relationship_count = len(graph.all_relationships())

        # Unresolved string references
        for cls_name, cls in all_resources.items():
            for rel_name, rel in getattr(cls, "__relationships__", {}).items():
                if isinstance(getattr(rel, "target", None), str):
                    if graph.get(rel.target) is None:
                        errors.append(
                            f"{cls_name}.{rel_name}: target '{rel.target}' "
                            f"not found in registry"
                        )

        # has_many/has_one FK field exists on target
        for resolved_rel in graph.all_relationships():
            if resolved_rel.kind in ("has_many", "has_one"):
                if resolved_rel.target and not _has_field(resolved_rel.target, resolved_rel.key):
                    errors.append(
                        f"{resolved_rel.source.__name__}.{resolved_rel.name}: "
                        f"FK field '{resolved_rel.key}' not found on "
                        f"target {resolved_rel.target.__name__}"
                    )

        # many_to_many through resource exists
        for resolved_rel in graph.all_relationships():
            if resolved_rel.kind == "many_to_many" and resolved_rel.through is None:
                errors.append(
                    f"{resolved_rel.source.__name__}.{resolved_rel.name}: "
                    f"through resource not found"
                )

    return ValidationResult(
        errors=errors,
        warnings=warnings,
        stats={
            "resources": len(all_resources),
            "relationships": graph_relationship_count or total_relationships,
            "signals": total_signals,
            "actions": total_actions,
            "routes": total_routes,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_awaited_signals(cls: type, signals: dict) -> set[str]:
    """Scan handler source code for ctx.receive("signal_name") references."""
    found = set()
    signal_names = set(signals.keys())

    # Check all handlers for ctx.receive references
    for signal_def in signals.values():
        handler = getattr(signal_def, "_handler_fn", None)
        if handler is None:
            continue
        try:
            source = inspect.getsource(handler)
            for name in signal_names:
                if f'"{name}"' in source or f"'{name}'" in source:
                    found.add(name)
        except (OSError, TypeError):
            # Can't get source (built-in, lambda, etc.)
            pass

    return found


def _has_field(cls: type, field_name: str) -> bool:
    for klass in cls.__mro__:
        if field_name in getattr(klass, "__annotations__", {}):
            return True
    return hasattr(cls, field_name)
