"""
BaseChannelAdapter — abstract base every channel implementation must satisfy.

Channel is a view of the thread. The adapter receives every thread message
and decides what to render. Inbound messages are posted via receive().

Registration:
    from ironbridge.platform.channels.registry import register_adapter
    register_adapter(MyAdapter())
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import httpx
from cuid2 import cuid_wrapper

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage

_cuid = cuid_wrapper()


class BaseChannelAdapter(ABC):
    channel_type: str  # must override — matches Channel.channel_type in DB

    @abstractmethod
    def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
        """
        Called by ChannelDelivery for every message added to the thread.

        message keys:
            thread_id        — the thread this message belongs to
            role             — USER | ASSISTANT | SYSTEM
            participant_id   — who sent it
            participant_type — HUMAN | AGENT | SYSTEM
            content          — {version, parts: [{type, ...}]}

        Part types:
            text             — plain text
            event            — AGENT_RUN_QUEUED/FAILED/ORPHANED/etc.
            response_request — HITL approval card
            response_reply   — HITL reply
            text_delta       — streaming chunk
            stream_end       — streaming complete

        config — channel.config from DB (credentials, settings).
        ctx    — ChannelContext for writing back to the thread.
        """
        ...

    def receive(
        self,
        content: dict,
        thread_id: str,
        tenant_id: str,
        participant_id: str,
        agent_id: str | None = None,
        idempotency_key: str | None = None,
        restate_url: str | None = None,
    ) -> None:
        """
        Post an inbound message to the thread via Restate ingress.
        HTTP to Restate ingress is the correct external boundary.
        """
        base = restate_url or os.environ.get("RESTATE_URL", "http://localhost:8080")
        ikey = idempotency_key or f"{thread_id}:{participant_id}:{_cuid()}"
        httpx.post(
            f"{base}/Thread/{thread_id}/add_message",
            json={
                "participant_id": participant_id,
                "participant_type": "HUMAN",
                "role": "USER",
                "content": content,
                "idempotency_key": ikey,
                "tenant_id": tenant_id,
                "user_name": participant_id,
                **({"agent_id": agent_id} if agent_id else {}),
            },
            timeout=10,
        )
