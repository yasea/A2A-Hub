"""create agent_friends table

Revision ID: 20260416_add_agent_friends
Revises: 
Create Date: 2026-04-16 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260416_add_agent_friends'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'agent_friends',
        sa.Column('id', sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('tenant_id', sa.String(), sa.ForeignKey('tenants.tenant_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('requester_agent_id', sa.String(), nullable=False),
        sa.Column('target_agent_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='PENDING'),
        sa.Column('context_id', sa.String(), nullable=True),
        sa.Column('invite_token', sa.String(), nullable=True),
        sa.Column('message', sa.String(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
    )


def downgrade() -> None:
    op.drop_table('agent_friends')
