#!/usr/bin/env bash
# =============================================================================
# A2A Hub — API 集成测试脚本
# 覆盖版块 1-3：基础骨架、核心任务流、Agent 注册与路由引擎
#
# 使用方式：
#   cd backend
#   bash ../tests/test_api.sh
#
# 前置条件：
#   1. 服务已启动：docker compose up -d postgres redis mosquitto db-init api
#   2. 数据库可连接：127.0.0.1:1881 / a2a_hub
#   3. 租户 tenant_001 已存在（脚本会自动创建）
# =============================================================================

set -e  # 遇到错误立即退出

# ── 配置 ──────────────────────────────────────────────────────────────────────
BASE_URL="${BASE_URL:-http://127.0.0.1:1880}"
DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-1881}"
DB_NAME="${DB_NAME:-a2a_hub}"
DB_USER="${DB_USER:-a2a_hub}"
DB_PASS="${DB_PASS:-a2a_hub_password}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="${VENV_PYTHON:-$PROJECT_ROOT/backend/.venv/bin/python}"
TOKEN_FILE="/tmp/a2a_test_token.txt"
RUN_ID="$(date +%s)"

cd "$PROJECT_ROOT/backend"

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[通过]${NC} $1"; }
fail() { echo -e "${RED}[失败]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[提示]${NC} $1"; }
section() { echo ""; echo -e "${YELLOW}══════════════════════════════════════${NC}"; echo -e "${YELLOW} $1${NC}"; echo -e "${YELLOW}══════════════════════════════════════${NC}"; }

# ── 工具函数 ──────────────────────────────────────────────────────────────────

# 执行 psql 命令
psql_exec() {
    PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "$1" 2>&1
}

# 发送 HTTP 请求，返回响应体
http() {
    local method="$1"
    local path="$2"
    local data="$3"
    local token
    token=$(cat "$TOKEN_FILE")

    if [ -n "$data" ]; then
        curl -s -X "$method" "${BASE_URL}${path}" \
            -H "Authorization: Bearer $token" \
            -H "Content-Type: application/json" \
            -d "$data"
    else
        curl -s -X "$method" "${BASE_URL}${path}" \
            -H "Authorization: Bearer $token"
    fi
}

# 发送无需 JWT 的公开 HTTP 请求
http_public() {
    local method="$1"
    local path="$2"
    local data="$3"

    if [ -n "$data" ]; then
        curl -s -X "$method" "${BASE_URL}${path}" \
            -H "Content-Type: application/json" \
            -d "$data"
    else
        curl -s -X "$method" "${BASE_URL}${path}"
    fi
}

