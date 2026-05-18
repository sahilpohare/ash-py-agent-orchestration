"""
ActionContext — effect accumulator for domain actions.

Domain resources import this and call ctx.send() / ctx.send_workflow()
to declare side effects. Zero Restate imports — plain Python objects.

Infrastructure (restate.py) injects an ActionContext before calling the
action, then executes the collected effects via Restate after the DB write.

Usage in a domain action:

    from ironbridge.shared.framework.effects import ActionContext

    @action(kind=ActionKind.ACTION)
    def add_message(self, action_ctx: ActionContext, ...) -> Message:
        msg = Message(...)
        if msg.participant_type == "HUMAN":
            action_ctx.send_workflow("AgentRun", key=run_id, arg={...})
        if msg.role == "ASSISTANT":
            action_ctx.send("ChannelDelivery", key=channel_id, arg={...}, handler="deliver")
        return msg
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SendEffect:
    """Fire-and-forget call to a Restate Service or VirtualObject handler."""
    service: str
    handler: str
    key: str | None
    arg: Any


@dataclass
class WorkflowEffect:
    """Start or signal a Restate Workflow."""
    service: str        # workflow name e.g. "AgentRun"
    handler: str        # "run" for new workflow, or named handler
    key: str
    arg: Any


class ActionContext:
    """
    Collects effects declared by a domain action.
    Passed in by infrastructure, never imported from Restate.
    """

    def __init__(self) -> None:
        self._effects: list[SendEffect | WorkflowEffect] = []

    def send(self, service: str, handler: str, key: str, arg: Any) -> None:
        """Enqueue a fire-and-forget send to a VirtualObject handler."""
        self._effects.append(SendEffect(service=service, handler=handler, key=key, arg=arg))

    def send_workflow(self, service: str, key: str, arg: Any, handler: str = "run") -> None:
        """Enqueue a workflow start or signal."""
        self._effects.append(WorkflowEffect(service=service, handler=handler, key=key, arg=arg))

    @property
    def effects(self) -> list[SendEffect | WorkflowEffect]:
        return list(self._effects)
