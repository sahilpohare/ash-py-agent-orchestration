"""
Agent registry — maps agent_id strings to BaseAgent classes.

Agents self-register at import time. The workflow runner resolves the
implementation by agent_id from AgentRunRequest.

Usage:
    # In your agent module:
    from ironbridge.platform.agents.registry import agent_registry
    agent_registry.register("my-agent", MyAgent)

    # Resolved by workflow runner automatically via req.agent_id.
"""

from __future__ import annotations

from ironbridge.platform.agents.base import BaseAgent


class AgentRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, type[BaseAgent] | str] = {}

    def register(self, agent_id: str, cls: type[BaseAgent]) -> None:
        self._registry[agent_id] = cls

    def register_url(self, agent_id: str, url: str) -> None:
        """Register a remote agent by URL. No local class required."""
        self._registry[agent_id] = url

    def resolve(self, agent_id: str) -> BaseAgent:
        entry = self._registry.get(agent_id)
        if entry is None:
            raise KeyError(
                f"No agent registered for id '{agent_id}'. "
                f"Available: {list(self._registry)}"
            )
        if isinstance(entry, str):
            from ironbridge.platform.agents.remote_agent import RemoteAgent
            return RemoteAgent(entry)
        return entry()

    def all(self) -> dict[str, type[BaseAgent] | str]:
        return dict(self._registry)


agent_registry = AgentRegistry()
