"""
Ironbridge Agent SDK — batteries-included server for external agents.

Mount this in any FastAPI app to implement the Ironbridge agent protocol.

Usage:

    from fastapi import FastAPI
    from ironbridge.agent_sdk import AgentServer, BaseTool

    class GetWeatherTool(BaseTool):
        name = "get_weather"
        requires_approval = True
        approval_prompt = 'Fetch live weather data for "{location}"?'

        def run(self, location: str) -> str:
            import httpx
            resp = httpx.get(f"https://wttr.in/{location}", params={"format": "3"})
            return resp.text.strip()

    server = AgentServer(tools=[GetWeatherTool()])

    @server.on_run
    def my_agent(payload, emit):
        # emit() sends SSE events back to the platform
        location = extract_location(payload.history)
        emit.tool_call("tc-1", "get_weather", {"location": location})

    app = FastAPI()
    app.include_router(server.router)

Or use the LLM-driven runner which handles the tool loop automatically:

    server = AgentServer(tools=[GetWeatherTool()])
    server.use_llm("cerebras/gpt-oss-120b", system="You answer weather questions.")
    app.include_router(server.router)
"""

from ironbridge.agent_sdk.server import AgentServer
from ironbridge.agent_sdk.tools import BaseTool

__all__ = ["AgentServer", "BaseTool"]
