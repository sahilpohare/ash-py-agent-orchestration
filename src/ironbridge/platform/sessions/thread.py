from datetime import UTC, datetime

from cuid2 import cuid_wrapper
from pydantic import BaseModel
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ironbridge.platform.channels.channel import resolve_agent_for_channel
from ironbridge.platform.channels.channel_binding import resolve_channels_for_thread
from ironbridge.platform.sessions.message import Message, MessageRole, ParticipantType
from ironbridge.shared.db import tenant_session
from ironbridge.shared.framework import ActionContext, ActionKind, Resource, action


class MessageView(BaseModel):
    id: str
    participant_id: str
    participant_type: str
    role: str
    content: dict
    position: int


class ThreadView(BaseModel):
    id: str
    created_at: str | None
    updated_at: str | None
    messages: list[MessageView]


class ThreadSummary(BaseModel):
    id: str
    created_at: str | None
    updated_at: str | None


class ThreadListResult(BaseModel):
    threads: list[ThreadSummary]


class AddMessageRequest(BaseModel):
    participant_id: str
    participant_type: str
    role: str
    content: dict
    idempotency_key: str
    tenant_id: str
    user_name: str = ""
    agent_id: str | None = None

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
                channel_ids = resolve_channels_for_thread(self.id, self.tenant_id)
                agent_id = resolve_agent_for_channel(channel_ids[0], self.tenant_id) if channel_ids else "stub"
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

        # Fanout to all channels bound to this thread.
        # send_after defers arg construction until after position is assigned.
        _thread_id = self.id
        _tenant_id = self.tenant_id
        _participant_id = participant_id
        _participant_type = participant_type
        _role = role
        _content = content
        for _channel_id in resolve_channels_for_thread(self.id, self.tenant_id):
            def _deliver_arg(result: dict, cid: str = _channel_id) -> dict:
                return {
                    "thread_id": _thread_id,
                    "channel_id": cid,
                    "tenant_id": _tenant_id,
                    "message": {
                        "participant_id": _participant_id,
                        "participant_type": _participant_type,
                        "role": _role,
                        "content": _content,
                        "position": result.get("position"),
                    },
                }

            action_ctx.send_after(
                service="ChannelDelivery",
                handler="deliver",
                key=None,
                factory=_deliver_arg,
            )

        return msg

    @action(kind=ActionKind.READ)
    def get(self) -> ThreadView:
        return ThreadView(
            id=self.id,
            created_at=self.created_at.isoformat() if self.created_at else None,
            updated_at=self.updated_at.isoformat() if self.updated_at else None,
            messages=[
                MessageView(
                    id=m.id,
                    participant_id=m.participant_id,
                    participant_type=m.participant_type.value if hasattr(m.participant_type, "value") else m.participant_type,
                    role=m.role.value if hasattr(m.role, "value") else m.role,
                    content=m.content,
                    position=m.position,
                )
                for m in (self.messages or [])
            ],
        )

    @action(kind=ActionKind.READ)
    def list(self, action_ctx: ActionContext) -> ThreadListResult:
        # Use the session already open by the framework — no second connection.
        # RLS is already set on this session via tenant_session() in the handler.
        from ironbridge.shared.derive.repository import SqlAlchemyRepository
        repo = SqlAlchemyRepository(action_ctx.session, Thread)
        threads = sorted(repo.list(), key=lambda t: t.created_at or datetime.min, reverse=True)
        return ThreadListResult(
            threads=[
                ThreadSummary(
                    id=t.id,
                    created_at=t.created_at.isoformat() if t.created_at else None,
                    updated_at=t.updated_at.isoformat() if t.updated_at else None,
                )
                for t in threads
            ]
        )

    @action(kind=ActionKind.READ)
    def get_messages(self, limit: int = 200) -> list[dict]:
        """
        Return thread messages visible to the LLM — control parts excluded.
        Returns the most recent `limit` messages, ordered by position ascending.
        Filters: response_reply and event parts are control flow — not for LLM.
        """
        all_msgs = list(self.messages or [])
        candidates = all_msgs[-limit:] if len(all_msgs) > limit else all_msgs
        result = []
        for m in candidates:
            content = m.content if isinstance(m.content, dict) else {}
            parts = content.get("parts", [])
            if any(p.get("type") in ("response_reply", "event") for p in parts):
                continue
            result.append({
                "id": m.id,
                "participant_id": m.participant_id,
                "participant_type": m.participant_type.value if hasattr(m.participant_type, "value") else m.participant_type,
                "role": m.role.value if hasattr(m.role, "value") else m.role,
                "content": content,
                "position": m.position,
            })
        return result

    @action(kind=ActionKind.READ)
    def find_hitl_run_id(self, request_id: str) -> str | None:
        """
        Find the run_id that owns a HITL request_id by scanning thread messages.
        Returns the run_id extracted from the participant_id of the response_request
        message, or None if not found.
        """
        for m in (self.messages or []):
            content = m.content if isinstance(m.content, dict) else {}
            for part in content.get("parts", []):
                if part.get("type") == "response_request" and part.get("request_id") == request_id:
                    pid = m.participant_id or ""
                    if pid.startswith("agent-run-"):
                        return pid[len("agent-run-"):]
        return None

    @action(kind=ActionKind.STREAM)
    def observe(self) -> "Thread":
        return self


# ── Domain helpers — pure DB reads, no Restate ────────────────────────────────

def _find_run_id_for_request(thread_id: str, request_id: str | None, tenant_id: str | None) -> str | None:
    if not request_id or not tenant_id:
        return None
    from ironbridge.shared.derive.repository import SqlAlchemyRepository
    with tenant_session(tenant_id) as db:
        repo = SqlAlchemyRepository(db, Thread)
        instance = repo.find_by_id(thread_id)
        if instance is None:
            return None
        return instance.find_hitl_run_id(request_id=request_id)