# 生成 Webhook HMAC 签名
sign_webhook() {
    local secret="$1"
    local payload="$2"
    local timestamp nonce signature
    timestamp=$(date +%s)
    nonce="nonce_$(date +%s%N)"
    signature=$(python3 -c "
import hashlib, hmac, sys
secret = sys.argv[1].encode('utf-8')
timestamp = sys.argv[2].encode('utf-8')
nonce = sys.argv[3].encode('utf-8')
payload = sys.argv[4].encode('utf-8')
print(hmac.new(secret, b'.'.join([timestamp, nonce, payload]), hashlib.sha256).hexdigest())
" "$secret" "$timestamp" "$nonce" "$payload")
    echo "${timestamp}|${nonce}|${signature}"
}

# 断言响应中 error 字段为 null
assert_ok() {
    local resp="$1"
    local desc="$2"
    local has_error
    has_error=$(echo "$resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print('yes' if d.get('error') else 'no')
except:
    print('yes')
" 2>/dev/null)
    if [ "$has_error" = "no" ]; then
        pass "$desc"
    else
        echo "$resp" | python3 -m json.tool 2>/dev/null || echo "$resp"
        fail "$desc — 响应包含 error"
    fi
}

# 断言响应中某字段等于期望值
assert_field() {
    local resp="$1"
    local field="$2"   # 支持 data.state 格式
    local expected="$3"
    local desc="$4"
    local actual
    actual=$(echo "$resp" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    keys = '$field'.split('.')
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            d = None
            break
    print(d)
except Exception as e:
    print('__ERROR__')
" 2>/dev/null)
    if [ "$actual" = "$expected" ]; then
        pass "$desc (${field}=${expected})"
    else
        echo "$resp" | python3 -m json.tool 2>/dev/null || echo "$resp"
        fail "$desc — 期望 ${field}=${expected}，实际=${actual}"
    fi
}

# ── 开始测试 ──────────────────────────────────────────────────────────────────

section "环境准备"

# 检查服务是否启动
info "检查服务健康状态..."
HEALTH=$(curl -s "${BASE_URL}/health")
if echo "$HEALTH" | grep -q '"ok"'; then
    pass "服务正常运行"
else
    fail "服务未启动，请先执行: docker compose up -d postgres redis mosquitto db-init api"
fi

# 初始化测试数据：租户
info "初始化测试租户 tenant_001..."
psql_exec "INSERT INTO tenants (tenant_id, name) VALUES ('tenant_001', '测试租户') ON CONFLICT DO NOTHING;" > /dev/null
pass "租户 tenant_001 就绪"

# 初始化测试数据：上下文
info "初始化测试上下文 ctx_test001..."
psql_exec "INSERT INTO contexts (context_id, tenant_id, source_channel, title, status, last_activity_at)
           VALUES ('ctx_test001', 'tenant_001', 'api', '测试会话', 'OPEN', now())
           ON CONFLICT DO NOTHING;" > /dev/null
pass "上下文 ctx_test001 就绪"

# 生成 JWT token（tenant_id=tenant_001）
info "生成测试 JWT token..."
$VENV_PYTHON -c "
from app.core.security import create_access_token
open('$TOKEN_FILE','w').write(create_access_token('test-user',{'tenant_id':'tenant_001'}))
"
pass "JWT token 已生成 → $TOKEN_FILE"

# ── 版块1+2：基础骨架 & 核心任务流 ───────────────────────────────────────────

section "版块1+2：基础骨架 & 核心任务流"

# 测试：健康检查接口
info "T01 — 健康检查 GET /health"
RESP=$(curl -s "${BASE_URL}/health")
assert_field "$RESP" "status" "ok" "T01 健康检查"

# 测试：发送消息创建任务（无路由规则时，任务进入 FAILED）
info "T02 — 发消息创建任务（无 Agent，预期路由失败进入 FAILED）"
# 先清理已有路由规则，确保此时无规则
psql_exec "DELETE FROM routing_rules WHERE tenant_id='tenant_001';" > /dev/null
psql_exec "DELETE FROM agents WHERE tenant_id='tenant_001';" > /dev/null

RESP=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"无路由测试"}]}')
assert_field "$RESP" "data.state" "FAILED" "T02 无 Agent 时任务进入 FAILED"

# 取出 task_id 用于后续查询
FAILED_TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")

# 测试：查询任务详情
info "T03 — 查询任务详情 GET /v1/tasks/{task_id}"
RESP=$(http GET "/v1/tasks/${FAILED_TASK_ID}")
assert_field "$RESP" "data.state" "FAILED" "T03 查询任务状态为 FAILED"
assert_field "$RESP" "data.tenant_id" "tenant_001" "T03 租户隔离正确"

# 测试：取消终态任务应返回错误（FAILED 是终态，不可跳转）
info "T04 — 取消终态任务（预期返回 422）"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BASE_URL}/v1/tasks/${FAILED_TASK_ID}/cancel" \
    -H "Authorization: Bearer $(cat $TOKEN_FILE)")
if [ "$HTTP_CODE" = "422" ]; then
    pass "T04 终态任务取消返回 422"
else
    fail "T04 期望 422，实际 HTTP $HTTP_CODE"
fi

