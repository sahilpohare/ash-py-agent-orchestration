"""
HITLTestAgent — minimal agent that suspends for HITL approval then echoes the result.

Used exclusively for integration testing the HITL flow end-to-end.
Registered as "hitl_test".
"""

from ironbridge.platform.agents.base import BaseAgent
from ironbridge.platform.agents.context import AgentContext
from ironbridge.platform.agents.registry import agent_registry


class HITLTestAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)

        last_user = next(
            (m for m in reversed(history) if m.role == "USER"),
            None,
        )
        if not last_user:
            return

        response = await ctx.request_approval(
            prompt="Proceed with the request?",
            created_by=f"agent-run-{ctx.run_id}",
            options=[
                {"id": "approve", "label": "Approve"},
                {"id": "reject", "label": "Reject"},
            ],
        )

        if response.timed_out:
            ctx.write_message(
                {"version": 1, "parts": [{"type": "text", "text": "Request timed out."}]},
                0,
            )
        elif response.approved:
            ctx.write_message(
                {"version": 1, "parts": [{"type": "text", "text": "Approved — proceeding."}]},
                0,
            )
        else:
            ctx.write_message(
                {"version": 1, "parts": [{"type": "text", "text": "Rejected — stopping."}]},
                0,
            )


agent_registry.register("hitl_test", HITLTestAgent)
