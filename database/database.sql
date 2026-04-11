-- A2A Hub — PostgreSQL Schema (v2, 生产级)
-- 参考: docs/A2A_Hub_v2_完整技术方案.md
-- 目标: PostgreSQL 12+ (需启用 pgcrypto 扩展)
-- 数据库连接:
--   host: 127.0.0.1:5432
--   database: a2a_hub
--   user: rzplan / 1E%q1v9rPDyG
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- 工具函数: 自动维护 updated_at（必须在所有表之前定义）
-- =============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- tenants — 租户主表（所有业务表的 tenant_id 外键来源）
-- =============================================================================

CREATE TABLE tenants (
    tenant_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'SUSPENDED', 'CLOSED')),
    config_json JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE tenants IS '租户主表；所有业务表的 tenant_id 均引用此表';
COMMENT ON COLUMN tenants.tenant_id   IS '租户唯一标识';
COMMENT ON COLUMN tenants.name        IS '租户名称';
COMMENT ON COLUMN tenants.status      IS 'ACTIVE/SUSPENDED/CLOSED';
COMMENT ON COLUMN tenants.config_json IS '租户级配置（限流策略、功能开关等）';

CREATE INDEX idx_tenants_status ON tenants (status);

CREATE TRIGGER tr_tenants_updated_at
    BEFORE UPDATE ON tenants FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- agents — 注册的 Agent 实体
-- =============================================================================

CREATE TABLE agents (
    agent_id        TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    agent_type      TEXT NOT NULL CHECK (agent_type IN ('native', 'federated', 'bridged')),
    display_name    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
    capabilities    JSONB NOT NULL DEFAULT '{}',
    auth_scheme     TEXT,
    config_json     JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE agents IS '可路由的 Agent 实体';
COMMENT ON COLUMN agents.agent_id     IS '业务主键，如 openclaw:ava';
COMMENT ON COLUMN agents.tenant_id    IS '所属租户';
COMMENT ON COLUMN agents.agent_type   IS 'native=自研, federated=官方API接入, bridged=渠道桥接';
COMMENT ON COLUMN agents.display_name IS '展示名称';
COMMENT ON COLUMN agents.status       IS 'ACTIVE/INACTIVE/SUSPENDED';
COMMENT ON COLUMN agents.capabilities IS '技能/能力声明 JSON';
COMMENT ON COLUMN agents.auth_scheme  IS '认证方式: jwt/apikey/oauth2';
COMMENT ON COLUMN agents.config_json  IS '平台特定配置（base_url、token 等）';

CREATE INDEX idx_agents_tenant      ON agents (tenant_id);
CREATE INDEX idx_agents_status      ON agents (status);
CREATE INDEX idx_agents_tenant_type ON agents (tenant_id, agent_type);

CREATE TRIGGER tr_agents_updated_at
    BEFORE UPDATE ON agents FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- contexts — 跨平台业务会话容器
-- =============================================================================

CREATE TABLE contexts (
    context_id              TEXT PRIMARY KEY,
    tenant_id               TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    owner_user_id           TEXT,
    source_channel          TEXT,
    source_conversation_id  TEXT,
    status                  TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'ARCHIVED')),
    title                   TEXT,
    metadata_json           JSONB NOT NULL DEFAULT '{}',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE contexts IS '业务会话容器；一个 RC room 对应一个 context';
COMMENT ON COLUMN contexts.context_id             IS '会话唯一标识';
COMMENT ON COLUMN contexts.tenant_id              IS '所属租户';
COMMENT ON COLUMN contexts.owner_user_id          IS '发起人 platform_user_id';
COMMENT ON COLUMN contexts.source_channel         IS '来源渠道: rocket_chat/openclaw/api';
COMMENT ON COLUMN contexts.source_conversation_id IS '外部会话ID（RC room_id / OC session_key）';
COMMENT ON COLUMN contexts.status                 IS 'OPEN/CLOSED/ARCHIVED';
COMMENT ON COLUMN contexts.last_activity_at       IS '最近活跃时间，用于超时清理';