# 测试：幂等键防重复提交
info "T05 — 幂等键防重复提交"
RESP1=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"幂等测试"}],"idempotency_key":"idem_test_001"}')
RESP2=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"幂等测试重复"}],"idempotency_key":"idem_test_001"}')
TASK_ID_1=$(echo "$RESP1" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")
TASK_ID_2=$(echo "$RESP2" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")
if [ "$TASK_ID_1" = "$TASK_ID_2" ]; then
    pass "T05 幂等键生效，两次请求返回同一 task_id"
else
    fail "T05 幂等键未生效，task_id 不同: $TASK_ID_1 vs $TASK_ID_2"
fi

# ── 版块3：Agent 注册与路由引擎 ───────────────────────────────────────────────

section "版块3：Agent 注册与路由引擎"

# 测试：注册 Agent
info "T06 — 注册 Agent POST /v1/agents"
RESP=$(http POST "/v1/agents" '{"agent_id":"openclaw:ava","agent_type":"federated","display_name":"OpenClaw AVA","capabilities":{"analysis":true,"generic":true},"config_json":{"base_url":"https://openclaw.example.com"}}')
assert_field "$RESP" "data.agent_id" "openclaw:ava" "T06 Agent 注册成功"
assert_field "$RESP" "data.status" "ACTIVE" "T06 Agent 初始状态为 ACTIVE"

# 测试：注册第二个 Agent（用于路由测试）
info "T07 — 注册第二个 Agent（workbuddy）"
RESP=$(http POST "/v1/agents" '{"agent_id":"workbuddy:main","agent_type":"bridged","display_name":"Workbuddy Main","capabilities":{"quote":true},"config_json":{}}')
assert_field "$RESP" "data.agent_id" "workbuddy:main" "T07 第二个 Agent 注册成功"

# 测试：列出 Agent
info "T08 — 列出所有 ACTIVE Agent GET /v1/agents"
RESP=$(http GET "/v1/agents")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))")
if [ "$COUNT" -ge "2" ]; then
    pass "T08 列出 Agent，数量 >= 2"
else
    fail "T08 期望 >= 2 个 Agent，实际 $COUNT"
fi

# 测试：查询单个 Agent
info "T09 — 查询单个 Agent GET /v1/agents/{agent_id}"
RESP=$(http GET "/v1/agents/openclaw:ava")
assert_field "$RESP" "data.agent_id" "openclaw:ava" "T09 查询单个 Agent 成功"
assert_field "$RESP" "data.display_name" "OpenClaw AVA" "T09 Agent 名称正确"

# 测试：Agent 健康检查
info "T10 — Agent 健康检查 GET /v1/agents/{agent_id}/health"
RESP=$(http GET "/v1/agents/openclaw:ava/health")
assert_field "$RESP" "data.healthy" "True" "T10 openclaw:ava 健康状态为 True"

# 测试：更新 Agent 状态为 INACTIVE
info "T11 — 更新 Agent 状态 PATCH /v1/agents/{agent_id}/status"
RESP=$(http PATCH "/v1/agents/workbuddy:main/status" '{"status":"INACTIVE"}')
assert_field "$RESP" "data.status" "INACTIVE" "T11 Agent 状态更新为 INACTIVE"

# 测试：重复注册同一 Agent，验证配置更新
info "T12 — 重复注册 Agent，验证配置更新"
RESP=$(http POST "/v1/agents" '{"agent_id":"openclaw:ava","agent_type":"federated","display_name":"OpenClaw AVA V2","capabilities":{"analysis":true,"generic":true,"report":true},"auth_scheme":"jwt","config_json":{"base_url":"https://openclaw-v2.example.com"}}')
assert_field "$RESP" "data.display_name" "OpenClaw AVA V2" "T12 Agent 名称更新成功"
assert_field "$RESP" "data.auth_scheme" "jwt" "T12 Agent 鉴权方案更新成功"
assert_field "$RESP" "data.config_json.base_url" "https://openclaw-v2.example.com" "T12 Agent 配置更新成功"

