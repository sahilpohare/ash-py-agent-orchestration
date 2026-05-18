"""
AgentServer — FastAPI router that implements the Ironbridge agent protocol.

Mount this router in any FastAPI app. The platform will POST to /run and
receive a streaming SSE response.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Generator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ironbridge.agent_sdk.tools import BaseTool
from ironbridge.platform.agents.protocol import AgentRunPayload


class Emitter:
    """Collects SSE events; iterated by the StreamingResponse."""

    def __init__(self) -> None:
        self._events: list[str] = []

    def text_delta(self, text: str) -> None:
        self._emit({"type": "text_delta", "text": text})

    def tool_call(self, id: str, name: str, args: dict) -> None:
        self._emit({"type": "tool_call", "id": id, "name": name, "args": args})

    def done(self, text: str = "") -> None:
        self._emit({"type": "done", "text": text})

    def error(self, message: str) -> None:
        self._emit({"type": "error", "message": message})

    def _emit(self, event: dict) -> None:
        self._events.append(f"data: {json.dumps(event)}\n\n")

    def flush(self) -> Generator[str, None, None]:
        yield from self._events
        self._events.clear()


class AgentServer:
    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {t.name: t for t in (tools or [])}
        self._run_handler: Callable | None = None
        self._llm_model: str | None = None
        self._llm_system: str = "You are a helpful assistant."
        self.router = APIRouter()
        self._register_routes()

    def on_run(self, fn: Callable) -> Callable:
        """Decorator to register a custom run handler."""
        self._run_handler = fn
        return fn

    def use_llm(self, model: str, system: str = "You are a helpful assistant.") -> None:
        """Use an LLM-driven tool loop. No manual on_run needed."""
        self._llm_model = model
        self._llm_system = system

    def _register_routes(self) -> None:
        router = self.router

        @router.post("/run")
        async def run(payload: AgentRunPayload) -> StreamingResponse:
            emitter = Emitter()

            if self._llm_model:
                events = _llm_run(payload, self._tools, self._llm_model, self._llm_system, emitter)
            elif self._run_handler:
                self._run_handler(payload, emitter)
                emitter.done()
                events = emitter.flush()
            else:
                emitter.error("No run handler configured.")
                emitter.done()
                events = emitter.flush()

            return StreamingResponse(events, media_type="text/event-stream")

        @router.post("/tools/{name}")
        async def execute_tool(name: str, body: dict) -> Any:
            tool = self._tools.get(name)
            if not tool:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
            return tool.run(**body)

        @router.post("/tools")
        async def execute_tool_generic(body: dict) -> Any:
            name = body.get("name")
            args = body.get("args", {})
            tool = self._tools.get(name)
            if not tool:
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")
            return tool.run(**args)

        @router.get("/tools")
        async def list_tools() -> list[dict]:
            return [
                {
                    **t.to_llm_schema(),
                    "requires_approval": t.requires_approval,
                    "approval_prompt": t.approval_prompt,
                }
                for t in self._tools.values()
            ]


# ── LLM-driven runner ──────────────────────────────────────────────────────────


def _llm_run(
    payload: AgentRunPayload,
    tools: dict[str, BaseTool],
    model: str,
    system: str,
    emitter: Emitter,
) -> Generator[str, None, None]:
    """
    Single-turn LLM call with tool use.
    Streams text_delta events, then emits tool_calls for any tool use.
    Tool results from previous turns are injected into the message history.
    """
    import litellm

    messages: list[dict] = [{"role": "system", "content": system}]

    # Inject history
    for msg in payload.history:
        role = msg.role.lower() if msg.role != "ASSISTANT" else "assistant"
        if role == "system":
            continue
        parts = msg.content.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if text:
            messages.append({"role": role, "content": text})

    # Inject tool results from previous turn
    for tr in payload.tool_results:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tr.tool_call_id,
                "content": json.dumps(tr.result),
            }
        )

    tool_schemas = [t.to_llm_schema() for t in tools.values()] if tools else None

    accumulated = []
    pending_tool_calls: list[dict] = []

    try:
        resp = litellm.completion(
            model=model,
            messages=messages,
            tools=tool_schemas or litellm.utils.NOT_GIVEN,
            stream=True,
            api_key=os.environ.get("CEREBRAS_API_KEY", ""),
        )

        for chunk in resp:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue
            delta = choice.delta

            if delta.content:
                accumulated.append(delta.content)
                emitter.text_delta(delta.content)
                yield from emitter.flush()

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    # accumulate streamed tool call fragments
                    idx = tc.index
                    while len(pending_tool_calls) <= idx:
                        pending_tool_calls.append({"id": "", "name": "", "args": ""})
                    if tc.id:
                        pending_tool_calls[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        pending_tool_calls[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        pending_tool_calls[idx]["args"] += tc.function.arguments

    except Exception as e:
        emitter.error(str(e))
        yield from emitter.flush()
        return

    # Emit finalised tool calls
    for tc in pending_tool_calls:
        try:
            args = json.loads(tc["args"]) if tc["args"] else {}
        except json.JSONDecodeError:
            args = {}

        tool = tools.get(tc["name"])
        emitter.tool_call(
            id=tc["id"] or str(uuid.uuid4()),
            name=tc["name"],
            args={
                **args,
                # Surface approval hints so platform can gate execution
                **({"requires_approval": True} if tool and tool.requires_approval else {}),
                **(
                    {"approval_prompt": tool.approval_prompt.format(**args)}
                    if tool and tool.approval_prompt
                    else {}
                ),
            },
        )
        yield from emitter.flush()

    emitter.done(text="".join(accumulated))
    yield from emitter.flush()
