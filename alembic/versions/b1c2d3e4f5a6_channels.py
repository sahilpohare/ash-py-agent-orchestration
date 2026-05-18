"""channels and channel_bindings

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-18
"""

from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, server_default=sa.text("current_setting('app.tenant_id', true)")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("channel_type", sa.String, nullable=False),
        sa.Column("config", sa.JSON, nullable=True),
        sa.Column("default_agent_id", sa.String, nullable=False, server_default="stub"),
        sa.Column("status", sa.String, nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_channels_tenant_id", "channels", ["tenant_id"])

    op.create_table(
        "channel_bindings",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, server_default=sa.text("current_setting('app.tenant_id', true)")),
        sa.Column("thread_id", sa.String, nullable=False, unique=True, index=True),
        sa.Column("channel_id", sa.String, nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_channel_bindings_tenant_id", "channel_bindings", ["tenant_id"])

    for table in ("channels", "channel_bindings"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
        """)
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app")


def downgrade() -> None:
    for table in ("channels", "channel_bindings"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_table("channel_bindings")
    op.drop_table("channels")
