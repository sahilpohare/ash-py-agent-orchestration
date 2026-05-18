"""
RemoteAgent — adapts an external HTTP agent to the BaseAgent interface.

The platform treats remote agents identically to local ones. The workflow
runner calls agent.run(ctx) as normal; this adapter drives the HTTP/SSE
protocol loop, handling:

  - Multi-turn: tool calls returned by the agent are executed as ctx.step()
    calls (durable, cancellable) then fed back in the next /run POST.
  - HITL: tools with requires_approval=True get a ctx.request_approval() gate
    before execution. The remote agent declares this via tool metadata in the
    DoneEvent or via a separate GET /tools endpoint (optional).
  - Streaming: text_delta events are forwarded as write_message calls so the
    frontend sees tokens in real time.
  - Cancellation: ctx.step() checks cancel before every tool execution.

Registration:
    agent_registry.register_url("my-agent", "https://my-agent.example.com")
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ironbridge.platform.agents.base import BaseAgent
from ironbridge.platform.agents.context import AgentContext
from ironbridge.platform.agents.protocol import (
    AgentRunPayload,
    HistoryMessage,
    ToolResult,
)


class RemoteAgent(BaseAgent):
    def __init__(self, url: str) -> None:
        # strip trailing slash
        self._url = url.rstrip("/")

    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)
        tool_results: list[ToolResult] = []
        message_count = 0
        turn = 0

        while True:
            payload = AgentRunPayload(
                run_id=ctx.run_id,
                agent_id=ctx.agent_id,
                thread_id=ctx.thread_id,
                tenant_id=ctx.tenant_id,
                history=[
                    HistoryMessage(role=m.role, content=m.content)
                    for m in history
                ],
                tool_results=tool_results,
            )

            # Stream one turn from the remote agent
            text, tool_calls = await ctx.step(
                f"remote_turn_{turn}",
                lambda p=payload: _call_agent(self._url, p),
            )

            # Forward any text as streaming deltas
            if text:
                ctx.write_message(
                    {"version": 1, "parts": [{"type": "text", "text": text}]},
                    message_count,
                )
                message_count += 1

            # No tool calls → done
            if not tool_calls:
                break

            # Execute each tool call as a durable step with optional HITL
            tool_results = []
            for tc in tool_calls:
                requires_approval: bool = tc.get("requires_approval", False)
                approval_prompt: str = tc.get(
                    "approval_prompt",
                    f"Allow tool `{tc['name']}` to run with args {tc['args']}?",
                )

                if requires_approval:
                    approval = await ctx.request_approval(
                        prompt=approval_prompt,
                        created_by=f"agent-run-{ctx.run_id}",
                        options=[
                            {"id": "approve", "label": "Allow"},
                            {"id": "reject", "label": "Deny"},
                        ],
                    )
                    if not approval.approved:
                        tool_results.append(ToolResult(
                            tool_call_id=tc["id"],
                            result="Tool execution was denied by the user.",
                        ))
                        continue

                result = await ctx.step(
                    f"tool_{turn}_{tc['id']}",
                    lambda t=tc: _execute_tool(self._url, t),
                )
                tool_results.append(ToolResult(tool_call_id=tc["id"], result=result))

            # Refresh history for next turn
            history = await ctx.step(f"fetch_history_{turn}", ctx.get_history)
            turn += 1


# ── HTTP helpers ───────────────────────────────────────────────────────────────


def _call_agent(url: str, payload: AgentRunPayload) -> tuple[str, list[dict]]:
    """
    POST /run, consume SSE stream.
    Returns (accumulated_text, tool_calls).
    Sync — designed to run inside ctx.step().
    """
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    with httpx.Client(timeout=120) as client:
        with client.stream(
            "POST",
            f"{url}/run",
            json=payload.model_dump(),
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type")
                if etype == "text_delta":
                    text_parts.append(event.get("text", ""))
                elif etype == "tool_call":
                    tool_calls.append(event)
                elif etype == "done":
                    # agent may send final accumulated text here
                    if event.get("text") and not text_parts:
                        text_parts.append(event["text"])
                elif etype == "error":
                    raise RuntimeError(f"Remote agent error: {event.get('message')}")

    return "".join(text_parts), tool_calls


def _execute_tool(url: str, tool_call: dict[str, Any]) -> Any:
    """
    POST /tools/{name} to execute a tool on the remote agent.
    Falls back to a generic POST /tools if the named endpoint 404s.
    Sync — designed to run inside ctx.step().
    """
    name = tool_call["name"]
    args = tool_call.get("args", {})

    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{url}/tools/{name}", json=args)
        if resp.status_code == 404:
            resp = client.post(f"{url}/tools", json={"name": name, "args": args})
        resp.raise_for_status()
        return resp.json()
