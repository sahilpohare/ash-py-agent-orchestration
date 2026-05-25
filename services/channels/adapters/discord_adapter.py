"""
Discord channel adapter.

Outbound: on_message → send text to the Discord channel the message came from.
Inbound:  discord.py bot listens for messages, routes to thread via receive().

Each Discord channel (or DM) maps to one Ironbridge thread, managed by
get_or_create_thread() / reset_thread() in BaseChannelAdapter.

/new or /reset — start a fresh thread for this Discord channel.

Run the bot:
    python -m services.channels.adapters.discord_adapter \\
        --tenant tenant-a --channel <ironbridge_channel_id>

Registration:
    register_adapter(DiscordAdapter()) in main.py to enable outbound delivery.
    The bot process is separate — run it per Channel record.
"""

from __future__ import annotations

import logging
import os

import discord
import httpx

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import ChannelMessage, TextPart
from services.channels.adapters.base import BaseChannelAdapter

logger = logging.getLogger(__name__)


class DiscordAdapter(BaseChannelAdapter):
    channel_type = "discord"

    # ── Outbound ──────────────────────────────────────────────────────────────

    def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
        """Send assistant messages back to the originating Discord channel."""
        if message.role not in ("ASSISTANT", "SYSTEM"):
            return

        discord_channel_id = config.get("threads", {})
        discord_channel_id = next(
            (dc_id for dc_id, tid in config.get("threads", {}).items() if tid == message.thread_id),
            None,
        )
        if not discord_channel_id:
            return

        text = " ".join(p.text for p in message.parts if isinstance(p, TextPart) and p.text)
        if not text:
            return

        bot_token = config.get("bot_token") or os.environ.get("DISCORD_BOT_TOKEN")
        if not bot_token:
            logger.warning("discord adapter: no bot_token in config")
            return

        try:
            httpx.post(
                f"https://discord.com/api/v10/channels/{discord_channel_id}/messages",
                headers={"Authorization": f"Bot {bot_token}"},
                json={"content": text},
                timeout=10,
            )
        except Exception:
            logger.exception("discord adapter: failed to send to channel %s", discord_channel_id)

    # ── Bot runner ────────────────────────────────────────────────────────────

    def run_bot(self, tenant_id: str, channel_id: str, bot_token: str) -> None:
        """
        Start the discord.py bot. Blocks until stopped.
        Each Discord text channel gets its own Ironbridge thread on first message.
        """
        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready() -> None:
            logger.info("discord bot ready as %s", client.user)

        @client.event
        async def on_message(msg: discord.Message) -> None:
            if msg.author.bot:
                return

            external_id = str(msg.channel.id)
            text = msg.content.strip()

            if text.lower() in ("/new", "/reset"):
                thread_id = self.reset_thread(tenant_id, channel_id, external_id)
                await msg.channel.send(f"New conversation started. (thread `{thread_id}`)")
                return

            thread_id = self.get_or_create_thread(tenant_id, channel_id, external_id)
            self.receive(
                text=text,
                thread_id=thread_id,
                tenant_id=tenant_id,
                participant_id=str(msg.author.id),
                idempotency_key=f"discord-{external_id}-{msg.id}",
            )

        client.run(bot_token)


# Self-register for outbound delivery
from ironbridge.platform.channels.registry import register_adapter  # noqa: E402

register_adapter(DiscordAdapter())


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Ironbridge Discord bot")
    p.add_argument("--tenant", default=os.environ.get("TENANT_ID", "tenant-a"))
    p.add_argument("--channel", required=True, help="Ironbridge channel_id")
    p.add_argument("--token", default=os.environ.get("DISCORD_BOT_TOKEN"))
    args = p.parse_args()

    if not args.token:
        raise SystemExit("DISCORD_BOT_TOKEN required")

    DiscordAdapter().run_bot(args.tenant, args.channel, args.token)
