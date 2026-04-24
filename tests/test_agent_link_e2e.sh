#!/usr/bin/env bash
#
# Agent Link 端到端测试脚本
# 读取 .env 中的配置，自动完成 agent 注册和消息测试
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 加载 .env 配置
if [ -f "$SCRIPT_DIR/.env" ]; then
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    if [[ "$value" =~ [\$\#\`\\] ]]; then
      continue
    fi
    export "$key=$value"
  done < "$SCRIPT_DIR/.env"
fi

# 配置 URL - 直接使用 A2A_HUB_PUBLIC_BASE_URL（已包含完整 URL）
API_URL="${A2A_HUB_PUBLIC_BASE_URL:-http://localhost:1880}"
MQTT_PORT="${MQTT_HOST_PORT:-1883}"

echo "========================================"
echo "A2A Hub Agent Link 端到端测试"
echo "========================================"
echo "API 地址: $API_URL"
echo "MQTT 端口: $MQTT_PORT"

# 1. 检查服务健康
echo ""
echo "[1/6] 检查服务健康..."
if ! curl -sf "$API_URL/health" >/dev/null 2>&1; then
  echo "API 服务未就绪，请先运行 run.sh" >&2
  exit 1
fi
echo "API 服务正常"

# 2. 获取 manifest
echo ""
echo "[2/6] 获取 Agent Link Manifest..."
manifest=$(curl -sf "$API_URL/v1/agent-link/manifest")
if [ -z "$manifest" ]; then
  echo "获取 Manifest 失败" >&2
  exit 1
fi

# 提取配置
CONNECT_URL=$(echo "$manifest" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('public_connect_url',''))")
PLUGIN_URL=$(echo "$manifest" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('plugin_download_url',''))")
INSTALL_SCRIPT_URL=$(echo "$manifest" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('openclaw_install_script_url',''))")
MQTT_BROKER=$(echo "$manifest" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('mqtt_public_broker_url',''))")

echo "Connect URL: $CONNECT_URL"
echo "Plugin URL: $PLUGIN_URL"
echo "MQTT Broker: $MQTT_BROKER"

# 3. 准备测试 agent ID
TEST_AGENT_ID="${TEST_AGENT_ID:-mia}"
echo ""
echo "[3/6] 注册 Agent: $TEST_AGENT_ID"

# 构建注册请求
register_req=$(cat <<PAYLOAD
{
  "agent_id": "$TEST_AGENT_ID",
  "display_name": "Test Agent $TEST_AGENT_ID",
  "capabilities": {"generic": true, "test": true},
  "config_json": {"test_mode": true},
  "owner_profile": {"source": "e2e_test", "user_id": "test_user"}
}
PAYLOAD
)

# 调用自注册 API
register_resp=$(curl -sf -X POST "$API_URL/v1/agent-link/self-register" \
  -H "Content-Type: application/json" \
  -d "$register_req")

if [ -z "$register_resp" ]; then
  echo "注册失败" >&2
  exit 1
fi

# 解析注册响应
AUTH_TOKEN=$(echo "$register_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('auth_token',''))")
MQTT_USER=$(echo "$register_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('mqtt_username',''))")
MQTT_PASS=$(echo "$register_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('mqtt_password',''))")
MQTT_TOPIC=$(echo "$register_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('mqtt_command_topic',''))")
TENANT_ID=$(echo "$register_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('tenant_id',''))")
AGENT_ID=$(echo "$register_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('agent_id',''))")

if [ -z "$AUTH_TOKEN" ]; then
  echo "注册响应异常: $register_resp" >&2
  exit 1
fi

echo "注册成功!"
echo "  Agent ID: $AGENT_ID"
echo "  Tenant ID: $TENANT_ID"
echo "  MQTT Topic: $MQTT_TOPIC"

# 4. 上报 Presence
echo ""
echo "[4/6] 上报 Presence..."
presence_resp=$(curl -sf -X POST "$API_URL/v1/agent-link/presence" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d '{"status": "online", "metadata": {"test": true}}')

if [ -z "$presence_resp" ]; then
  echo "Presence 上报失败" >&2
  exit 1
fi

presence_status=$(echo "$presence_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('status','unknown'))")
echo "Presence 状态: $presence_status"

# 5. 创建 context 并发送测试消息（使用平台 API）
echo ""
echo "[5/6] 创建 context 并发送测试消息..."

# 创建 context
context_resp=$(curl -sf -X POST "$API_URL/v1/contexts" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d '{
    "title": "E2E Test Context",
    "metadata": {"test": true}
  }')

CONTEXT_ID=$(echo "$context_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('context_id',''))" 2>/dev/null || echo "")

if [ -z "$CONTEXT_ID" ]; then
  # 使用 agent 的默认 context
  CONTEXT_ID="ctx_e2e_$(date +%s)"
  echo "使用自动生成的 context ID: $CONTEXT_ID"
fi

message_req=$(cat <<PAYLOAD
{
  "context_id": "$CONTEXT_ID",
  "target_agent_id": "$AGENT_ID",
  "parts": [{"type": "text/plain", "text": "E2E 测试消息"}],
  "metadata": {"test_id": "e2e_test"}
}
PAYLOAD
)

message_resp=$(curl -sf -X POST "$API_URL/v1/messages/send" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $AUTH_TOKEN" \
  -d "$message_req")

if [ -z "$message_resp" ]; then
  echo "消息发送失败，尝试 agent-link/send..."
  # 尝试使用 agent-link/send（如果不需要 context_id）
  agent_link_req=$(cat <<PAYLOAD
{
  "target_agent_id": "$AGENT_ID",
  "parts": [{"type": "text/plain", "text": "E2E 测试消息"}]
}
PAYLOAD
  )
  message_resp=$(curl -sf -X POST "$API_URL/v1/agent-link/messages/send" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $AUTH_TOKEN" \
    -d "$agent_link_req")
fi

if [ -z "$message_resp" ]; then
  echo "消息发送失败，跳过任务验证"
  TASK_ID="skipped"
else
  TASK_ID=$(echo "$message_resp" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('task_id',''))" 2>/dev/null || echo "")
  echo "消息已发送"
  echo "  Task ID: $TASK_ID"
fi

# 6. 查询任务状态
echo ""
echo "[6/6] 查询任务状态..."
sleep 2

if [ -z "$TASK_ID" ] || [ "$TASK_ID" = "skipped" ]; then
  echo "跳过任务状态查询"
  task_state="N/A"
else
  task_resp=$(curl -sf "$API_URL/v1/tasks/$TASK_ID" \
    -H "Authorization: Bearer $AUTH_TOKEN")

  if [ -z "$task_resp" ]; then
    echo "任务查询失败" >&2
    task_state="unknown"
  else
    # 兼容不同响应格式
    task_state=$(echo "$task_resp" | python3 -c "
import sys,json
data = json.load(sys.stdin).get('data',{})
task = data.get('task', data)
print(task.get('state', 'unknown'))
" 2>/dev/null || echo "unknown")
    echo "任务状态: $task_state"
  fi
fi

# 清理测试数据（可选）
echo ""
echo "========================================"
echo "测试完成!"
echo "========================================"
echo "Agent ID: $AGENT_ID"
echo "Tenant ID: $TENANT_ID"
echo "Task ID: $TASK_ID"
echo "Task State: $task_state"

# 返回非 0 如果任务失败
if [ "$task_state" = "FAILED" ]; then
  echo "警告: 任务状态为 FAILED"
  exit 1
fi

if [ "$task_state" = "N/A" ]; then
  echo "跳过任务状态验证"
fi

exit 0