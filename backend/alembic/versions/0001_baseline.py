"""全量 baseline：创建所有核心表。

这是项目首次完整的 Alembic 迁移，涵盖 A2A Hub 全部核心业务表。
后续增量迁移将以此为基线。

Revision ID: 0001_baseline
Revises: 
Create Date: 2026-04-16 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tenants',
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='ACTIVE'),
        sa.Column('config_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('tenant_id'),
    )
    op.create_index('idx_tenants_status', 'tenants', ['status'])

    op.create_table('agents',
        sa.Column('agent_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('agent_type', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='ACTIVE'),
        sa.Column('capabilities', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('auth_scheme', sa.String(), nullable=True),
        sa.Column('config_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('agent_id'),
    )
    op.create_index('idx_agents_tenant', 'agents', ['tenant_id'])
    op.create_index('idx_agents_status', 'agents', ['status'])
    op.create_index('idx_agents_tenant_type', 'agents', ['tenant_id', 'agent_type'])

    op.create_table('contexts',
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('owner_user_id', sa.String(), nullable=True),
        sa.Column('source_channel', sa.String(), nullable=True),
        sa.Column('source_conversation_id', sa.String(), nullable=True),
        sa.Column('status', sa.String(), nullable=False, server_default='OPEN'),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_activity_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('context_id'),
    )
    op.create_index('idx_contexts_tenant', 'contexts', ['tenant_id'])
    op.create_index('idx_contexts_source', 'contexts', ['source_channel', 'source_conversation_id'])
    op.create_index('idx_contexts_status', 'contexts', ['tenant_id', 'status'])
    op.create_index('idx_contexts_activity', 'contexts', [sa.text('last_activity_at DESC')])

    op.create_table('context_participants',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('participant_type', sa.String(), nullable=False),
        sa.Column('participant_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=True),
        sa.Column('joined_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('context_id', 'participant_type', 'participant_id'),
    )
    op.create_index('idx_context_participants_context', 'context_participants', ['context_id'])

    op.create_table('rc_room_context_bindings',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('rc_room_id', sa.String(), nullable=False),
        sa.Column('rc_server_url', sa.String(), nullable=True),
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'rc_room_id'),
    )
    op.create_index('idx_rc_room_bindings_context', 'rc_room_context_bindings', ['context_id'])

    op.create_table('platform_users',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('role', sa.String(), nullable=False, server_default='member'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_platform_users_tenant', 'platform_users', ['tenant_id'])

    op.create_table('identity_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('source_system', sa.String(), nullable=False),
        sa.Column('external_user_id', sa.String(), nullable=False),
        sa.Column('platform_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('metadata', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['platform_user_id'], ['platform_users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_system', 'external_user_id'),
    )
    op.create_index('idx_identity_mappings_platform_user', 'identity_mappings', ['platform_user_id'])

    op.create_table('tasks',
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('parent_task_id', sa.String(), nullable=True),
        sa.Column('initiator_agent_id', sa.String(), nullable=True),
        sa.Column('target_agent_id', sa.String(), nullable=True),
        sa.Column('task_type', sa.String(), nullable=False, server_default='generic'),
        sa.Column('state', sa.String(), nullable=False, server_default='SUBMITTED'),
        sa.Column('priority', sa.String(), nullable=False, server_default='normal'),
        sa.Column('input_text', sa.String(), nullable=True),
        sa.Column('output_text', sa.String(), nullable=True),
        sa.Column('approval_required', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('external_ref', sa.String(), nullable=True),
        sa.Column('idempotency_key', sa.String(), nullable=True),
        sa.Column('source_system', sa.String(), nullable=True),
        sa.Column('source_message_id', sa.String(), nullable=True),
        sa.Column('trace_id', sa.String(), nullable=True),
        sa.Column('failure_reason', sa.String(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('completed_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['parent_task_id'], ['tasks.task_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['initiator_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['target_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('task_id'),
    )
    op.create_index('idx_tasks_context', 'tasks', ['context_id'])
    op.create_index('idx_tasks_tenant_state', 'tasks', ['tenant_id', 'state'])
    op.create_index('idx_tasks_target_state', 'tasks', ['target_agent_id', 'state'],
                     postgresql_where=sa.text('target_agent_id IS NOT NULL'))
    op.create_index('idx_tasks_parent', 'tasks', ['parent_task_id'],
                     postgresql_where=sa.text('parent_task_id IS NOT NULL'))
    op.create_index('idx_tasks_trace', 'tasks', ['trace_id'],
                     postgresql_where=sa.text('trace_id IS NOT NULL'))
    op.create_index('idx_tasks_created', 'tasks', [sa.text('created_at DESC')])
    op.create_index('idx_tasks_expires', 'tasks', ['expires_at'],
                     postgresql_where=sa.text('expires_at IS NOT NULL'))
    op.create_index('uq_tasks_idempotency', 'tasks', ['tenant_id', 'idempotency_key'],
                     postgresql_where=sa.text('idempotency_key IS NOT NULL'), unique=True)
    op.create_index('uq_tasks_source_message', 'tasks', ['tenant_id', 'source_system', 'source_message_id'],
                     postgresql_where=sa.text('source_system IS NOT NULL AND source_message_id IS NOT NULL'), unique=True)

    op.create_table('task_messages',
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('mime_type', sa.String(), nullable=False, server_default='text/plain'),
        sa.Column('content_text', sa.String(), nullable=True),
        sa.Column('content_json', postgresql.JSONB(), nullable=True),
        sa.Column('source_agent_id', sa.String(), nullable=True),
        sa.Column('source_message_id', sa.String(), nullable=True),
        sa.Column('seq_no', sa.BigInteger(), nullable=False),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['source_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('message_id'),
        sa.UniqueConstraint('task_id', 'seq_no'),
    )
    op.create_index('idx_task_messages_task_seq', 'task_messages', ['task_id', 'seq_no'])
    op.create_index('idx_task_messages_context_seq', 'task_messages', ['context_id', 'seq_no'])
    op.create_index('uq_task_messages_source', 'task_messages', ['source_agent_id', 'source_message_id'],
                     postgresql_where=sa.text('source_agent_id IS NOT NULL AND source_message_id IS NOT NULL'), unique=True)

    op.create_table('task_artifacts',
        sa.Column('artifact_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('artifact_type', sa.String(), nullable=False),
        sa.Column('storage_uri', sa.String(), nullable=False),
        sa.Column('mime_type', sa.String(), nullable=True),
        sa.Column('checksum', sa.String(), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('artifact_id'),
    )
    op.create_index('idx_artifacts_task', 'task_artifacts', ['task_id'])
    op.create_index('idx_artifacts_context', 'task_artifacts', ['context_id'])

    op.create_table('approvals',
        sa.Column('approval_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('context_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='PENDING'),
        sa.Column('approver_user_id', sa.String(), nullable=True),
        sa.Column('requested_by', sa.String(), nullable=True),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('decision_note', sa.String(), nullable=True),
        sa.Column('external_key', sa.String(), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('resolved_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('approval_id'),
    )
    op.create_index('idx_approvals_task', 'approvals', ['task_id', 'status'])
    op.create_index('idx_approvals_approver', 'approvals', ['approver_user_id', 'status'],
                     postgresql_where=sa.text('approver_user_id IS NOT NULL'))
    op.create_index('idx_approvals_tenant', 'approvals', ['tenant_id', 'status'])
    op.create_index('uq_approvals_external', 'approvals', ['task_id', 'external_key'],
                     postgresql_where=sa.text('external_key IS NOT NULL'), unique=True)

    op.create_table('deliveries',
        sa.Column('delivery_id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=True),
        sa.Column('target_channel', sa.String(), nullable=False),
        sa.Column('target_ref', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('payload', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('status', sa.String(), nullable=False, server_default='PENDING'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='8'),
        sa.Column('next_retry_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('trace_id', sa.String(), nullable=True),
        sa.Column('idempotency_key', sa.String(), nullable=True),
        sa.Column('dead_letter_reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('delivery_id'),
    )
    op.create_index('idx_deliveries_worker', 'deliveries', ['status', 'next_retry_at'],
                     postgresql_where=sa.text("status IN ('PENDING', 'FAILED')"))
    op.create_index('idx_deliveries_dlq', 'deliveries', ['status'],
                     postgresql_where=sa.text("status = 'DEAD'"))
    op.create_index('idx_deliveries_task', 'deliveries', ['task_id'],
                     postgresql_where=sa.text('task_id IS NOT NULL'))
    op.create_index('idx_deliveries_trace', 'deliveries', ['trace_id'],
                     postgresql_where=sa.text('trace_id IS NOT NULL'))
    op.create_index('uq_deliveries_idempotency', 'deliveries', ['tenant_id', 'idempotency_key'],
                     postgresql_where=sa.text('idempotency_key IS NOT NULL'), unique=True)

    op.create_table('webhook_nonces',
        sa.Column('nonce', sa.String(), nullable=False),
        sa.Column('source_system', sa.String(), nullable=False),
        sa.Column('expires_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('nonce'),
    )
    op.create_index('idx_webhook_nonces_expires', 'webhook_nonces', ['expires_at'])

    op.create_table('audit_logs',
        sa.Column('audit_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('actor_type', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('resource_type', sa.String(), nullable=False),
        sa.Column('resource_id', sa.String(), nullable=True),
        sa.Column('payload_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('trace_id', sa.String(), nullable=True),
        sa.Column('request_id', sa.String(), nullable=True),
        sa.Column('ip_address', postgresql.INET(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('audit_id'),
    )
    op.create_index('idx_audit_tenant_time', 'audit_logs', ['tenant_id', sa.text('created_at DESC')])
    op.create_index('idx_audit_resource', 'audit_logs', ['resource_type', 'resource_id'])
    op.create_index('idx_audit_trace', 'audit_logs', ['trace_id'],
                     postgresql_where=sa.text('trace_id IS NOT NULL'))
    op.create_index('idx_audit_action', 'audit_logs', ['action'])

    op.create_table('routing_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('match_expr', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('target_agent_id', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['target_agent_id'], ['agents.agent_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_routing_rules_tenant_priority', 'routing_rules', ['tenant_id', 'is_active', 'priority'])

    op.create_table('task_state_transitions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('from_state', sa.String(), nullable=True),
        sa.Column('to_state', sa.String(), nullable=False),
        sa.Column('reason', sa.String(), nullable=True),
        sa.Column('actor_type', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), nullable=True),
        sa.Column('trace_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_task_state_trans_task', 'task_state_transitions', ['task_id', 'created_at'])
    op.create_index('idx_task_state_trans_tenant', 'task_state_transitions', ['tenant_id', sa.text('created_at DESC')])
    op.create_index('idx_task_state_trans_trace', 'task_state_transitions', ['trace_id'],
                     postgresql_where=sa.text('trace_id IS NOT NULL'))

    op.create_table('task_route_hops',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('hop_seq', sa.Integer(), nullable=False),
        sa.Column('from_agent_id', sa.String(), nullable=True),
        sa.Column('to_agent_id', sa.String(), nullable=True),
        sa.Column('route_reason', sa.String(), nullable=True),
        sa.Column('matched_rule_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['from_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['to_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'hop_seq'),
    )
    op.create_index('idx_task_route_hops_task', 'task_route_hops', ['task_id', 'hop_seq'])
    op.create_index('idx_task_route_hops_tenant', 'task_route_hops', ['tenant_id'])

    op.create_table('metering_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), nullable=True),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('metric_name', sa.String(), nullable=False),
        sa.Column('metric_value', sa.Numeric(18, 4), nullable=False, server_default='0'),
        sa.Column('unit', sa.String(), nullable=False, server_default='count'),
        sa.Column('extra_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.task_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_metering_tenant_time', 'metering_events', ['tenant_id', sa.text('created_at DESC')])
    op.create_index('idx_metering_task', 'metering_events', ['task_id'],
                     postgresql_where=sa.text('task_id IS NOT NULL'))
    op.create_index('idx_metering_event_type', 'metering_events', ['tenant_id', 'event_type', sa.text('created_at DESC')])

    op.create_table('agent_link_error_events',
        sa.Column('error_id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=True),
        sa.Column('agent_id', sa.String(), nullable=True),
        sa.Column('source_side', sa.String(), nullable=False),
        sa.Column('stage', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False, server_default='runtime'),
        sa.Column('summary', sa.String(), nullable=False),
        sa.Column('detail', sa.String(), nullable=True),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('request_path', sa.String(), nullable=True),
        sa.Column('payload_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('error_id'),
    )

    op.create_table('service_publications',
        sa.Column('service_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('handler_agent_id', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('summary', sa.String(), nullable=True),
        sa.Column('visibility', sa.String(), nullable=False, server_default='listed'),
        sa.Column('contact_policy', sa.String(), nullable=False, server_default='auto_accept'),
        sa.Column('allow_agent_initiated_chat', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('status', sa.String(), nullable=False, server_default='ACTIVE'),
        sa.Column('tags', postgresql.JSONB(), nullable=False, server_default='[]'),
        sa.Column('capabilities_public', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['handler_agent_id'], ['agents.agent_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('service_id'),
    )
    op.create_index('idx_service_publications_tenant', 'service_publications', ['tenant_id'])
    op.create_index('idx_service_publications_status_visibility', 'service_publications', ['status', 'visibility'])

    op.create_table('service_threads',
        sa.Column('thread_id', sa.String(), nullable=False),
        sa.Column('service_id', sa.String(), nullable=False),
        sa.Column('consumer_tenant_id', sa.String(), nullable=False),
        sa.Column('provider_tenant_id', sa.String(), nullable=False),
        sa.Column('provider_context_id', sa.String(), nullable=False),
        sa.Column('initiator_agent_id', sa.String(), nullable=True),
        sa.Column('handler_agent_id', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False, server_default='OPEN'),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('last_activity_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['service_id'], ['service_publications.service_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['consumer_tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['provider_tenant_id'], ['tenants.tenant_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['provider_context_id'], ['contexts.context_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['initiator_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['handler_agent_id'], ['agents.agent_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('thread_id'),
    )
    op.create_index('idx_service_threads_consumer', 'service_threads', ['consumer_tenant_id', sa.text('created_at DESC')])
    op.create_index('idx_service_threads_provider', 'service_threads', ['provider_tenant_id', sa.text('created_at DESC')])
    op.create_index('idx_service_threads_service', 'service_threads', ['service_id', sa.text('created_at DESC')])

    op.create_table('service_thread_messages',
        sa.Column('message_id', sa.String(), nullable=False),
        sa.Column('thread_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('sender_tenant_id', sa.String(), nullable=True),
        sa.Column('sender_agent_id', sa.String(), nullable=True),
        sa.Column('linked_task_id', sa.String(), nullable=True),
        sa.Column('content_text', sa.String(), nullable=False),
        sa.Column('seq_no', sa.BigInteger(), nullable=False),
        sa.Column('metadata_json', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['thread_id'], ['service_threads.thread_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['sender_tenant_id'], ['tenants.tenant_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['sender_agent_id'], ['agents.agent_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['linked_task_id'], ['tasks.task_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('message_id'),
        sa.UniqueConstraint('thread_id', 'seq_no'),
    )
    op.create_index('idx_service_thread_messages_thread', 'service_thread_messages', ['thread_id', 'seq_no'])


def downgrade() -> None:
    op.drop_table('service_thread_messages')
    op.drop_table('service_threads')
    op.drop_table('service_publications')
    op.drop_table('agent_link_error_events')
    op.drop_table('metering_events')
    op.drop_table('task_route_hops')
    op.drop_table('task_state_transitions')
    op.drop_table('routing_rules')
    op.drop_table('audit_logs')
    op.drop_table('webhook_nonces')
    op.drop_table('deliveries')
    op.drop_table('approvals')
    op.drop_table('task_artifacts')
    op.drop_table('task_messages')
    op.drop_table('tasks')
    op.drop_table('identity_mappings')
    op.drop_table('platform_users')
    op.drop_table('rc_room_context_bindings')
    op.drop_table('context_participants')
    op.drop_table('contexts')
    op.drop_table('agents')
    op.drop_table('tenants')