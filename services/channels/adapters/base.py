"""
BaseChannelAdapter — abstract base every channel adapter must satisfy.

Implementing an adapter requires only:
  1. Set channel_type = "myservice"
  2. Implement on_message(message, config, ctx) for outbound delivery
  3. Use self.receive() for inbound messages
  4. Use self.get_or_create_thread() to find-or-create a thread for an
     external conversation (bot adapters)

Everything else — HTTP transport, idempotency keys, JSON shapes, thread
binding — is handled by the base class. Adapters never touch Restate internals.

Bot processes (discord, telegram, etc.) are external — they communicate
with the Ironbridge app via HTTP to Restate ingress. This is correct and
intentional: bots run outside the app process.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod

import httpx
from cuid2 import cuid_wrapper

from ironbridge.platform.channels.channel import Channel
from ironbridge.platform.channels.channel_binding import ChannelBinding
from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage
from ironbridge.shared.db import tenant_session
from ironbridge.shared.derive.repository import SqlAlchemyRepository

_cuid = cuid_wrapper()


class BaseChannelAdapter(ABC):
    channel_type: str  # must override — matches Channel.channel_type in DB

    # ── Adapter interface ─────────────────────────────────────────────────────

    @abstractmethod
    def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
        """
        Called by ChannelDelivery for every message added to the thread.
        Adapter decides what to render and what to ignore.

        message.role        — USER | ASSISTANT | SYSTEM
        message.parts       — list of typed parts (TextPart, EventPart, etc.)
        message.thread_id   — originating thread
        config              — channel.config from DB (credentials, settings)
        ctx                 — write back to thread (ctx.send_message, ctx.send_event)
        """
        ...

    # ── Inbound ───────────────────────────────────────────────────────────────

    def receive(
        self,
        thread_id: str,
        tenant_id: str,
        participant_id: str,
        text: str | None = None,
        content: dict | None = None,
        agent_id: str | None = None,
        idempotency_key: str | None = None,
        restate_url: str | None = None,
    ) -> None:
        """
        Post an inbound message to the thread via Restate ingress.

        Provide either text (plain string) or content (raw content dict).
        If both are provided, content takes precedence.
        """
        body = content or {"version": 1, "parts": [{"type": "text", "text": text or ""}]}
        ikey = idempotency_key or hashlib.sha256(
            f"{thread_id}:{participant_id}:{str(body)[:128]}".encode()
        ).hexdigest()[:16]
        self._post(
            f"/Thread/{thread_id}/add_message",
            {
                "participant_id": participant_id,
                "participant_type": "HUMAN",
                "role": "USER",
                "content": body,
                "idempotency_key": ikey,
                "tenant_id": tenant_id,
                "user_name": participant_id,
                **({"agent_id": agent_id} if agent_id else {}),
            },
            restate_url=restate_url,
        )

    # ── Thread management (in-app adapters) ───────────────────────────────────

    def get_or_create_channel(self, tenant_id: str) -> str:
        """Return the channel_id for this adapter's channel_type, creating it if needed."""
        with tenant_session(tenant_id) as db:
            repo = SqlAlchemyRepository(db, Channel)
            existing = repo.find_by(channel_type=self.channel_type)
            if existing:
                return existing.id
            ch = Channel()
            ch.create(name=self.channel_type.capitalize(), channel_type=self.channel_type)
            repo.save(ch)
            db.commit()
            return ch.id

    def bind_thread(self, tenant_id: str, thread_id: str, channel_id: str) -> None:
        """Bind a channel to a thread. Idempotent."""
        with tenant_session(tenant_id) as db:
            repo = SqlAlchemyRepository(db, ChannelBinding)
            if not repo.find_by(thread_id=thread_id, channel_id=channel_id):
                binding = ChannelBinding()
                binding.id = _cuid()
                binding.thread_id = thread_id
                binding.channel_id = channel_id
                repo.save(binding)
            db.commit()

    def new_thread(self, tenant_id: str, channel_id: str, restate_url: str | None = None) -> str:
        """Create a new Thread, bind it to the channel, return thread_id."""
        thread_id = _cuid()
        self._post(f"/Thread/{thread_id}/create", {"tenant_id": tenant_id}, restate_url=restate_url)
        self.bind_thread(tenant_id, thread_id, channel_id)
        return thread_id

    # ── Thread management (bot/external adapters) ─────────────────────────────

    def get_or_create_thread(
        self,
        tenant_id: str,
        channel_id: str,
        external_id: str,
    ) -> str:
        """
        Return the active thread_id for an external conversation ID,
        creating a new thread if none exists.

        external_id — stable identifier for the external conversation
                      (e.g. Discord channel ID, Telegram chat ID)
        """
        thread_id = self._get_thread_mapping(tenant_id, channel_id, external_id)
        if not thread_id:
            thread_id = _cuid()
            self._post(f"/Thread/{thread_id}/create", {"tenant_id": tenant_id})
            self._set_thread_mapping(tenant_id, channel_id, external_id, thread_id)
        return thread_id

    def reset_thread(self, tenant_id: str, channel_id: str, external_id: str) -> str:
        """Start a fresh thread for an external conversation. Returns new thread_id."""
        thread_id = _cuid()
        self._post(f"/Thread/{thread_id}/create", {"tenant_id": tenant_id})
        self._set_thread_mapping(tenant_id, channel_id, external_id, thread_id)
        return thread_id

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_thread_mapping(self, tenant_id: str, channel_id: str, external_id: str) -> str | None:
        r = self._post(
            f"/Channel/{channel_id}/get_thread_mapping",
            {"tenant_id": tenant_id, "external_channel_id": external_id},
        )
        if r and r.status_code == 200:
            return r.json() or None
        return None

    def _set_thread_mapping(self, tenant_id: str, channel_id: str, external_id: str, thread_id: str) -> None:
        self._post(
            f"/Channel/{channel_id}/set_thread_mapping",
            {"tenant_id": tenant_id, "external_channel_id": external_id, "thread_id": thread_id},
        )

    def _post(self, path: str, body: dict, restate_url: str | None = None) -> httpx.Response | None:
        base = restate_url or os.environ.get("RESTATE_URL", "http://localhost:8080")
        try:
            return httpx.post(f"{base}{path}", json=body, timeout=10)
        except Exception:
            return None
