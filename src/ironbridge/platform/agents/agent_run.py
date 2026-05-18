"""
AgentRun — a single execution of an Agent on a Thread.

Relationship:
  Agent (1) ──── (*) AgentRun
                        │
                        └── wraps Restate Workflow (infra, derive/restate_workflow.py)

AgentRun is not a VirtualObject — it is a one-shot Workflow.
Key = run_id (unique per execution).

Lifecycle: RUNNING → COMPLETED | CANCELLED | FAILED

Agent messages write back via Thread.add_message() — same queue as human
messages. Position counter stays consistent. No bypass.

Cancellation at step boundaries — current ctx.run() completes, then loop
checks the cancel promise and exits cleanly.

Derivation deferred — wired in shared/derive/restate_workflow.py when the
agents subdomain is fully designed.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class AgentRunRequest(BaseModel):
    run_id: str
    agent_id: str
    thread_id: str
    tenant_id: str


class ResolveHITLRequest(BaseModel):
    request_id: str
    thread_id: str
    tenant_id: str
    selected: list[str]
    submitted_by: str


class AgentRunResult(BaseModel):
    run_id: str
    agent_id: str
    thread_id: str
    status: AgentRunStatus
    message_count: int
    error: str | None = None
