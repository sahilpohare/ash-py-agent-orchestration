"""
Webhook channel adapter — generic HTTP callback outbound.

Outbound: POSTs ASSISTANT text messages to config["callback_url"].
Inbound:  POST /inbound/{channel_id} via the channel service HTTP router.

config keys:
    callback_url  (str) — URL to POST outbound messages to
"""

from __future__ import annotations

import httpx

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage, TextPart
from services.channels.adapters.base import BaseChannelAdapter


class WebhookAdapter(BaseChannelAdapter):
    channel_type = "webhook"

    def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
        if message.role != "ASSISTANT":
            return
        callback_url = config.get("callback_url")
        if not callback_url:
            return
        text = " ".join(p.text for p in message.parts if isinstance(p, TextPart))
        if not text:
            return
        httpx.post(
            callback_url,
            json={"thread_id": message.thread_id, "text": text},
            timeout=10,
        )
