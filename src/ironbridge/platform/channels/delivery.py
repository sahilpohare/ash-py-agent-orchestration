"""
ChannelDelivery — fanout of thread events to all bound channels.

Thread is the model. Every channel is a view of the same thread.
All messages (USER, ASSISTANT, SYSTEM, events, HITL) are routed here.
Each adapter decides what to render.

Adapter contract:
    class MyAdapter:
        channel_type: str = "cli"
        def on_message(self, message: dict, config: dict) -> None: ...

Register:
    from ironbridge.platform.channels.delivery import register_adapter
    register_adapter(CliAdapter())
"""

from __future__ import annotations

import logging
from typing import Any

import restate

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage
from ironbridge.platform.channels.registry import get_adapter
from ironbridge.shared.db import tenant_session
from ironbridge.shared.derive.repository import SqlAlchemyRepository

logger = logging.getLogger(__name__)

channel_delivery = restate.Service("ChannelDelivery")
_rp = restate.InvocationRetryPolicy(max_attempts=3)


@channel_delivery.handler(invocation_retry_policy=_rp)
async def deliver(ctx: Any, req: dict | None) -> None:
    """
    Deliver a thread message to the channel adapter.

    req: {channel_id, thread_id, tenant_id, message}
    message: {participant_id, participant_type, role, content}

    Looks up channel_type + config, dispatches to registered adapter.
    Adapter filters — it sees everything, renders what it cares about.
    """
    req = req or {}
    channel_id = req.get("channel_id", "")
    tenant_id = req.get("tenant_id", "")
    thread_id = req.get("thread_id", "")
    message = req.get("message", {})

    if not channel_id:
        logger.warning("deliver: no channel_id in req")
        return

    channel_ctx = ChannelContext(ctx, thread_id, channel_id, tenant_id)

    def _load_and_dispatch():
        from ironbridge.platform.channels.channel import Channel
        with tenant_session(tenant_id) as db:
            repo = SqlAlchemyRepository(db, Channel)
            channel = repo.find_by_id(channel_id)
            if not channel:
                logger.warning("deliver: channel %s not found in DB", channel_id)
                return
            channel_type = channel.channel_type
            config = channel.config or {}

        adapter = get_adapter(channel_type)
        if not adapter:
            logger.warning("deliver: no adapter for channel_type=%s", channel_type)
            return

        logger.debug("deliver: dispatching thread=%s to %s adapter", thread_id, channel_type)
        channel_msg = ChannelMessage.from_dict({**message, "thread_id": thread_id})
        adapter.on_message(channel_msg, config, channel_ctx)

    await ctx.run("deliver", _load_and_dispatch)