# 测试：创建精确匹配路由规则
info "T13 — 创建路由规则（analysis → openclaw:ava）"
RESP=$(http POST "/v1/routing-rules" '{"name":"analysis-to-openclaw","priority":10,"match_expr":{"task_type":"analysis"},"target_agent_id":"openclaw:ava","is_active":true}')
assert_field "$RESP" "data.name" "analysis-to-openclaw" "T13 精确路由规则创建成功"
RULE_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('id',''))")

# 测试：创建兜底路由规则
info "T14 — 创建兜底路由规则（空 match_expr → openclaw:ava）"
RESP=$(http POST "/v1/routing-rules" '{"name":"default-fallback","priority":999,"match_expr":{},"target_agent_id":"openclaw:ava","is_active":true}')
assert_field "$RESP" "data.name" "default-fallback" "T14 兜底路由规则创建成功"

# 测试：列出路由规则
info "T15 — 列出路由规则 GET /v1/routing-rules"
RESP=$(http GET "/v1/routing-rules")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))")
if [ "$COUNT" -ge "2" ]; then
    pass "T15 路由规则列表，数量 >= 2"
else
    fail "T15 期望 >= 2 条规则，实际 $COUNT"
fi

# 测试：路由 dry-run（精确匹配 analysis）
info "T16 — 路由测试 analysis（命中精确规则）"
RESP=$(http POST "/v1/routing/test" '{"task_type":"analysis"}')
assert_field "$RESP" "data.target_agent_id" "openclaw:ava" "T16 analysis 路由到 openclaw:ava"
assert_field "$RESP" "data.routed" "True" "T16 路由成功"

# 测试：路由 dry-run（走兜底规则）
info "T17 — 路由测试 generic（走兜底规则）"
RESP=$(http POST "/v1/routing/test" '{"task_type":"generic"}')
assert_field "$RESP" "data.target_agent_id" "openclaw:ava" "T17 generic 走兜底路由到 openclaw:ava"

# 测试：路由 dry-run（显式指定 target_agent_id）
info "T18 — 路由测试（显式指定 target_agent_id）"
RESP=$(http POST "/v1/routing/test" '{"task_type":"generic","target_agent_id":"openclaw:ava"}')
assert_field "$RESP" "data.routed" "True" "T18 显式指定路由成功"

# 测试：禁用路由规则
info "T19 — 禁用路由规则 PATCH /v1/routing-rules/{rule_id}"
RESP=$(http PATCH "/v1/routing-rules/${RULE_ID}" '{"is_active":false}')
assert_field "$RESP" "data.is_active" "False" "T19 路由规则禁用成功"

# 重新启用，恢复测试环境
http PATCH "/v1/routing-rules/${RULE_ID}" '{"is_active":true}' > /dev/null

# ── 端到端：发消息自动路由 ────────────────────────────────────────────────────

section "端到端：发消息 → 自动路由 → WORKING"

# 测试：发消息，任务自动路由到 openclaw:ava
info "T20 — 发消息自动路由（预期 state=WORKING）"
RESP=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"请分析这个客户需求"}],"metadata":{"source":"test"}}')
assert_field "$RESP" "data.state" "WORKING" "T20 任务自动路由，state=WORKING"
TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")

# 测试：查询任务，确认 target_agent_id 已填充
info "T21 — 查询任务，确认路由结果写入"
RESP=$(http GET "/v1/tasks/${TASK_ID}")
assert_field "$RESP" "data.state" "WORKING" "T21 任务状态为 WORKING"
assert_field "$RESP" "data.target_agent_id" "openclaw:ava" "T21 目标 Agent 已写入任务"

# 测试：通过 API 将任务更新为 COMPLETED
info "T22 — 更新任务状态为 COMPLETED"
RESP=$(http PATCH "/v1/tasks/${TASK_ID}/state" '{"new_state":"COMPLETED","reason":"处理完成","output_text":"分析报告已生成"}')
assert_field "$RESP" "data.state" "COMPLETED" "T22 任务状态更新为 COMPLETED"
assert_field "$RESP" "data.output_text" "分析报告已生成" "T22 任务输出写入成功"

