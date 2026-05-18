"""
ChannelContext — runtime context passed to every channel adapter on_message call.

Gives adapters the ability to write back to the thread — send messages,
trigger events — without knowing about Restate internals.

Constructed by ChannelDelivery before dispatching to the adapter.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cuid2 import cuid_wrapper

_cuid = cuid_wrapper()


class ChannelContext:
    """
    Runtime context for a channel adapter invocation.

    Exposes write primitives scoped to the current thread:
        send_message(text)          — write SYSTEM text message to thread
        send_event(event, **kwargs) — write SYSTEM event to thread
    """

    def __init__(self, restate_ctx: Any, thread_id: str, channel_id: str, tenant_id: str) -> None:
        self._ctx = restate_ctx
        self.thread_id = thread_id
        self.channel_id = channel_id
        self.tenant_id = tenant_id

    def send_message(self, text: str) -> None:
        """Fire-and-forget text message to the thread from the channel."""
        ikey = hashlib.sha256(f"{self.channel_id}:msg:{text[:64]}:{_cuid()}".encode()).hexdigest()[:16]
        self._ctx.generic_send(
            "Thread",
            "add_message",
            json.dumps({
                "participant_id": f"channel-{self.channel_id}",
                "participant_type": "SYSTEM",
                "role": "SYSTEM",
                "content": {"version": 1, "parts": [{"type": "text", "text": text}]},
                "idempotency_key": ikey,
                "tenant_id": self.tenant_id,
                "user_name": f"channel-{self.channel_id}",
            }).encode(),
            key=self.thread_id,
        )

    def send_event(self, event: str, **kwargs) -> None:
        """Fire-and-forget system event to the thread."""
        ikey = hashlib.sha256(f"{self.channel_id}:event:{event}:{_cuid()}".encode()).hexdigest()[:16]
        self._ctx.generic_send(
            "Thread",
            "add_message",
            json.dumps({
                "participant_id": f"channel-{self.channel_id}",
                "participant_type": "SYSTEM",
                "role": "SYSTEM",
                "content": {"version": 1, "parts": [{"type": "event", "event": event, **kwargs}]},
                "idempotency_key": ikey,
                "tenant_id": self.tenant_id,
                "user_name": f"channel-{self.channel_id}",
            }).encode(),
            key=self.thread_id,
        )
