"""
AgentContext — the runtime interface handed to every agent implementation.

Agents import nothing from Restate. All durable primitives (steps, HITL,
thread writes, history) are accessed through this object.

Constructed by the workflow runner (restate_workflow.py), wraps the Restate
WorkflowContext. Agents are unaware of Restate internals.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from restate.exceptions import RetryableError, TerminalError
from sqlalchemy import text

from ironbridge.platform.agents.agent_run import AgentRunRequest
from ironbridge.platform.agents.hitl import HITL, HumanResponse, _call_add_message
from ironbridge.shared.db import tenant_session

# HTTP status codes that are permanent — no point retrying
_TERMINAL_STATUS_CODES = {400, 401, 403, 404, 422}


class AgentCancelledError(Exception):
    """Raised by AgentContext.step() when a cancel signal is detected."""


class AgentContext:
    """
    Runtime context for an agent execution.

    Exposes domain-level primitives wrapping Restate:
      - step(name, fn)            durable step with automatic cancel check
      - run(name, fn)             durable step without cancel check (for setup/teardown)
      - get_history()             fetch thread messages from DB (sync, call inside step())
      - write_message(content)    fire-and-forget to Thread.add_message queue
      - request_approval(...)     HITL suspend/resume
      - is_cancelled()            non-blocking cancel check

    All writes go through the Thread VirtualObject queue — position ordering
    guaranteed. DB is the source of truth, not Restate state.
    """

    def __init__(self, restate_ctx: Any, req: AgentRunRequest) -> None:
        self._ctx = restate_ctx
        self.req = req
        self.thread_id = req.thread_id
        self.run_id = req.run_id
        self.tenant_id = req.tenant_id
        self.agent_id = req.agent_id
        self._hitl = HITL(restate_ctx, req.thread_id, req.run_id, req.tenant_id)
        self._cancel_promise = restate_ctx.promise("cancel")

    async def step(self, name: str, fn: Callable) -> Any:
        """
        Durable step with automatic cancel check before execution.
        Raises AgentCancelledError if cancel signal is set.
        Use this for all agent work — LLM calls, tool execution, history fetches.

        Permanent errors (HTTP 4xx) are wrapped as TerminalError so Restate
        stops retrying immediately and the workflow surfaces AGENT_RUN_FAILED.
        """
        if await self.is_cancelled():
            raise AgentCancelledError()

        def _guarded():
            try:
                return fn()
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status in _TERMINAL_STATUS_CODES:
                    raise TerminalError(str(e), status_code=500)
                raise

        try:
            return await self._ctx.run(name, _guarded)
        except RetryableError as e:
            _write_retry_event(self.thread_id, self.run_id, self.tenant_id, name, str(e))
            raise

    async def run(self, name: str, fn: Callable) -> Any:
        """
        Durable step without cancel check.
        Use for setup/teardown steps that must complete regardless of cancellation.
        """
        return await self._ctx.run(name, fn)

    def get_history(self) -> list[dict]:
        """
        Fetch thread message history from DB.
        Sync — designed to be called inside step() or run():
            history = await ctx.step("fetch_history", ctx.get_history)
        Filters control messages (response_reply, event) not visible to LLM.
        """
        return _fetch_thread(self.thread_id, self.tenant_id)

    def write_message(self, content: dict, message_count: int) -> None:
        """
        Fire-and-forget write to Thread.add_message queue.
        Non-blocking — workflow does not wait for Thread handler.
        Position ordering guaranteed by Thread's exclusive queue.
        """
        ikey = hashlib.sha256(f"{self.run_id}:response:{message_count}".encode()).hexdigest()[:16]
        self._ctx.generic_send(
            "Thread",
            "add_message",
            json.dumps(
                {
                    "participant_id": f"agent-run-{self.run_id}",
                    "participant_type": "AGENT",
                    "role": "ASSISTANT",
                    "content": content,
                    "idempotency_key": f"{self.run_id}:response:{ikey}",
                    "tenant_id": self.tenant_id,
                    "user_name": f"agent-run-{self.run_id}",
                }
            ).encode(),
            key=self.thread_id,
        )

    async def call(
        self,
        tool: Any,
        step_name: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Run a tool as a durable step, with optional HITL gate.

        If the tool has `requires_approval = True`, a HITL prompt is shown
        before execution. The prompt can be customised via `approval_prompt`
        (a string that may reference kwargs by name, e.g. "Fetch weather for {location}?").

        Usage:
            result = await ctx.call(MyTool(), location="London")
        """
        requires_approval: bool = getattr(tool, "requires_approval", False)
        approval_prompt: str = getattr(tool, "approval_prompt", f"Allow tool `{tool.name}` to run?")
        try:
            formatted_prompt = approval_prompt.format(**kwargs)
        except (KeyError, AttributeError):
            formatted_prompt = approval_prompt

        if requires_approval:
            approval = await self.request_approval(
                prompt=formatted_prompt,
                created_by=f"agent-run-{self.run_id}",
                options=[
                    {"id": "approve", "label": "Allow"},
                    {"id": "reject", "label": "Deny"},
                ],
            )
            if not approval.approved:
                return f"Tool `{tool.name}` was denied by the user."

        name = step_name or f"{tool.name}:{':'.join(str(v) for v in kwargs.values())}"
        return await self.step(name, lambda: tool._run(**kwargs))

    async def request_approval(
        self,
        prompt: str,
        created_by: str,
        options: list[dict] | None = None,
        context: dict | None = None,
        timeout: timedelta = timedelta(hours=24),
    ) -> HumanResponse:
        """Suspend and wait for human response via HITL promise."""
        return await self._hitl.request_response(
            prompt=prompt,
            created_by=created_by,
            options=options,
            context=context,
            timeout=timeout,
        )

    async def is_cancelled(self) -> bool:
        """Non-blocking check of cancel signal."""
        try:
            val = await self._cancel_promise.peek()
            return val is True
        except Exception:
            return False


# ── Internal DB helpers ────────────────────────────────────────────────────────


def _fetch_thread(thread_id: str, tenant_id: str) -> list[dict]:
    """
    Fetch thread message history directly from DB.
    Filters control messages (response_reply, event) — not visible to LLM.
    """
    with tenant_session(tenant_id) as db:
        rows = db.execute(
            text(
                "SELECT id, participant_id, participant_type, role, content, position "
                "FROM messages WHERE thread_id = :tid ORDER BY position"
            ),
            {"tid": thread_id},
        ).fetchall()

    msgs = []
    for r in rows:
        content = r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}")
        parts = content.get("parts", [])
        if any(p.get("type") in ("response_reply", "event") for p in parts):
            continue
        msgs.append(
            {
                "id": r[0],
                "participant_id": r[1],
                "participant_type": r[2],
                "role": r[3],
                "content": content,
                "position": r[5],
            }
        )
    return msgs


def _write_retry_event(
    thread_id: str, run_id: str, tenant_id: str, step_name: str, error: str
) -> None:
    """
    Write an AGENT_RUN_RETRY event message to the thread.
    Called synchronously inside _guarded() before re-raising RetryableError.
    Surfaces retry notifications to UI via Pusher.
    """
    ikey = hashlib.sha256(f"{run_id}:retry:{step_name}:{int(time.time())}".encode()).hexdigest()[:16]
    _call_add_message(
        thread_id=thread_id,
        run_id=run_id,
        tenant_id=tenant_id,
        content={
            "version": 1,
            "parts": [
                {
                    "type": "event",
                    "event": "AGENT_RUN_RETRY",
                    "step": step_name,
                    "error": error,
                }
            ],
        },
        idempotency_key=ikey,
        role="SYSTEM",
    )
