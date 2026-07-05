"""
Agent registry. Maps agent_id strings to BaseAgent instances.

    from ironbridge.platform_v2.agents.registry import agent_registry

    agent_registry.register("scheduling", SchedulingAgent())
    agent = agent_registry.resolve("scheduling")
"""
from __future__ import annotations

from .base import BaseAgent


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent_id: str, agent: BaseAgent) -> None:
        self._agents[agent_id] = agent

    def resolve(self, agent_id: str) -> BaseAgent:
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not registered")
        return self._agents[agent_id]

    def list(self) -> dict[str, BaseAgent]:
        return dict(self._agents)


agent_registry = AgentRegistry()
