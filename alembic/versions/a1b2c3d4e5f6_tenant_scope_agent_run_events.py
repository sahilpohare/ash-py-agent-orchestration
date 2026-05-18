"""tenant scope agent_run_events

Revision ID: a1b2c3d4e5f6
Revises: 4035fd03e234
Create Date: 2026-05-17 20:00:00.000000

Adds tenant_id column and RLS to agent_run_events.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "15dccfc06104"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_run_events",
        sa.Column(
            "tenant_id",
            sa.String(),
            nullable=False,
            server_default=sa.text("current_setting('app.tenant_id', true)"),
        ),
    )
    op.create_index("ix_agent_run_events_tenant_id", "agent_run_events", ["tenant_id"])

    op.execute("ALTER TABLE agent_run_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_run_events FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON agent_run_events
            USING (tenant_id = current_setting('app.tenant_id', true))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_run_events")
    op.execute("ALTER TABLE agent_run_events DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_agent_run_events_tenant_id", table_name="agent_run_events")
    op.drop_column("agent_run_events", "tenant_id")
