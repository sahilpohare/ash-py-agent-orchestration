"""
Channel adapter registry.

Adapters register themselves on import — same pattern as agent_registry.
ChannelDelivery looks up adapters here by channel_type.
"""

from __future__ import annotations

from typing import Any

_adapters: dict[str, Any] = {}


def register_adapter(adapter: Any) -> None:
    _adapters[adapter.channel_type] = adapter


def get_adapter(channel_type: str) -> Any | None:
    return _adapters.get(channel_type)