# 测试：再次查询任务，确认完成态已持久化
info "T23 — 查询任务，确认完成态与输出结果持久化"
RESP=$(http GET "/v1/tasks/${TASK_ID}")
assert_field "$RESP" "data.state" "COMPLETED" "T23 任务状态为 COMPLETED"
assert_field "$RESP" "data.output_text" "分析报告已生成" "T23 任务输出结果持久化成功"

# 测试：创建第二个任务，验证取消流程
info "T24 — 再次发消息创建任务，用于取消流程"
RESP=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"请继续跟进这个客户需求"}],"metadata":{"source":"test-cancel"}}')
assert_field "$RESP" "data.state" "WORKING" "T24 第二个任务自动路由，state=WORKING"
CANCEL_TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")

# 测试：取消 WORKING 状态的任务
info "T25 — 取消 WORKING 任务"
RESP=$(http POST "/v1/tasks/${CANCEL_TASK_ID}/cancel")
assert_field "$RESP" "data.state" "CANCELED" "T25 任务取消成功，state=CANCELED"

# 测试：验证数据库中状态流水完整
info "T26 — 验证已完成任务的 task_state_transitions 流水记录"
TRANS_COUNT=$(psql_exec "SELECT COUNT(*) FROM task_state_transitions WHERE task_id='${TASK_ID}';" | grep -E '^\s+[0-9]+' | tr -d ' ')
if [ "$TRANS_COUNT" -ge "4" ]; then
    pass "T26 状态流水记录 >= 4 条（SUBMITTED/ROUTING/WORKING/COMPLETED）"
else
    fail "T26 期望 >= 4 条状态流水，实际 $TRANS_COUNT"
fi

# 测试：验证路由跳转记录
info "T27 — 验证 task_route_hops 跳转记录"
HOP_COUNT=$(psql_exec "SELECT COUNT(*) FROM task_route_hops WHERE task_id='${TASK_ID}';" | grep -E '^\s+[0-9]+' | tr -d ' ')
if [ "$HOP_COUNT" -ge "1" ]; then
    pass "T27 路由跳转记录 >= 1 条"
else
    fail "T27 期望 >= 1 条路由跳转记录，实际 $HOP_COUNT"
fi

# ── 安全：租户隔离 ────────────────────────────────────────────────────────────

section "安全：租户隔离"

# 创建另一个租户的 token，尝试访问 tenant_001 的资源
info "T28 — 租户隔离：tenant_002 不能访问 tenant_001 的任务"
psql_exec "INSERT INTO tenants (tenant_id, name) VALUES ('tenant_002', '测试租户2') ON CONFLICT DO NOTHING;" > /dev/null
$VENV_PYTHON -c "
from app.core.security import create_access_token
open('/tmp/a2a_token_002.txt','w').write(create_access_token('other-user',{'tenant_id':'tenant_002'}))
"
RESP=$(curl -s "${BASE_URL}/v1/tasks/${TASK_ID}" \
    -H "Authorization: Bearer $(cat /tmp/a2a_token_002.txt)")
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${BASE_URL}/v1/tasks/${TASK_ID}" \
    -H "Authorization: Bearer $(cat /tmp/a2a_token_002.txt)")
if [ "$HTTP_CODE" = "404" ]; then
    pass "T28 租户隔离生效，tenant_002 访问 tenant_001 任务返回 404"
else
    fail "T28 租户隔离失败，HTTP $HTTP_CODE"
fi

# ── 版块4：OpenClaw 接入 ─────────────────────────────────────────────────────

section "版块4：OpenClaw 接入"

info "T29 — OpenClaw transcript 映射为任务与会话"
RESP=$(http_public POST "/v1/openclaw/events/transcript" '{"tenant_id":"tenant_001","session_key":"oc_session_001","event_id":"oc_evt_001","text":"OpenClaw 产出了一段 transcript","sender_type":"agent","sender_id":"openclaw:ava","task_type":"analysis","metadata":{"source":"integration"}}')
assert_field "$RESP" "data.state" "SUBMITTED" "T29 OpenClaw transcript 创建任务成功"
OC_TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")
RESP=$(http GET "/v1/tasks/${OC_TASK_ID}")
assert_field "$RESP" "data.task_type" "analysis" "T29 OpenClaw transcript 任务类型正确"

