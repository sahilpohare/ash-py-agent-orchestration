"""
StubAgent — reference implementation for development and testing.

Demonstrates the full agent loop pattern:
  - History fetch via ctx.step()
  - LLM call wrapped in ctx.step() for durability
  - Tool execution with HITL approval gate
  - Writing responses via ctx.write_message()
  - Cooperative cancellation via ctx.step() (automatic)

Replace _call_llm and _execute_tool with real implementations.
"""

from __future__ import annotations

from datetime import timedelta

from ironbridge.platform.agents.base import BaseAgent
from ironbridge.platform.agents.context import AgentContext
from ironbridge.platform.agents.registry import agent_registry
from ironbridge.platform.sessions.thread import MessageView

APPROVAL_REQUIRED = {"write_file", "delete_rows", "read_db", "send_email"}


class StubAgent(BaseAgent):
    async def run(self, ctx: AgentContext) -> None:
        history = await ctx.step("fetch_history", ctx.get_history)
        message_count = 0

        while True:
            response = await ctx.step(
                f"llm_call_{message_count}",
                lambda h=history: _call_llm(h),
            )

            if not response:
                break

            tool_calls = response.get("tool_calls", [])
            needs_approval = [tc for tc in tool_calls if tc["name"] in APPROVAL_REQUIRED]

            # Write intro text first, durably, before any HITL cards
            text = response.get("content", "")
            if text == "__MULTI_CHOICE__":
                # Demo: multi-option HITL card — ask user to pick one of several options
                choice = await ctx.request_approval(
                    prompt="Which option would you like?",
                    created_by=f"agent-run-{ctx.run_id}",
                    options=[
                        {"id": "option_a", "label": "Option A — Run summary report"},
                        {"id": "option_b", "label": "Option B — Export to CSV"},
                        {"id": "option_c", "label": "Option C — Send email digest"},
                        {"id": "cancel",   "label": "Cancel"},
                    ],
                    context={"demo": "multi_choice"},
                    timeout=timedelta(hours=24),
                )
                selected = choice.selected[0] if choice.selected else "cancel"
                if choice.timed_out or selected == "cancel":
                    reply = "Cancelled — no option selected."
                else:
                    labels = {
                        "option_a": "Running summary report…",
                        "option_b": "Exporting to CSV…",
                        "option_c": "Sending email digest…",
                    }
                    reply = labels.get(selected, f"Selected: {selected}")
                ctx.write_message(
                    {"version": 1, "parts": [{"type": "text", "text": reply}]},
                    message_count,
                )
                message_count += 1
                break
            elif text:
                ctx.write_message(
                    {"version": 1, "parts": [{"type": "text", "text": text}]},
                    message_count,
                )
                message_count += 1

            # Request all approvals sequentially (one HITL card per tool)
            approvals: dict[str, bool] = {}
            for tc in needs_approval:
                approval = await ctx.request_approval(
                    prompt=f"Allow `{tc['name']}`?",
                    created_by=f"agent-run-{ctx.run_id}",
                    options=[
                        {"id": "approve", "label": "Approve"},
                        {"id": "reject", "label": "Reject"},
                    ],
                    context={"tool": tc["name"], "arguments": tc.get("arguments", {})},
                    timeout=timedelta(hours=24),
                )
                approvals[tc["id"]] = approval.approved

            # Show "processing…" after all approvals before executing
            if needs_approval:
                ctx.write_message(
                    {"version": 1, "parts": [{"type": "text", "text": "Processing…"}]},
                    message_count,
                )
                message_count += 1

            tool_results = []
            for i, tool_call in enumerate(tool_calls):
                if tool_call["name"] in APPROVAL_REQUIRED and not approvals.get(tool_call["id"], False):
                    tool_results.append(f"`{tool_call['name']}`: denied")
                    continue
                result = await ctx.step(
                    f"tool_{message_count}_{i}_{tool_call['name']}",
                    lambda tc=tool_call: _execute_tool(tc),
                )
                tool_results.append(f"`{tool_call['name']}`: {result}")

            if tool_results:
                summary = "Done:\n" + "\n".join(tool_results)
                ctx.write_message(
                    {"version": 1, "parts": [{"type": "text", "text": summary}]},
                    message_count,
                )
                message_count += 1

            if response.get("done"):
                break

            history = await ctx.step(
                f"fetch_history_{message_count}",
                ctx.get_history,
            )


# ── Stubs — replace with real LLM/tool implementations ────────────────────────

def _call_llm(history: list[MessageView]) -> dict | None:
    if not history:
        return None
    last = history[-1]
    if last.role != "USER":
        return {"content": "", "tool_calls": [], "done": True, "raw": None}
    parts = last.content.get("parts", [])
    text = next((p.get("text", "") for p in parts if p.get("type") == "text"), "")
    if "choose" in text.lower() or "pick" in text.lower() or "options" in text.lower():
        return {
            "content": "__MULTI_CHOICE__",
            "tool_calls": [],
            "done": True,
            "raw": None,
        }
    if "parallel" in text.lower():
        return {
            "content": "I'll run 3 tools in parallel — each needs your approval.",
            "tool_calls": [
                {"id": "tc-1", "name": "write_file", "arguments": {"path": "/tmp/out.txt", "content": text}},
                {"id": "tc-2", "name": "read_db", "arguments": {"query": "SELECT * FROM users LIMIT 10"}},
                {"id": "tc-3", "name": "send_email", "arguments": {"to": "admin@example.com", "subject": "Report"}},
            ],
            "done": True,
            "raw": None,
        }
    if "write" in text.lower():
        return {
            "content": "I'll write that file for you.",
            "tool_calls": [{"id": "tc-1", "name": "write_file", "arguments": {"path": "/tmp/out.txt", "content": text}}],
            "done": True,
            "raw": None,
        }
    return {"content": f"Echo: {text}", "tool_calls": [], "done": True, "raw": None}


def _execute_tool(tool_call: dict) -> dict:
    name = tool_call["name"]
    args = tool_call.get("arguments", {})
    if name == "search":
        return {"results": [f"Stub result for: {args.get('query')}"]}
    if name == "write_file":
        return {"written": True, "path": args.get("path")}
    if name == "read_db":
        return {"rows": 10, "query": args.get("query")}
    if name == "send_email":
        return {"sent": True, "to": args.get("to")}
    return {"error": f"unknown tool: {name}"}


agent_registry.register("stub", StubAgent)
