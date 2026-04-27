"""Add public_number column to agents table for legacy databases.

This migration adds the public_number column that was missing from
the original create_all-based schema. It is a no-op for fresh
databases created by the baseline migration (which already includes
the column).

Revision ID: 20260425_add_agent_public_number
Revises: 20260418_expand_agent_friends_contexts
Create Date: 2026-04-25 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260425_add_agent_public_number"
down_revision = "20260418_expand_agent_friends_contexts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    col_exists = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'agents' AND column_name = 'public_number'"
        )
    ).scalar()
    if col_exists:
        return
    op.add_column("agents", sa.Column("public_number", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE agents SET public_number = 10000000 + s.rn
        FROM (SELECT agent_id, ROW_NUMBER() OVER (ORDER BY created_at, agent_id) AS rn FROM agents WHERE public_number IS NULL) s
        WHERE agents.agent_id = s.agent_id AND agents.public_number IS NULL
        """
    )
    op.alter_column("agents", "public_number", nullable=False)
    op.create_unique_constraint("uq_agents_public_number", "agents", ["public_number"])
    op.create_index("idx_agents_public_number", "agents", ["public_number"])


def downgrade() -> None:
    conn = op.get_bind()
    col_exists = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'agents' AND column_name = 'public_number'"
        )
    ).scalar()
    if not col_exists:
        return
    op.drop_index("idx_agents_public_number", table_name="agents")
    op.drop_constraint("uq_agents_public_number", "agents", type_="unique")
    op.drop_column("agents", "public_number")