CREATE INDEX idx_contexts_tenant   ON contexts (tenant_id);
CREATE INDEX idx_contexts_source   ON contexts (source_channel, source_conversation_id);
CREATE INDEX idx_contexts_status   ON contexts (tenant_id, status);
CREATE INDEX idx_contexts_activity ON contexts (last_activity_at DESC);

CREATE TRIGGER tr_contexts_updated_at
    BEFORE UPDATE ON contexts FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- context_participants — context 参与者
-- =============================================================================

CREATE TABLE context_participants (
    id               BIGSERIAL PRIMARY KEY,
    context_id       TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    participant_type TEXT NOT NULL CHECK (participant_type IN ('user', 'agent', 'system')),
    participant_id   TEXT NOT NULL,
    role             TEXT,
    joined_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (context_id, participant_type, participant_id)
);

COMMENT ON TABLE context_participants IS 'context 参与者列表';
COMMENT ON COLUMN context_participants.participant_type IS 'user/agent/system';
COMMENT ON COLUMN context_participants.participant_id   IS 'platform_user_id 或 agent_id';
COMMENT ON COLUMN context_participants.role             IS 'owner/member/observer';

CREATE INDEX idx_context_participants_context ON context_participants (context_id);

-- =============================================================================
-- rc_room_context_bindings — Rocket.Chat room <-> context 映射
-- =============================================================================

CREATE TABLE rc_room_context_bindings (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    rc_room_id    TEXT NOT NULL,
    rc_server_url TEXT,
    context_id    TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, rc_room_id)
);

COMMENT ON TABLE rc_room_context_bindings IS 'RC room 与 Hub context 绑定（一对一）';
COMMENT ON COLUMN rc_room_context_bindings.rc_room_id    IS 'Rocket.Chat 房间 ID';
COMMENT ON COLUMN rc_room_context_bindings.rc_server_url IS 'RC 服务器地址（多实例场景）';

CREATE INDEX idx_rc_room_bindings_context ON rc_room_context_bindings (context_id);

-- =============================================================================
-- platform_users — 平台内部用户
-- =============================================================================

CREATE TABLE platform_users (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    display_name TEXT,
    email        TEXT,
    role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member', 'approver', 'observer')),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE platform_users IS '平台内部用户身份';
COMMENT ON COLUMN platform_users.tenant_id IS '所属租户';
COMMENT ON COLUMN platform_users.email     IS '用户邮箱（可选）';
COMMENT ON COLUMN platform_users.role      IS '角色: admin/member/approver/observer';
COMMENT ON COLUMN platform_users.is_active IS '是否启用';

CREATE INDEX idx_platform_users_tenant ON platform_users (tenant_id);

CREATE TRIGGER tr_platform_users_updated_at
    BEFORE UPDATE ON platform_users FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- identity_mappings — 外部平台用户 ID 映射
-- =============================================================================

CREATE TABLE identity_mappings (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_system    TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    platform_user_id UUID NOT NULL REFERENCES platform_users (id) ON DELETE CASCADE,
    metadata         JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_system, external_user_id)
);

COMMENT ON TABLE identity_mappings IS '外部平台用户 ID 映射到内部 platform_user';
COMMENT ON COLUMN identity_mappings.source_system    IS '来源系统: rocket_chat/openclaw 等';
COMMENT ON COLUMN identity_mappings.external_user_id IS '外部平台的用户 ID';
COMMENT ON COLUMN identity_mappings.platform_user_id IS '对应的内部用户 ID';

CREATE INDEX idx_identity_mappings_platform_user ON identity_mappings (platform_user_id);

-- =============================================================================
-- tasks — 系统第一公民
-- =============================================================================

