"""
RecordingAdapter — a channel adapter stub for tests.

Registers as channel_type "recording". Records every on_message() call
instead of delivering anywhere. Use in unit tests to verify that
ChannelDelivery dispatch logic calls adapters correctly.

Usage:
    from tests.channel_adapter_stub import RecordingAdapter

    adapter = RecordingAdapter.install()  # singleton — safe to call repeatedly
    adapter.clear()

    adapter.on_message(msg, {}, ctx)

    msgs = adapter.received(thread_id="thread-xyz")
    assert len(msgs) == 1
    assert msgs[0].role == "USER"
"""

from __future__ import annotations

from typing import Optional

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage
from ironbridge.platform.channels.registry import register_adapter
from services.channels.adapters.base import BaseChannelAdapter

_instance: Optional["RecordingAdapter"] = None


class RecordingAdapter(BaseChannelAdapter):
    """
    Channel adapter that records every on_message() call.
    Not thread-safe — intended for single-threaded unit tests only.
    """

    channel_type = "recording"

    def __init__(self) -> None:
        self._received: list[ChannelMessage] = []

    def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
        self._received.append(message)

    def received(self, thread_id: Optional[str] = None) -> list[ChannelMessage]:
        """Return recorded messages, optionally filtered by thread_id."""
        if thread_id is None:
            return list(self._received)
        return [m for m in self._received if m.thread_id == thread_id]

    def clear(self) -> None:
        """Reset recorded messages."""
        self._received.clear()

    @classmethod
    def install(cls) -> "RecordingAdapter":
        """
        Return the singleton RecordingAdapter, creating and registering it on first call.
        Subsequent calls return the same instance — registry entry is stable.
        Call clear() between tests.
        """
        global _instance
        if _instance is None:
            _instance = cls()
            register_adapter(_instance)
        return _instance
