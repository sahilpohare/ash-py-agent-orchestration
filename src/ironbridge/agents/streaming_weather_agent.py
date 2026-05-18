"""
StreamingWeatherAgent — weather agent with streaming LLM output and HITL permission.

Differences from WeatherAgent:
  - Uses CrewAI LLM(stream=True) — token chunks are written to the thread in real-time
    via LLMStreamChunkEvent on crewai_event_bus.
  - Asks for HITL approval before calling the weather API.
  - Streaming is best-effort: on Restate replay the journaled result is returned
    immediately and chunk events do not re-fire (safe, expected behaviour).

Registered as "streaming_weather".
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

import httpx
from crewai import LLM, Agent, Crew, Task
from crewai.events.event_bus import crewai_event_bus
from crewai.events.types.llm_events import LLMStreamChunkEvent
from crewai.tools import BaseTool
from pydantic import BaseModel

from ironbridge.platform.agents.base import BaseAgent
from ironbridge.platform.agents.context import AgentContext
from ironbridge.platform.agents.registry import agent_registry

# ── Tool ───────────────────────────────────────────────────────────────────────


class GetWeatherInput(BaseModel):
    location: str


class GetWeatherTool(BaseTool):
    name: str = "get_weather"
    description: str = "Get the current weather for a given location."
    args_schema: type[BaseModel] = GetWeatherInput
    requires_approval: bool = True
    approval_prompt: str = 'Fetch live weather data for "{location}"?'

    def _run(self, location: str) -> str:
        try:
            resp = httpx.get(
                f"https://wttr.in/{location}",
                params={"format": "3"},
                timeout=8,
            )
            resp.raise_for_status()
            return resp.text.strip()
        except Exception as e:
            return f"Could not fetch weather for {location}: {e}"


# ── Streaming runner ────────────────────────────────────────────────────────────


def _run_crew_streaming(
    text: str,
    on_chunk: Callable[[str], None],
) -> str:
    """
    Run CrewAI crew with streaming enabled.
    on_chunk is called for each LLM token chunk as it arrives.
    Returns the final accumulated response string.
    """
    llm = LLM(
        model="cerebras/gpt-oss-120b",
        api_key=os.environ.get("CEREBRAS_API_KEY", ""),
        stream=True,
    )

    # Track chunks to avoid emitting tool-call fragments as text
    _lock = threading.Lock()
    _accumulated: list[str] = []

    @crewai_event_bus.on(LLMStreamChunkEvent)
    def _on_chunk(source: object, event: LLMStreamChunkEvent) -> None:
        chunk = event.chunk
        if not chunk:
            return
        with _lock:
            _accumulated.append(chunk)
        on_chunk(chunk)

    try:
        agent = Agent(
            role="Weather Assistant",
            goal="Answer weather questions accurately using the get_weather tool.",
            backstory="You are a helpful assistant that provides current weather information.",
            llm=llm,
            tools=[GetWeatherTool()],
            verbose=False,
        )
        task = Task(
            description=text,
            expected_output="A clear weather report answering the user's question.",
            agent=agent,
        )
        crew = Crew(agents=[agent], tasks=[task], verbose=False)
        result = crew.kickoff()
        return str(result)
    finally:
        crewai_event_bus.off(LLMStreamChunkEvent, _on_chunk)


# ── Agent ───────────────────────────────────────────────────────────────────────


class StreamingWeatherAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)

        last_user = next(
            (m for m in reversed(history) if m.get("role") == "USER"),
            None,
        )
        if not last_user:
            return

        parts = last_user.get("content", {}).get("parts", [])
        user_text = next((p.get("text", "") for p in parts if p.get("type") == "text"), "")
        if not user_text:
            return

        # ── Streaming crew run ─────────────────────────────────────────────────
        # chunk_count tracks how many write_message calls we've made for ordering.
        # Chunks are written as text_delta parts so the UI can append them.
        # The final result from ctx.run() is the full response (for journal).
        chunk_index = [0]

        def on_chunk(chunk: str) -> None:
            ctx.write_message(
                {"version": 1, "parts": [{"type": "text_delta", "text": chunk}]},
                chunk_index[0],
            )
            chunk_index[0] += 1

        def _run_crew(text: str = user_text) -> str:
            return _run_crew_streaming(text, on_chunk)

        await ctx.step("crew_run", _run_crew)
        # Final message is assembled by the UI from text_delta parts.
        # Write a terminal marker so clients know streaming is done.
        ctx.write_message(
            {"version": 1, "parts": [{"type": "stream_end"}]},
            chunk_index[0],
        )


agent_registry.register("streaming_weather", StreamingWeatherAgent)