CREATE TABLE tasks (
    task_id            TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    context_id         TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    parent_task_id     TEXT REFERENCES tasks (task_id) ON DELETE SET NULL,
    initiator_agent_id TEXT REFERENCES agents (agent_id) ON DELETE SET NULL,
    target_agent_id    TEXT REFERENCES agents (agent_id) ON DELETE SET NULL,
    task_type          TEXT NOT NULL DEFAULT 'generic',
    state              TEXT NOT NULL DEFAULT 'SUBMITTED'
        CHECK (state IN (
            'SUBMITTED', 'ROUTING', 'WORKING', 'WAITING_EXTERNAL',
            'AUTH_REQUIRED', 'COMPLETED', 'FAILED', 'CANCELED', 'EXPIRED'
        )),
    priority           TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    input_text         TEXT,
    output_text        TEXT,
    approval_required  BOOLEAN NOT NULL DEFAULT FALSE,
    external_ref       TEXT,
    idempotency_key    TEXT,
    source_system      TEXT,
    source_message_id  TEXT,
    trace_id           TEXT,
    failure_reason     TEXT,
    retry_count        INT NOT NULL DEFAULT 0,
    expires_at         TIMESTAMPTZ,
    metadata_json      JSONB NOT NULL DEFAULT '{}',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ
);

COMMENT ON TABLE tasks IS '任务状态机核心表；所有跨平台代理动作必须落成 Task';
COMMENT ON COLUMN tasks.task_id            IS '任务唯一标识';
COMMENT ON COLUMN tasks.tenant_id          IS '所属租户';
COMMENT ON COLUMN tasks.context_id         IS '所属会话';
COMMENT ON COLUMN tasks.parent_task_id     IS '父任务 ID（子任务场景）';
COMMENT ON COLUMN tasks.initiator_agent_id IS '发起方 Agent';
COMMENT ON COLUMN tasks.target_agent_id    IS '目标 Agent';
COMMENT ON COLUMN tasks.task_type          IS '任务类型标签，如 generic/analysis/quote';
COMMENT ON COLUMN tasks.state              IS '状态机: SUBMITTED→ROUTING→WORKING→COMPLETED/FAILED/CANCELED/EXPIRED';
COMMENT ON COLUMN tasks.priority           IS '优先级: low/normal/high/urgent';
COMMENT ON COLUMN tasks.input_text         IS '任务输入文本';
COMMENT ON COLUMN tasks.output_text        IS '任务输出结果';
COMMENT ON COLUMN tasks.approval_required  IS '是否需要人工审批';
COMMENT ON COLUMN tasks.external_ref       IS '外部平台任务 ID';
COMMENT ON COLUMN tasks.idempotency_key    IS '幂等键，防重复提交';
COMMENT ON COLUMN tasks.source_system      IS '入站来源系统（去重用）';
COMMENT ON COLUMN tasks.source_message_id  IS '入站消息 ID（去重用）';
COMMENT ON COLUMN tasks.trace_id           IS '链路追踪 ID';
COMMENT ON COLUMN tasks.failure_reason     IS '失败原因描述';
COMMENT ON COLUMN tasks.retry_count        IS '已重试次数';
COMMENT ON COLUMN tasks.expires_at         IS '任务过期时间';

CREATE INDEX idx_tasks_context      ON tasks (context_id);
CREATE INDEX idx_tasks_tenant_state ON tasks (tenant_id, state);
CREATE INDEX idx_tasks_target_state ON tasks (target_agent_id, state) WHERE target_agent_id IS NOT NULL;
CREATE INDEX idx_tasks_parent       ON tasks (parent_task_id) WHERE parent_task_id IS NOT NULL;
CREATE INDEX idx_tasks_trace        ON tasks (trace_id) WHERE trace_id IS NOT NULL;
CREATE INDEX idx_tasks_created      ON tasks (created_at DESC);
CREATE INDEX idx_tasks_expires      ON tasks (expires_at) WHERE expires_at IS NOT NULL;

