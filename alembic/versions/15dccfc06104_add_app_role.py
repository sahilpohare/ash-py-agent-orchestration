"""add_app_role

Revision ID: 15dccfc06104
Revises: 4035fd03e234
Create Date: 2026-05-17 14:55:11.343309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '15dccfc06104'
down_revision: Union[str, Sequence[str], None] = '4035fd03e234'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create non-superuser app role — RLS applies to non-superusers.
    # Superusers bypass RLS even with FORCE ROW LEVEL SECURITY.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app') THEN
                CREATE ROLE app WITH LOGIN PASSWORD 'app';
            END IF;
        END
        $$
    """)
    for table in ("tenants", "users", "agents", "threads", "messages", "agent_run_events"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO app")
    op.execute("GRANT USAGE ON SCHEMA public TO app")
    # Ensure app role is subject to RLS — superusers and BYPASSRLS roles bypass it.
    op.execute("ALTER ROLE app NOSUPERUSER NOBYPASSRLS")


def downgrade() -> None:
    for table in ("tenants", "users", "agents", "threads", "messages", "agent_run_events"):
        op.execute(f"REVOKE ALL ON {table} FROM app")
    op.execute("DROP ROLE IF EXISTS app")
