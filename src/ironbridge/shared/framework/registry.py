from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .resource import Resource

_registry: dict[str, type[Resource]] = {}


def register(resource_class: type[Resource]) -> None:
    _registry[resource_class.__name__] = resource_class


def get(name: str) -> type[Resource]:
    if name not in _registry:
        raise KeyError(f"Resource '{name}' not registered. Did you import it?")
    return _registry[name]


def all_resources() -> dict[str, type[Resource]]:
    return dict(_registry)