CREATE UNIQUE INDEX uq_tasks_idempotency
    ON tasks (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX uq_tasks_source_message
    ON tasks (tenant_id, source_system, source_message_id)
    WHERE source_system IS NOT NULL AND source_message_id IS NOT NULL;

CREATE TRIGGER tr_tasks_updated_at
    BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- task_messages — 任务消息流水
-- =============================================================================

CREATE TABLE task_messages (
    message_id        TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL REFERENCES tasks (task_id) ON DELETE CASCADE,
    context_id        TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    role              TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    mime_type         TEXT NOT NULL DEFAULT 'text/plain',
    content_text      TEXT,
    content_json      JSONB,
    source_agent_id   TEXT REFERENCES agents (agent_id) ON DELETE SET NULL,
    source_message_id TEXT,
    seq_no            BIGINT NOT NULL,
    metadata_json     JSONB NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, seq_no)
);

COMMENT ON TABLE task_messages IS '任务消息流水；seq_no 保证顺序和回放';
COMMENT ON COLUMN task_messages.message_id        IS '消息唯一标识';
COMMENT ON COLUMN task_messages.task_id           IS '所属任务';
COMMENT ON COLUMN task_messages.context_id        IS '所属会话';
COMMENT ON COLUMN task_messages.role              IS '消息角色: user/assistant/system/tool';
COMMENT ON COLUMN task_messages.mime_type         IS '内容类型: text/plain, application/json, text/markdown';
COMMENT ON COLUMN task_messages.content_text      IS '文本内容';
COMMENT ON COLUMN task_messages.content_json      IS 'JSON 结构化内容';
COMMENT ON COLUMN task_messages.source_agent_id   IS '来源 Agent（外部消息去重用）';
COMMENT ON COLUMN task_messages.source_message_id IS '外部平台消息 ID（去重用）';
COMMENT ON COLUMN task_messages.seq_no            IS '消息序号，同 task 内单调递增';

CREATE INDEX idx_task_messages_task_seq    ON task_messages (task_id, seq_no);
CREATE INDEX idx_task_messages_context_seq ON task_messages (context_id, seq_no);

CREATE UNIQUE INDEX uq_task_messages_source
    ON task_messages (source_agent_id, source_message_id)
    WHERE source_agent_id IS NOT NULL AND source_message_id IS NOT NULL;

-- =============================================================================
-- task_artifacts — 任务结构化产出
-- =============================================================================

CREATE TABLE task_artifacts (
    artifact_id   TEXT PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    task_id       TEXT NOT NULL REFERENCES tasks (task_id) ON DELETE CASCADE,
    context_id    TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    storage_uri   TEXT NOT NULL,
    mime_type     TEXT,
    checksum      TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE task_artifacts IS '任务结构化产出（文档/报价/图片等）';
COMMENT ON COLUMN task_artifacts.tenant_id     IS '所属租户';
COMMENT ON COLUMN task_artifacts.artifact_type IS '产出类型: document/quote/order/image/json';
COMMENT ON COLUMN task_artifacts.storage_uri   IS 'S3/MinIO 存储路径';
COMMENT ON COLUMN task_artifacts.checksum      IS '文件校验和（SHA256）';

CREATE INDEX idx_artifacts_task    ON task_artifacts (task_id);
CREATE INDEX idx_artifacts_context ON task_artifacts (context_id);

-- =============================================================================
-- approvals — 人工审批门控
-- =============================================================================

CREATE TABLE approvals (
    approval_id      TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    task_id          TEXT NOT NULL REFERENCES tasks (task_id) ON DELETE CASCADE,
    context_id       TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'CANCELED')),
    approver_user_id TEXT,
    requested_by     TEXT,
    reason           TEXT,
    decision_note    TEXT,
    external_key     TEXT,
    metadata_json    JSONB NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at      TIMESTAMPTZ
);

