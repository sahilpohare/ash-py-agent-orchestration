"""
CLI channel adapter.

Outbound: prints thread messages to stdout, colour-coded by role.
Inbound:  run `python -m services.channels.adapters.cli` for an interactive REPL.

config keys (set on the Channel record):
    show_system  (bool, default True)  — print SYSTEM/event messages
    show_user    (bool, default False) — echo USER messages back
"""

from __future__ import annotations

import os

from cuid2 import cuid_wrapper

from ironbridge.platform.channels.context import ChannelContext
from ironbridge.platform.channels.message import (
    ChannelMessage,
    EventPart,
    ResponseRequestPart,
    TextPart,
)
from ironbridge.platform.channels.registry import register_adapter
from services.channels.adapters.base import BaseChannelAdapter

_cuid = cuid_wrapper()

RESET  = "\033[0m"
BOLD   = "\033[1m"
BLUE   = "\033[34m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
DIM    = "\033[2m"


class CliAdapter(BaseChannelAdapter):
    channel_type = "cli"

    def on_message(self, message: ChannelMessage, config: dict, ctx: ChannelContext) -> None:
        show_system = config.get("show_system", True)
        show_user   = config.get("show_user", False)

        for part in message.parts:
            if isinstance(part, TextPart):
                if message.role == "ASSISTANT":
                    print(f"{GREEN}{BOLD}assistant:{RESET} {part.text}", flush=True)
                elif message.role == "USER" and show_user:
                    print(f"{BLUE}user:{RESET} {part.text}", flush=True)

            elif isinstance(part, EventPart) and show_system:
                if part.event == "AGENT_RUN_QUEUED":
                    print(f"{DIM}[queued · position {getattr(part, 'queue_position', '?')}]{RESET}", flush=True)
                elif part.event == "AGENT_RUN_FAILED":
                    print(f"{RED}[agent failed: {getattr(part, 'error', '')}]{RESET}", flush=True)
                elif part.event == "AGENT_RUN_RETRY":
                    print(f"{YELLOW}[retrying · {getattr(part, 'step', '')}]{RESET}", flush=True)
                elif part.event == "AGENT_RUN_ORPHANED":
                    print(f"{YELLOW}[orphaned run]{RESET}", flush=True)
                else:
                    print(f"{DIM}[{part.event}]{RESET}", flush=True)

            elif isinstance(part, ResponseRequestPart) and show_system:
                opts = " / ".join(o["label"] for o in (part.options or []))
                print(f"{YELLOW}[approval needed] {part.prompt}  [{opts}]{RESET}", flush=True)
                print(f"{DIM}  request_id: {part.request_id}{RESET}", flush=True)


def run_cli(
    tenant_id: str,
    thread_id: str | None = None,
    agent_id: str = "stub",
    user: str = "cli-user",
    restate_url: str | None = None,
) -> None:
    adapter = CliAdapter()
    channel_id = adapter.get_or_create_channel(tenant_id)
    if thread_id:
        adapter.bind_thread(tenant_id, thread_id, channel_id)
    else:
        thread_id = adapter.new_thread(tenant_id, channel_id)
    n = 0

    print(f"{DIM}thread: {thread_id}  agent: {agent_id}{RESET}", flush=True)
    print(f"{DIM}type a message, Ctrl-C to exit{RESET}\n", flush=True)

    try:
        while True:
            try:
                text_in = input(f"{BLUE}> {RESET}").strip()
            except EOFError:
                break
            if not text_in:
                continue
            n += 1
            adapter.receive(
                content={"version": 1, "parts": [{"type": "text", "text": text_in}]},
                thread_id=thread_id,
                tenant_id=tenant_id,
                participant_id=user,
                agent_id=agent_id,
                idempotency_key=f"cli-{thread_id}-{n}",
                restate_url=restate_url,
            )
    except KeyboardInterrupt:
        pass

    print(f"\n{DIM}bye{RESET}")


# Self-register
register_adapter(CliAdapter())


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Ironbridge CLI channel")
    p.add_argument("--tenant",  default=os.environ.get("TENANT_ID", "tenant-a"))
    p.add_argument("--thread",  default=None)
    p.add_argument("--agent",   default=os.environ.get("AGENT_ID", "stub"))
    p.add_argument("--user",    default="cli-user")
    p.add_argument("--restate", default=os.environ.get("RESTATE_URL", "http://localhost:8080"))
    args = p.parse_args()
    run_cli(args.tenant, args.thread, args.agent, args.user, args.restate)
