"""
WeatherAgent — manual LLM tool-use loop with HITL gate.

Flow:
  1. Ask LLM to plan (tool_choice="auto") — returns tool_calls or plain text
  2. For each get_weather call → ctx.request_approval (durable HITL, no polling)
  3. Execute approved fetches via ctx.step
  4. Feed results back to LLM for final answer

Registered as "weather".
"""

from __future__ import annotations

import json
import os

import httpx
from openai import OpenAI

from ironbridge.platform.agents.base import BaseAgent
from ironbridge.platform.agents.context import AgentContext
from ironbridge.platform.agents.registry import agent_registry

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }
]


def _make_client() -> tuple["OpenAI", str]:
    """Build (client, model) from env. Called once at module load."""
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("CEREBRAS_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL") or "https://api.cerebras.ai/v1"
    model = os.environ.get("LLM_MODEL", "llama3.1-8b")
    if model.startswith("openrouter/"):
        model = model[len("openrouter/"):]
        base_url = "https://openrouter.ai/api/v1"
    elif "/" in model and not model.startswith("http"):
        model = model.split("/", 1)[-1]
    return OpenAI(api_key=api_key, base_url=base_url), model


_llm_client, _llm_model = _make_client()


def _llm_call(messages: list, tools=None) -> dict:
    kwargs: dict = {"model": _llm_model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    resp = _llm_client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    return {
        "content": msg.content or "",
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": json.loads(tc.function.arguments)}
            for tc in (msg.tool_calls or [])
        ],
    }


def _fetch_weather(location: str) -> str:
    try:
        resp = httpx.get(f"https://wttr.in/{location}", params={"format": "3"}, timeout=8)
        resp.raise_for_status()
        return resp.text.strip()
    except Exception as e:
        return f"Could not fetch weather for {location}: {e}"


class WeatherAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)

        messages = [
            {"role": "system", "content": "You are a helpful weather assistant. Use get_weather when the user asks about weather."},
        ]
        for m in history:
            role = "user" if m.get("role") == "USER" else "assistant"
            parts = m.get("content", {}).get("parts", [])
            text = next((p.get("text", "") for p in parts if p.get("type") == "text"), "")
            if text:
                messages.append({"role": role, "content": text})

        if not messages or messages[-1]["role"] != "user":
            return

        # Step 1: LLM planning — may return tool_calls or direct answer
        response = await ctx.step("llm_plan", lambda: _llm_call(messages, tools=_TOOLS))

        tool_calls = response.get("tool_calls", [])

        if not tool_calls:
            # No tools needed — direct answer
            if response.get("content"):
                ctx.write_message(
                    {"version": 1, "parts": [{"type": "text", "text": response["content"]}]}, 0
                )
            return

        # Step 2: Resolve each location — clarify if ambiguous, then fetch
        tool_results = []
        for i, tc in enumerate(tool_calls):
            raw_location = tc["arguments"].get("location", "")

            # Ask LLM: is this location ambiguous? If so, produce options.
            clarify = await ctx.step(
                f"clarify_{i}_{raw_location}",
                lambda loc=raw_location: _llm_call([
                    {"role": "system", "content": (
                        "You are a location disambiguation assistant. "
                        "Given a location string, decide if it is ambiguous or unclear. "
                        "If it is clear and unambiguous, respond with JSON: {\"ambiguous\": false, \"resolved\": \"<location>\"}. "
                        "If it is ambiguous, respond with JSON: {\"ambiguous\": true, \"prompt\": \"<question to ask user>\", "
                        "\"options\": [{\"id\": \"<canonical name>\", \"label\": \"<display label>\"}, ...]}. "
                        "Always respond with valid JSON only, no extra text."
                    )},
                    {"role": "user", "content": loc},
                ]),
            )

            try:
                clarify_data = json.loads(clarify.get("content", "{}"))
            except (json.JSONDecodeError, AttributeError):
                clarify_data = {"ambiguous": False, "resolved": raw_location}

            if clarify_data.get("ambiguous"):
                response = await ctx.request_approval(
                    prompt=clarify_data.get("prompt", f"Which '{raw_location}' did you mean?"),
                    created_by=f"agent-run-{ctx.run_id}",
                    options=clarify_data.get("options", [
                        {"id": raw_location, "label": raw_location},
                        {"id": "cancel", "label": "Cancel"},
                    ]),
                    context={"raw_location": raw_location},
                )
                selected = response.selected[0] if response.selected else "cancel"
                if response.timed_out or selected == "cancel":
                    tool_results.append({"id": tc["id"], "name": tc["name"], "result": f"Cancelled for {raw_location}."})
                    continue
                location = selected
            else:
                location = clarify_data.get("resolved", raw_location)

            result = await ctx.step(
                f"get_weather_{i}_{location}",
                lambda loc=location: _fetch_weather(loc),
            )
            tool_results.append({"id": tc["id"], "name": tc["name"], "result": result})

        # Step 3: Feed results back to LLM for final answer
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": tr["id"], "type": "function", "function": {"name": tr["name"], "arguments": json.dumps(tool_calls[i]["arguments"])}}
            for i, tr in enumerate(tool_results)
        ]})
        for tr in tool_results:
            messages.append({"role": "tool", "tool_call_id": tr["id"], "content": tr["result"]})

        final = await ctx.step("llm_final", lambda: _llm_call(messages))
        ctx.write_message(
            {"version": 1, "parts": [{"type": "text", "text": final.get("content", "")}]}, 1
        )


agent_registry.register("weather", WeatherAgent)