COMMENT ON TABLE approvals IS '人工审批记录；task 进入 AUTH_REQUIRED 时创建';
COMMENT ON COLUMN approvals.tenant_id        IS '所属租户';
COMMENT ON COLUMN approvals.status           IS 'PENDING/APPROVED/REJECTED/EXPIRED/CANCELED';
COMMENT ON COLUMN approvals.approver_user_id IS '指定审批人 platform_user_id';
COMMENT ON COLUMN approvals.requested_by     IS '发起审批的 agent_id 或 user_id';
COMMENT ON COLUMN approvals.reason           IS '审批原因说明';
COMMENT ON COLUMN approvals.decision_note    IS '审批人决策备注';
COMMENT ON COLUMN approvals.external_key     IS '外部平台审批 ID（如 OC approval key）';
COMMENT ON COLUMN approvals.resolved_at      IS '审批完成时间';

CREATE INDEX idx_approvals_task     ON approvals (task_id, status);
CREATE INDEX idx_approvals_approver ON approvals (approver_user_id, status) WHERE approver_user_id IS NOT NULL;
CREATE INDEX idx_approvals_tenant   ON approvals (tenant_id, status);

CREATE UNIQUE INDEX uq_approvals_external
    ON approvals (task_id, external_key)
    WHERE external_key IS NOT NULL;

-- =============================================================================
-- deliveries — 出站投递、重试与 DLQ
-- =============================================================================

CREATE TABLE deliveries (
    delivery_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    task_id            TEXT REFERENCES tasks (task_id) ON DELETE SET NULL,
    target_channel     TEXT NOT NULL
        CHECK (target_channel IN ('rocket_chat', 'openclaw', 'workbuddy', 'webhook', 'telegram', 'email', 'other')),
    target_ref         JSONB NOT NULL DEFAULT '{}',
    payload            JSONB NOT NULL DEFAULT '{}',
    status             TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'SENDING', 'DELIVERED', 'FAILED', 'DEAD')),
    attempt_count      INT NOT NULL DEFAULT 0,
    max_attempts       INT NOT NULL DEFAULT 8,
    next_retry_at      TIMESTAMPTZ,
    last_error         TEXT,
    trace_id           TEXT,
    idempotency_key    TEXT,
    dead_letter_reason TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE deliveries IS '出站投递队列；指数退避重试；status=DEAD 为 DLQ';
COMMENT ON COLUMN deliveries.tenant_id          IS '所属租户';
COMMENT ON COLUMN deliveries.target_channel     IS '投递渠道: rocket_chat/openclaw/webhook 等';
COMMENT ON COLUMN deliveries.target_ref         IS '投递目标详情（room_id、url 等）';
COMMENT ON COLUMN deliveries.payload            IS '投递内容';
COMMENT ON COLUMN deliveries.status             IS 'PENDING/SENDING/DELIVERED/FAILED/DEAD';
COMMENT ON COLUMN deliveries.attempt_count      IS '已尝试次数';
COMMENT ON COLUMN deliveries.max_attempts       IS '最大重试次数，默认 8';
COMMENT ON COLUMN deliveries.next_retry_at      IS '下次重试时间（指数退避）';
COMMENT ON COLUMN deliveries.last_error         IS '最近一次错误信息';
COMMENT ON COLUMN deliveries.dead_letter_reason IS '进入 DLQ 的原因';

CREATE INDEX idx_deliveries_worker
    ON deliveries (status, next_retry_at)
    WHERE status IN ('PENDING', 'FAILED');

