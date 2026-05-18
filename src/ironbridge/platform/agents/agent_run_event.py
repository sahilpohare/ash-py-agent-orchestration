from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from ironbridge.shared.framework.resource import Resource

_utcnow = lambda: datetime.now(UTC)  # noqa: E731


class AgentRunEvent(Resource):
    """
    Lifecycle events for agent runs (RUNNING, COMPLETED, CANCELLED, FAILED).
    Tenant-scoped via ResourceMeta — tenant_id injected and resolved from session GUC.
    """

    class Meta:
        tenant_scoped = True

    __tablename__ = "agent_run_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
