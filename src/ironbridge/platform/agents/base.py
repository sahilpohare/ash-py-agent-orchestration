"""
BaseAgent — abstract base every agent implementation must satisfy.

Agents receive an AgentContext and drive execution entirely through it.
No Restate imports, no framework knowledge — pure domain logic.

Registration:
    from ironbridge.platform.agents.registry import agent_registry
    agent_registry.register("my-agent-id", MyAgent)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ironbridge.platform.agents.context import AgentContext


class BaseAgent(ABC):
    """
    Abstract agent base.

    Subclasses implement run() — the full agent execution for a single
    AgentRun invocation. The agent controls its own loop, tool calls,
    and message writes via AgentContext.

    Cancellation: use ctx.step() for all durable work — it auto-checks
    cancel before each step and raises AgentCancelledError if set.
    The workflow runner catches AgentCancelledError and marks the run CANCELLED.
    """

    @abstractmethod
    async def run(self, ctx: AgentContext) -> None:
        """
        Execute the agent for one AgentRun.

        - Fetch history:       await ctx.step("fetch_history", ctx.get_history)
        - LLM call:            await ctx.step("llm_call_0", lambda: call_llm(history))
        - Tool execution:      await ctx.step("tool_0_search", lambda: execute(tc))
        - Write message:       ctx.write_message(content, message_count)
        - Request approval:    await ctx.request_approval(prompt, options)
        - Check cancellation:  handled automatically by ctx.step()
        """
        ...