CREATE INDEX idx_deliveries_dlq   ON deliveries (status) WHERE status = 'DEAD';
CREATE INDEX idx_deliveries_task  ON deliveries (task_id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_deliveries_trace ON deliveries (trace_id) WHERE trace_id IS NOT NULL;

CREATE UNIQUE INDEX uq_deliveries_idempotency
    ON deliveries (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TRIGGER tr_deliveries_updated_at
    BEFORE UPDATE ON deliveries FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

CREATE UNIQUE INDEX uq_deliveries_idempotency
    ON deliveries (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- =============================================================================
-- webhook_nonces — Webhook 重放攻击防护
-- =============================================================================

CREATE TABLE webhook_nonces (
    nonce         TEXT PRIMARY KEY,
    source_system TEXT NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE webhook_nonces IS 'HMAC + 时间戳 + nonce 三重防重放';
COMMENT ON COLUMN webhook_nonces.nonce         IS '随机唯一值，TTL 内拒绝重复';
COMMENT ON COLUMN webhook_nonces.source_system IS '来源系统标识';
COMMENT ON COLUMN webhook_nonces.expires_at    IS 'nonce 过期时间';

CREATE INDEX idx_webhook_nonces_expires ON webhook_nonces (expires_at);

-- =============================================================================
-- audit_logs — 不可变操作审计日志
-- =============================================================================

CREATE TABLE audit_logs (
    audit_id      BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    actor_type    TEXT NOT NULL CHECK (actor_type IN ('user', 'system', 'webhook', 'worker', 'agent')),
    actor_id      TEXT,
    action        TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id   TEXT,
    payload_json  JSONB NOT NULL DEFAULT '{}',
    trace_id      TEXT,
    request_id    TEXT,
    ip_address    INET,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE audit_logs IS '不可变审计日志；覆盖所有关键写动作';
COMMENT ON COLUMN audit_logs.tenant_id     IS '所属租户';
COMMENT ON COLUMN audit_logs.actor_type    IS '操作者类型: user/system/webhook/worker/agent';
COMMENT ON COLUMN audit_logs.actor_id      IS '操作者 ID';
COMMENT ON COLUMN audit_logs.action        IS '操作名称，如 task.create/approval.resolve';
COMMENT ON COLUMN audit_logs.resource_type IS '资源类型，如 task/approval/delivery';
COMMENT ON COLUMN audit_logs.resource_id   IS '资源 ID（TEXT 兼容各类 ID 格式）';
COMMENT ON COLUMN audit_logs.payload_json  IS '操作详情快照';
COMMENT ON COLUMN audit_logs.trace_id      IS '链路追踪 ID';
COMMENT ON COLUMN audit_logs.request_id    IS '请求 ID';
COMMENT ON COLUMN audit_logs.ip_address    IS '来源 IP';

CREATE INDEX idx_audit_tenant_time ON audit_logs (tenant_id, created_at DESC);
CREATE INDEX idx_audit_resource    ON audit_logs (resource_type, resource_id);
CREATE INDEX idx_audit_trace       ON audit_logs (trace_id) WHERE trace_id IS NOT NULL;
CREATE INDEX idx_audit_action      ON audit_logs (action);

-- =============================================================================
-- routing_rules — 策略路由规则
-- =============================================================================

CREATE TABLE routing_rules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    name            TEXT NOT NULL,
    priority        INT NOT NULL DEFAULT 100,
    match_expr      JSONB NOT NULL DEFAULT '{}',
    target_agent_id TEXT NOT NULL REFERENCES agents (agent_id) ON DELETE CASCADE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE routing_rules IS '策略路由规则；priority 越小越优先';
COMMENT ON COLUMN routing_rules.tenant_id       IS '所属租户';
COMMENT ON COLUMN routing_rules.priority        IS '优先级，数值越小越优先';
COMMENT ON COLUMN routing_rules.match_expr      IS '匹配条件 JSON（task_type/source_channel 等）';
COMMENT ON COLUMN routing_rules.target_agent_id IS '命中后路由到的目标 Agent';
COMMENT ON COLUMN routing_rules.is_active       IS '是否启用';

CREATE INDEX idx_routing_rules_tenant_priority ON routing_rules (tenant_id, is_active, priority);

CREATE TRIGGER tr_routing_rules_updated_at
    BEFORE UPDATE ON routing_rules FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- quotes — 商务报价单
-- =============================================================================

CREATE TABLE quotes (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    context_id   TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    task_id      TEXT REFERENCES tasks (task_id) ON DELETE SET NULL,
    status       TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'pending_approval', 'approved', 'rejected', 'expired', 'superseded')),
    currency     TEXT NOT NULL DEFAULT 'CNY',
    total_amount NUMERIC(18, 4),
    line_items   JSONB NOT NULL DEFAULT '[]',
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE quotes IS '结构化商务报价单';
COMMENT ON COLUMN quotes.tenant_id    IS '所属租户';
COMMENT ON COLUMN quotes.status       IS 'draft/pending_approval/approved/rejected/expired/superseded';
COMMENT ON COLUMN quotes.currency     IS '货币单位，默认 CNY';
COMMENT ON COLUMN quotes.total_amount IS '报价总金额';
COMMENT ON COLUMN quotes.line_items   IS '报价明细 JSON 数组';

CREATE INDEX idx_quotes_context ON quotes (context_id);
CREATE INDEX idx_quotes_task    ON quotes (task_id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_quotes_status  ON quotes (tenant_id, status);

CREATE TRIGGER tr_quotes_updated_at
    BEFORE UPDATE ON quotes FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- orders — 订单
-- =============================================================================

CREATE TABLE orders (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    quote_id     UUID REFERENCES quotes (id) ON DELETE SET NULL,
    context_id   TEXT NOT NULL REFERENCES contexts (context_id) ON DELETE CASCADE,
    task_id      TEXT REFERENCES tasks (task_id) ON DELETE SET NULL,
    status       TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'confirmed', 'fulfilling', 'completed', 'cancelled', 'failed')),
    currency     TEXT NOT NULL DEFAULT 'CNY',
    total_amount NUMERIC(18, 4),
    line_items   JSONB NOT NULL DEFAULT '[]',
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE orders IS '订单生命周期；由报价审批通过后创建';
COMMENT ON COLUMN orders.tenant_id    IS '所属租户';
COMMENT ON COLUMN orders.quote_id     IS '来源报价单 ID';
COMMENT ON COLUMN orders.status       IS 'created/confirmed/fulfilling/completed/cancelled/failed';
COMMENT ON COLUMN orders.currency     IS '货币单位，默认 CNY';
COMMENT ON COLUMN orders.total_amount IS '订单总金额';
COMMENT ON COLUMN orders.line_items   IS '订单明细 JSON 数组';

CREATE INDEX idx_orders_context ON orders (context_id);
CREATE INDEX idx_orders_quote   ON orders (quote_id) WHERE quote_id IS NOT NULL;
CREATE INDEX idx_orders_status  ON orders (tenant_id, status);

CREATE TRIGGER tr_orders_updated_at
    BEFORE UPDATE ON orders FOR EACH ROW EXECUTE PROCEDURE set_updated_at();

-- =============================================================================
-- task_state_transitions — 任务状态变更流水（状态机审计专表）
-- =============================================================================

CREATE TABLE task_state_transitions (
    id          BIGSERIAL PRIMARY KEY,
    task_id     TEXT NOT NULL REFERENCES tasks (task_id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    from_state  TEXT,                                          -- NULL 表示初始创建
    to_state    TEXT NOT NULL,
    reason      TEXT,                                          -- 变更原因
    actor_type  TEXT NOT NULL CHECK (actor_type IN ('user', 'system', 'agent', 'worker')),
    actor_id    TEXT,
    trace_id    TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE task_state_transitions IS '任务状态变更流水；支持状态机回放、非法跳转审查';
COMMENT ON COLUMN task_state_transitions.from_state IS '变更前状态；NULL 表示初始创建';
COMMENT ON COLUMN task_state_transitions.to_state   IS '变更后状态';
COMMENT ON COLUMN task_state_transitions.reason     IS '变更原因说明';
COMMENT ON COLUMN task_state_transitions.actor_type IS '触发变更的主体类型';
COMMENT ON COLUMN task_state_transitions.actor_id   IS '触发变更的主体 ID';

CREATE INDEX idx_task_state_trans_task    ON task_state_transitions (task_id, created_at);
CREATE INDEX idx_task_state_trans_tenant  ON task_state_transitions (tenant_id, created_at DESC);
CREATE INDEX idx_task_state_trans_trace   ON task_state_transitions (trace_id) WHERE trace_id IS NOT NULL;

-- =============================================================================
-- task_route_hops — 路由跳转记录（防循环、跳数限制）
-- =============================================================================

CREATE TABLE task_route_hops (
    id              BIGSERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL REFERENCES tasks (task_id) ON DELETE CASCADE,
    tenant_id       TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    hop_seq         INT NOT NULL,                              -- 跳转序号，从 1 开始
    from_agent_id   TEXT REFERENCES agents (agent_id) ON DELETE SET NULL,
    to_agent_id     TEXT REFERENCES agents (agent_id) ON DELETE SET NULL,
    route_reason    TEXT,                                      -- 路由选择原因
    matched_rule_id UUID,                                      -- 命中的 routing_rule id（可选）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id, hop_seq)
);

COMMENT ON TABLE task_route_hops IS '任务路由跳转记录；用于防循环检测和最大跳数限制';
COMMENT ON COLUMN task_route_hops.hop_seq       IS '跳转序号，同一 task 内单调递增，默认上限 3';
COMMENT ON COLUMN task_route_hops.from_agent_id IS '路由来源 Agent';
COMMENT ON COLUMN task_route_hops.to_agent_id   IS '路由目标 Agent';
COMMENT ON COLUMN task_route_hops.route_reason  IS '路由选择原因（skill匹配/policy/fallback）';
COMMENT ON COLUMN task_route_hops.matched_rule_id IS '命中的路由规则 ID';

CREATE INDEX idx_task_route_hops_task   ON task_route_hops (task_id, hop_seq);
CREATE INDEX idx_task_route_hops_tenant ON task_route_hops (tenant_id);

-- =============================================================================
-- metering_events — 用量计量流水（为后续计费准备）
-- =============================================================================

CREATE TABLE metering_events (
    id              BIGSERIAL PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants (tenant_id) ON DELETE RESTRICT,
    task_id         TEXT REFERENCES tasks (task_id) ON DELETE SET NULL,
    agent_id        TEXT REFERENCES agents (agent_id) ON DELETE SET NULL,
    event_type      TEXT NOT NULL,                             -- 'llm_call', 'api_call', 'delivery', 'storage'
    metric_name     TEXT NOT NULL,                             -- 'token_input', 'token_output', 'request_count'
    metric_value    NUMERIC(18, 4) NOT NULL DEFAULT 0,
    unit            TEXT NOT NULL DEFAULT 'count',             -- 'token', 'count', 'byte', 'second'
    extra_json      JSONB NOT NULL DEFAULT '{}',               -- 附加信息（模型名、渠道等）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE metering_events IS '用量计量流水；记录 token/调用次数/存储等，为计费提供数据基础';
COMMENT ON COLUMN metering_events.tenant_id    IS '所属租户';
COMMENT ON COLUMN metering_events.task_id      IS '关联任务（可选）';
COMMENT ON COLUMN metering_events.agent_id     IS '关联 Agent（可选）';
COMMENT ON COLUMN metering_events.event_type   IS '事件类型: llm_call/api_call/delivery/storage';
COMMENT ON COLUMN metering_events.metric_name  IS '指标名: token_input/token_output/request_count';
COMMENT ON COLUMN metering_events.metric_value IS '指标数值';
COMMENT ON COLUMN metering_events.unit         IS '单位: token/count/byte/second';
COMMENT ON COLUMN metering_events.extra_json   IS '附加信息（模型名、渠道、版本等）';

CREATE INDEX idx_metering_tenant_time  ON metering_events (tenant_id, created_at DESC);
CREATE INDEX idx_metering_task         ON metering_events (task_id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_metering_event_type   ON metering_events (tenant_id, event_type, created_at DESC);