# ── 版块5：Rocket.Chat 接入 ──────────────────────────────────────────────────

section "版块5：Rocket.Chat 接入"

info "T30 — Rocket.Chat webhook 入站触发任务"
RC_PAYLOAD='{"tenant_id":"tenant_001","room_id":"rc_room_001","text":"请在 RC 中分析这条需求","sender_id":"rc_user_001","sender_name":"RC测试用户","server_url":"https://rc.example.com","message_id":"rc_msg_001","metadata":{"source":"integration"}}'
IFS='|' read -r RC_TS RC_NONCE RC_SIG <<< "$(sign_webhook "dev-rocket-secret" "$RC_PAYLOAD")"
RESP=$(curl -s -X POST "${BASE_URL}/v1/rocketchat/webhook" \
    -H "Content-Type: application/json" \
    -H "X-A2A-Timestamp: ${RC_TS}" \
    -H "X-A2A-Nonce: ${RC_NONCE}" \
    -H "X-A2A-Signature: ${RC_SIG}" \
    -d "$RC_PAYLOAD")
assert_field "$RESP" "data.state" "WORKING" "T30 Rocket.Chat webhook 已创建并路由任务"
RC_TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")
RESP=$(http GET "/v1/tasks/${RC_TASK_ID}")
assert_field "$RESP" "data.state" "WORKING" "T30 Rocket.Chat 任务状态为 WORKING"

# ── 版块6：审批流 ────────────────────────────────────────────────────────────

section "版块6：审批流"

info "T31 — 创建待审批任务"
RESP=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"这是一项需要审批的操作"}],"metadata":{"source":"approval-test"}}')
assert_field "$RESP" "data.state" "WORKING" "T31 审批测试任务创建成功"
APPROVAL_TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")

info "T32 — 创建审批并使任务进入 AUTH_REQUIRED"
RESP=$(http POST "/v1/approvals" "{\"task_id\":\"${APPROVAL_TASK_ID}\",\"approver_user_id\":\"approver_001\",\"reason\":\"高风险操作需人工确认\",\"metadata\":{\"source\":\"integration\"}}")
assert_field "$RESP" "data.status" "PENDING" "T32 审批创建成功"
APPROVAL_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('approval_id',''))")
RESP=$(http GET "/v1/tasks/${APPROVAL_TASK_ID}")
assert_field "$RESP" "data.state" "AUTH_REQUIRED" "T32 任务进入 AUTH_REQUIRED"

info "T33 — 审批通过后任务恢复 WORKING"
RESP=$(http POST "/v1/approvals/${APPROVAL_ID}/resolve" '{"decision":"APPROVED","note":"允许继续执行"}')
assert_field "$RESP" "data.status" "APPROVED" "T33 审批通过成功"
RESP=$(http GET "/v1/tasks/${APPROVAL_TASK_ID}")
assert_field "$RESP" "data.state" "WORKING" "T33 审批通过后任务恢复 WORKING"

info "T34 — 再创建一个审批并拒绝"
RESP=$(http POST "/v1/messages/send" '{"context_id":"ctx_test001","parts":[{"type":"text/plain","text":"这是一项应被拒绝的操作"}],"metadata":{"source":"approval-reject"}}')
assert_field "$RESP" "data.state" "WORKING" "T34 拒绝分支任务创建成功"
REJECT_TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('task_id',''))")
RESP=$(http POST "/v1/approvals" "{\"task_id\":\"${REJECT_TASK_ID}\",\"approver_user_id\":\"approver_001\",\"reason\":\"该操作应拒绝\"}")
REJECT_APPROVAL_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('approval_id',''))")
RESP=$(http POST "/v1/approvals/${REJECT_APPROVAL_ID}/resolve" '{"decision":"REJECTED","note":"不允许执行"}')
assert_field "$RESP" "data.status" "REJECTED" "T34 审批拒绝成功"
RESP=$(http GET "/v1/tasks/${REJECT_TASK_ID}")
assert_field "$RESP" "data.state" "FAILED" "T34 审批拒绝后任务进入 FAILED"

