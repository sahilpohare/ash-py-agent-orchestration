"""
Entrypoint. Imports all domain resources — registration is a side effect of
import — then derives Restate VirtualObjects and Starlette HTTP routes from
the registry.
"""

import os

import restate
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ironbridge.agents.hitl_test_agent import (
    HITLTestAgent,  # noqa: F401 — registers "hitl_test" agent
)
from ironbridge.agents.streaming_weather_agent import (
    StreamingWeatherAgent,  # noqa: F401 — registers "streaming_weather" agent
)
from ironbridge.agents.stub import StubAgent  # noqa: F401 — registers "stub" agent
from ironbridge.agents.weather_agent import WeatherAgent  # noqa: F401 — registers "weather" agent
from ironbridge.platform.agents.agent import Agent  # noqa: F401
from ironbridge.platform.agents.agent_run_event import AgentRunEvent  # noqa: F401
from ironbridge.platform.channels.channel import Channel  # noqa: F401
from ironbridge.platform.channels.channel_binding import ChannelBinding  # noqa: F401
from ironbridge.platform.channels.delivery import channel_delivery
from ironbridge.platform.channels.registry import get_adapter
from ironbridge.platform.identity.tenant import Tenant  # noqa: F401
from ironbridge.platform.identity.user import User  # noqa: F401
from ironbridge.platform.sessions.message import Message  # noqa: F401
from ironbridge.platform.sessions.thread import Thread  # noqa: F401
from ironbridge.shared.derive.restate import derive_virtual_object
from ironbridge.shared.derive.restate_workflow import agent_run_workflow
from ironbridge.shared.framework import registry
from ironbridge.platform.agents.registry import agent_registry
from services.channels.adapters.cli import CliAdapter  # noqa: F401 — registers "cli" adapter
from services.channels.adapters.web import WebAdapter  # noqa: F401 — registers "web" adapter

# ── Validate all registered agents are instantiable ───────────────────────────
agent_registry.validate_all()

# ── Derive Restate VirtualObjects ──────────────────────────────────────────────
restate_services = [
    derive_virtual_object(cls)
    for cls in registry.all_resources().values()
    if cls.__meta__.get("restate_object")
]

# ── AgentRun Workflow ──────────────────────────────────────────────────────────
restate_services.append(agent_run_workflow)

# ── ChannelDelivery ────────────────────────────────────────────────────────────
restate_services.append(channel_delivery)

# ── Restate ASGI app ───────────────────────────────────────────────────────────
restate_app = restate.app(services=restate_services)

# ── FastAPI app ────────────────────────────────────────────────────────────────
fastapi_app = FastAPI()
_cors_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()]
fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_web_adapter = get_adapter("web")
if _web_adapter and hasattr(_web_adapter, "get_router"):
    fastapi_app.include_router(_web_adapter.get_router())  # type: ignore[arg-type]

fastapi_app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="frontend")


async def app(scope, receive, send):
    """
    Combined ASGI app.
    /api/* → FastAPI (browser-facing, authed)
    everything else → Restate ASGI (internal SDK protocol + VirtualObject handlers)
    """
    if scope["type"] == "http":
        path = scope.get("path", "")
        if path.startswith("/api/") or path in ("/docs", "/openapi.json") or path in ("/", "/index.html"):
            await fastapi_app(scope, receive, send)
            return
    await restate_app(scope, receive, send)
