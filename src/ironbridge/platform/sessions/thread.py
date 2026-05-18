import json
from datetime import UTC, datetime

from cuid2 import cuid_wrapper
from sqlalchemy import DateTime, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ironbridge.platform.channels.channel import resolve_agent_for_channel
from ironbridge.platform.channels.channel_binding import resolve_channel_for_thread
from ironbridge.platform.sessions.message import Message, MessageRole, ParticipantType
from ironbridge.shared.db import tenant_session
from ironbridge.shared.framework import ActionContext, ActionKind, Resource, action

_cuid = cuid_wrapper()
_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class Thread(Resource):
    class Meta:
        tenant_scoped = True
        restate_object = True

    __tablename__ = "threads"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_cuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    messages: Mapped[list[Message]] = relationship(
        "Message",
        cascade="all, delete-orphan",
        order_by="Message.position",
        lazy="selectin",
        foreign_keys="Message.thread_id",
    )

    @action(kind=ActionKind.CREATE)
    def create(self) -> "Thread":
        if not self.id:
            self.id = _cuid()
        return self

    @action(kind=ActionKind.ACTION)
    def add_message(
        self,
        action_ctx: ActionContext,
        participant_id: str,
        participant_type: str,
        role: str,
        content: dict,
        idempotency_key: str,
        raw_response: dict | None = None,
        agent_id: str | None = None,
    ) -> Message:
        msg = Message(
            id=_cuid(),
            thread_id=self.id,
            participant_id=participant_id,
            participant_type=ParticipantType(participant_type),
            role=MessageRole(role),
            content=content,
            raw_response=raw_response,
            idempotency_key=idempotency_key,
            position=-1,  # assigned by derive/restate.py via ctx position counter
        )

        parts = content.get("parts", []) if isinstance(content, dict) else []

        # HITL: response_reply → resolve the named promise on the AgentRun workflow
        for part in parts:
            if part.get("type") == "response_reply":
                request_id = part.get("request_id")
                run_id = _find_run_id_for_request(self.id, request_id, self.tenant_id)
                if run_id:
                    action_ctx.send_workflow(
                        service="AgentRun",
                        key=run_id,
                        handler="resolve_hitl",
                        arg={
                            "request_id": request_id,
                            "thread_id": self.id,
                            "tenant_id": self.tenant_id,
                            "selected": part.get("selected", []),
                            "submitted_by": participant_id,
                        },
                    )

        is_response_reply = any(p.get("type") == "response_reply" for p in parts)

        # Trigger agent for inbound human messages
        if ParticipantType(participant_type) == ParticipantType.HUMAN and not is_response_reply:
            run_id = _cuid()
            if not agent_id:
                channel_id = resolve_channel_for_thread(self.id, self.tenant_id)
                agent_id = resolve_agent_for_channel(channel_id, self.tenant_id) if channel_id else "stub"
            action_ctx.send_workflow(
                service="AgentRun",
                key=run_id,
                arg={
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "thread_id": self.id,
                    "tenant_id": self.tenant_id,
                },
            )

        # Route all messages to channel for adapters to filter
        channel_id = resolve_channel_for_thread(self.id, self.tenant_id)
        if channel_id:
            action_ctx.send(
                service="ChannelDelivery",
                handler="deliver",
                key=None,
                arg={
                    "thread_id": self.id,
                    "channel_id": channel_id,
                    "tenant_id": self.tenant_id,
                    "message": {
                        "participant_id": participant_id,
                        "participant_type": participant_type,
                        "role": role,
                        "content": content,
                    },
                },
            )

        return msg

    @action(kind=ActionKind.READ)
    def get(self) -> "Thread":
        return self

    @action(kind=ActionKind.STREAM)
    def observe(self) -> "Thread":
        return self


# ── Domain helpers — pure DB reads, no Restate ────────────────────────────────

def _find_run_id_for_request(thread_id: str, request_id: str | None, tenant_id: str | None) -> str | None:
    if not request_id or not tenant_id:
        return None
    with tenant_session(tenant_id) as db:
        rows = db.execute(
            text("SELECT content, participant_id FROM messages WHERE thread_id = :tid ORDER BY position"),
            {"tid": thread_id},
        ).fetchall()
    for content_raw, participant_id in rows:
        content = content_raw if isinstance(content_raw, dict) else json.loads(content_raw or "{}")
        for part in content.get("parts", []):
            if part.get("type") == "response_request" and part.get("request_id") == request_id:
                if participant_id and participant_id.startswith("agent-run-"):
                    return participant_id[len("agent-run-"):]
    return None
