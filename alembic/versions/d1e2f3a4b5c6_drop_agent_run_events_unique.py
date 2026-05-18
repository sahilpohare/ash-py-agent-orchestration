"""drop uq_agent_run_events_run_event — log table, not deduplicated

Revision ID: d1e2f3a4b5c6
Revises: c2d3e4f5a6b7
Create Date: 2026-05-18 00:00:00.000000

agent_run_events is an append log. Multiple FAILED events for the same run
are valid (e.g. orphaned HITL resolve + workflow failure). The unique constraint
on (run_id, event_type) caused a DB error on the second write.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_agent_run_events_run_event", "agent_run_events", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_agent_run_events_run_event",
        "agent_run_events",
        ["run_id", "event_type"],
    )
