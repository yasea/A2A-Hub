"""expand agent_friends with bilateral tenant and context fields

Revision ID: 20260418_expand_agent_friends_contexts
Revises: 20260416_add_agent_friends
Create Date: 2026-04-18 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "20260418_expand_agent_friends_contexts"
down_revision = "20260416_add_agent_friends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_friends", sa.Column("requester_tenant_id", sa.String(), nullable=True))
    op.add_column("agent_friends", sa.Column("target_tenant_id", sa.String(), nullable=True))
    op.add_column("agent_friends", sa.Column("requester_context_id", sa.String(), nullable=True))
    op.add_column("agent_friends", sa.Column("target_context_id", sa.String(), nullable=True))

    op.execute(
        """
        UPDATE agent_friends
        SET requester_tenant_id = tenant_id,
            target_tenant_id = tenant_id,
            requester_context_id = context_id,
            target_context_id = context_id
        """
    )

    op.alter_column("agent_friends", "requester_tenant_id", nullable=False)
    op.alter_column("agent_friends", "target_tenant_id", nullable=False)
    op.create_foreign_key(
        "fk_agent_friends_requester_tenant",
        "agent_friends",
        "tenants",
        ["requester_tenant_id"],
        ["tenant_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_agent_friends_target_tenant",
        "agent_friends",
        "tenants",
        ["target_tenant_id"],
        ["tenant_id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_friends_target_tenant", "agent_friends", type_="foreignkey")
    op.drop_constraint("fk_agent_friends_requester_tenant", "agent_friends", type_="foreignkey")
    op.drop_column("agent_friends", "target_context_id")
    op.drop_column("agent_friends", "requester_context_id")
    op.drop_column("agent_friends", "target_tenant_id")
    op.drop_column("agent_friends", "requester_tenant_id")
