"""
BaseAgent - abstract base for agent implementations.

Agents implement run() which receives a WorkflowContext.
Registration via agent_registry.

    class MyAgent(BaseAgent):
        async def run(self, ctx: WorkflowContext) -> None:
            history = await ctx.step("fetch", ctx.get_history)
            ...
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ironbridge.shared.framework.workflow import WorkflowContext


class BaseAgent(ABC):
    @abstractmethod
    async def run(self, ctx: WorkflowContext) -> None:
        ...
