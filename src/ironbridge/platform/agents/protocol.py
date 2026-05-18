"""
Ironbridge Agent Protocol — wire types for the external agent API.

An external agent is any HTTP service that implements:

  POST {base_url}/run
    Request:  AgentRunPayload (JSON)
    Response: SSE stream of AgentEvent

  Each turn: platform sends history, agent streams back text + tool calls.
  Platform executes tools (with HITL, durability), then posts next turn with
  updated history including tool results.

Agent developers only need to implement the HTTP contract — no Restate SDK,
no platform imports. Use ironbridge.agent_sdk (FastAPI router) for a batteries-
included server, or implement the protocol in any language/framework.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ── Inbound (platform → agent) ─────────────────────────────────────────────────

class HistoryMessage(BaseModel):
    role: Literal["USER", "ASSISTANT", "SYSTEM"]
    content: dict


class AgentRunPayload(BaseModel):
    """Posted to POST /run on each turn."""
    run_id: str
    agent_id: str
    thread_id: str
    tenant_id: str
    history: list[HistoryMessage]
    tool_results: list[ToolResult] = []  # populated on turns after tool calls


class ToolResult(BaseModel):
    tool_call_id: str
    result: Any


# ── Outbound (agent → platform, SSE) ──────────────────────────────────────────

class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str                    # stable id for this tool call
    name: str
    args: dict[str, Any]


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    text: str = ""             # final accumulated text (convenience)


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


AgentEvent = TextDeltaEvent | ToolCallEvent | DoneEvent | ErrorEvent
