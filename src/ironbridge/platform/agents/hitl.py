"""
Human-in-the-loop primitives for agent workflows.

All HITL interaction flows through the thread message log.
The agent posts a response_request message; the human replies with a
response_reply message. Both are regular add_message calls — HITL is not
a side-channel, it is part of the unit of work.

Suspension model:
  - request_id is a stable cuid generated before any Restate state — DB is
    the source of truth for HITL identifiers.
  - AgentRun suspends on ctx.promise(f"hitl:{request_id}") — a named durable
    promise scoped to the workflow key. Survives Restate restarts.
  - derive/restate.py sees response_reply part → looks up run_id from DB →
    sends resolve_hitl signal to AgentRun workflow → handler resolves promise.
  - If workflow is dead (orphaned run), resolve_hitl writes AGENT_RUN_ORPHANED
    event to thread and FAILED to agent_run_events. Explicit, observable.

Usage inside AgentRun workflow:

    hitl = HITL(ctx, thread_id, run_id, tenant_id)

    response = await hitl.request_response(
        prompt="Deploy to production?",
        options=[
            {"id": "deploy", "label": "Deploy"},
            {"id": "cancel", "label": "Cancel"},
        ],
    )

    if response.timed_out or "cancel" in response.selected:
        ...

    # Yes/No is just options=[yes, no]
    # Free text is options=None
    # Multi-select is multi_select=True
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
import restate
from cuid2 import cuid_wrapper

_cuid = cuid_wrapper()


@dataclass
class HumanResponse:
    selected: list[str]        # option ids, or free-text values if options=None
    submitted_by: str          # participant_id of the human who responded
    timed_out: bool
    context: dict = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        """Convenience — True if selected contains 'yes' or 'approve'."""
        return bool(set(self.selected) & {"yes", "approve"})


class HITL:
    """
    Human-in-the-loop primitives for use inside an AgentRun Workflow.

    All interaction is through thread messages — no side-channel.
    Awakeable wiring is handled by derive/restate.py, invisible to agent code.
    """

    def __init__(self, ctx: Any, thread_id: str, run_id: str, tenant_id: str) -> None:
        self._ctx = ctx
        self._thread_id = thread_id
        self._run_id = run_id
        self._tenant_id = tenant_id

    async def request_response(
        self,
        prompt: str,
        created_by: str,
        options: list[dict] | None = None,
        multi_select: bool = False,
        context: dict | None = None,
        timeout: timedelta = timedelta(hours=24),
    ) -> HumanResponse:
        """
        Agent suspends and waits for a human response.

        Steps are durable via ctx.run() — same as every other workflow step.
        Suspension is via ctx.promise — a named promise resolved by
        AgentRun.resolve_hitl when the human replies via add_message.

        request_id is a stable cuid (journaled) — DB identifier, not the promise key.
        """
        # Stable cuid — generated once, written to DB, consistent across restarts.
        # ctx.run journals the generation so replay returns same id.
        request_id = await self._ctx.run(
            f"gen_request_id:{prompt[:32]}",
            lambda: _cuid(),
        )

        await self._ctx.run(
            f"write_response_request:{request_id}",
            lambda rid=request_id: _call_add_message(
                thread_id=self._thread_id,
                run_id=self._run_id,
                tenant_id=self._tenant_id,
                content={
                    "version": 1,
                    "parts": [
                        {
                            "type": "response_request",
                            "request_id": rid,
                            "prompt": prompt,
                            "options": options,
                            "multi_select": multi_select,
                            "context": context or {},
                            "created_by": created_by,
                        }
                    ],
                },
                idempotency_key=f"{self._run_id}:request:{rid}",
            ),
        )

        # Named durable promise — survives Restate restart, scoped to workflow key.
        # resolve_hitl handler resolves it when human replies via add_message.
        # .value() returns a ServerDurableFuture required by restate.select.
        promise = self._ctx.promise(f"hitl:{request_id}")
        promise_future = promise.value()
        sleep_future = self._ctx.sleep(timeout)

        match await restate.select(response=promise_future, timeout=sleep_future):
            case ["response", data]:
                result_key, result_data = "response", data
            case ["timeout", _]:
                result_key, result_data = "timeout", None
            case _:
                result_key, result_data = "timeout", None

        if result_key == "timeout":
            await self._ctx.run(
                f"write_request_timeout:{request_id}",
                lambda rid=request_id: _call_add_message(
                    thread_id=self._thread_id,
                    run_id=self._run_id,
                    tenant_id=self._tenant_id,
                    content={
                        "version": 1,
                        "parts": [{"type": "event", "event": "RESPONSE_TIMED_OUT", "request_id": rid}],
                    },
                    idempotency_key=f"{self._run_id}:timeout:{rid}",
                    role="SYSTEM",
                ),
            )
            return HumanResponse(selected=[], submitted_by="", timed_out=True)

        data = result_data or {}
        return HumanResponse(
            selected=data.get("selected", []),
            submitted_by=data.get("submitted_by", ""),
            timed_out=False,
            context=data,
        )

    async def inject_message(self, content: dict) -> None:
        """Agent writes a message into the thread mid-execution."""
        key = hashlib.sha256(
            f"{self._run_id}:inject:{json.dumps(content, sort_keys=True)}".encode()
        ).hexdigest()[:16]
        await self._ctx.run(
            f"inject_message:{key}",
            lambda: _call_add_message(
                thread_id=self._thread_id,
                run_id=self._run_id,
                tenant_id=self._tenant_id,
                content=content,
                idempotency_key=f"{self._run_id}:inject:{key}",
            ),
        )

    async def checkpoint(self, label: str, state: dict | None = None) -> None:
        """Agent saves current state at a named point."""
        await self._ctx.run(
            f"checkpoint:{label}",
            lambda: _call_add_message(
                thread_id=self._thread_id,
                run_id=self._run_id,
                tenant_id=self._tenant_id,
                content={
                    "version": 1,
                    "parts": [{"type": "event", "event": "CHECKPOINT", "label": label, "state": state or {}}],
                },
                idempotency_key=f"{self._run_id}:checkpoint:{label}",
                role="SYSTEM",
            ),
        )


# ── Internal helpers ───────────────────────────────────────────────────────────


def _call_add_message(
    thread_id: str,
    run_id: str,
    tenant_id: str,
    content: dict,
    idempotency_key: str | None = None,
    role: str = "ASSISTANT",
) -> None:
    restate_base = os.environ.get("RESTATE_URL", "http://localhost:8080")
    if idempotency_key is None:
        idempotency_key = hashlib.sha256(
            f"{run_id}:{json.dumps(content, sort_keys=True)}".encode()
        ).hexdigest()[:16]
    httpx.post(
        f"{restate_base}/Thread/{thread_id}/add_message",
        json={
            "participant_id": f"agent-run-{run_id}",
            "participant_type": "AGENT",
            "role": role,
            "content": content,
            "idempotency_key": idempotency_key,
            "tenant_id": tenant_id,
            "user_name": f"agent-run-{run_id}",
        },
        timeout=10,
    )
