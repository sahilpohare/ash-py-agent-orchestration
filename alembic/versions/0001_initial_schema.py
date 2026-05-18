"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-17
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tenants ───────────────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slug", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)

    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, server_default=sa.text("current_setting('app.tenant_id', true)")),
        sa.Column("email", sa.String, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"])
    op.create_index("ix_users_email", "users", ["email"])

    # ── agents ────────────────────────────────────────────────────────────────
    op.create_table(
        "agents",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, server_default=sa.text("current_setting('app.tenant_id', true)")),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("instructions", sa.String, nullable=True),
        sa.Column("model", sa.String, nullable=False),
        sa.Column("tools", sa.JSON, nullable=True),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])

    # ── threads ───────────────────────────────────────────────────────────────
    op.create_table(
        "threads",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, server_default=sa.text("current_setting('app.tenant_id', true)")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_threads_tenant_id", "threads", ["tenant_id"])

    # ── messages ──────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("tenant_id", sa.String, nullable=False, server_default=sa.text("current_setting('app.tenant_id', true)")),
        sa.Column("thread_id", sa.String, sa.ForeignKey("threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant_id", sa.String, nullable=False),
        sa.Column("participant_type", sa.String, nullable=False),
        sa.Column("role", sa.String, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("raw_response", sa.JSON, nullable=True),
        sa.Column("position", sa.BigInteger, nullable=False),
        sa.Column("idempotency_key", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"])
    op.create_index("ix_messages_participant_id", "messages", ["participant_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_idempotency_key", "messages", ["idempotency_key"])
    op.create_unique_constraint("uq_messages_thread_idempotency", "messages", ["thread_id", "idempotency_key"])

    # ── RLS ───────────────────────────────────────────────────────────────────
    # Enable RLS on all tenant-scoped tables.
    # Policy: session GUC app.tenant_id must match the row's tenant_id.
    for table in ("users", "agents", "threads", "messages"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    for table in ("users", "agents", "threads", "messages"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_table("messages")
    op.drop_table("threads")
    op.drop_table("agents")
    op.drop_table("users")
    op.drop_table("tenants")