# ── 版块7：投递服务 + 重试 + DLQ ───────────────────────────────────────────

section "版块7：投递服务 + 重试 + DLQ"

info "清理当前租户历史投递记录..."
psql_exec "DELETE FROM deliveries WHERE tenant_id='tenant_001';" > /dev/null
pass "历史投递记录已清理"

info "T35 — 创建成功投递并处理"
RESP=$(http POST "/v1/deliveries" '{"target_channel":"other","target_ref":{"simulate":"success"},"payload":{"text":"投递成功测试"},"task_id":"'"${TASK_ID}"'","idempotency_key":"delivery_success_'"${RUN_ID}"'"}')
assert_field "$RESP" "data.status" "PENDING" "T35 投递任务创建成功"
RESP=$(http POST "/v1/deliveries/process-due?limit=1" '{}')
assert_ok "$RESP" "T35 处理待投递接口返回成功"
PROCESSED_COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['processed_count'])")
if [ "$PROCESSED_COUNT" -ge "1" ]; then
    pass "T35 成功投递已被处理"
else
    fail "T35 期望至少处理 1 条投递，实际 $PROCESSED_COUNT"
fi

info "T36 — 创建失败投递并进入 DLQ"
RESP=$(http POST "/v1/deliveries" '{"target_channel":"other","target_ref":{"simulate":"fail"},"payload":{"text":"投递失败测试"},"task_id":"'"${TASK_ID}"'","idempotency_key":"delivery_fail_'"${RUN_ID}"'","max_attempts":1}')
assert_field "$RESP" "data.status" "PENDING" "T36 失败投递任务创建成功"
FAIL_DELIVERY_ID=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); data=d.get('data') or {}; print(data.get('delivery_id',''))")
RESP=$(http POST "/v1/deliveries/process-due?limit=1" '{}')
assert_ok "$RESP" "T36 处理待投递接口返回成功"
PROCESSED_COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['processed_count'])")
if [ "$PROCESSED_COUNT" -ge "1" ]; then
    pass "T36 失败投递处理完成"
else
    fail "T36 期望至少处理 1 条投递，实际 $PROCESSED_COUNT"
fi
RESP=$(http GET "/v1/deliveries/dlq")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))")
if [ "$COUNT" -ge "1" ]; then
    pass "T36 DLQ 列表中存在失败投递"
else
    fail "T36 期望 DLQ 至少 1 条，实际 $COUNT"
fi

info "T37 — 重放 DLQ 投递"
RESP=$(http POST "/v1/deliveries/${FAIL_DELIVERY_ID}/replay" '{}')
assert_field "$RESP" "data.status" "PENDING" "T37 DLQ 重放成功"

info "T38 — 计量汇总可查询"
RESP=$(http GET "/v1/metering/summary")
COUNT=$(echo "$RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))")
if [ "$COUNT" -ge "1" ]; then
    pass "T38 计量汇总已产生数据"
else
    fail "T38 期望计量汇总至少 1 条，实际 $COUNT"
fi

# ── 测试完成 ──────────────────────────────────────────────────────────────────

section "测试完成"
echo -e "${GREEN}所有测试用例通过！${NC}"
echo ""
echo "测试覆盖："
echo "  版块1+2  基础骨架 & 核心任务流  T01-T05"
echo "  版块3    Agent 注册与路由引擎   T06-T19"
echo "  端到端   任务创建/完成/取消     T20-T27"
echo "  安全     租户隔离               T28"
echo "  版块4    OpenClaw 接入          T29"
echo "  版块5    Rocket.Chat 接入       T30"
echo "  版块6    审批流                 T31-T34"
echo "  版块7    投递/重试/DLQ/计量     T35-T38"
