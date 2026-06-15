#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Agent Link 插件安装测试脚本（通过 agent 对话方式）
# ============================================================
# 测试流程：
#   1. 检查 Hub 健康状态
#   2. 逐个 agent 发送安装命令
#   3. 沙箱 agent 自动检测 → 宿主机补装
#   4. 等待 MQTT 上线
#   5. 验证最终状态
#   6. 测试 remove 命令
#   7. 测试全量安装（批量模式）
# ============================================================

HUB_URL="${HUB_URL:-https://test.aihub.com}"
OPENCLAW_BIN="${OPENCLAW_BIN:-$HOME/.local/share/nvm/v24.14.1/bin/openclaw}"
TIMEOUT_SEC="${TIMEOUT_SEC:-90}"       # 单个 agent 等待上限
POLL_INTERVAL=5                        # 轮询间隔（秒）
FORCE_CLEAN="${FORCE_CLEAN:-true}"    # 是否全新安装
TEST_REMOVE="${TEST_REMOVE:-false}"   # 是否测试 remove 命令
TEST_BATCH="${TEST_BATCH:-false}"     # 是否测试批量安装

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ---- 工具函数 ----

log_info()  { echo -e "${CYAN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_err()   { echo -e "${RED}[FAIL]${NC}  $(date '+%H:%M:%S') $*"; }
log_step()  { echo -e "\n${CYAN}━━━ 步骤 $1: $2 ━━━${NC}"; }

divider()   { echo -e "${CYAN}────────────────────────────────────────${NC}"; }

# 等待文件出现并返回内容
wait_for_file() {
  local file="$1" label="$2" timeout="$3"
  local elapsed=0
  while [ ! -f "$file" ]; do
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
    if [ $elapsed -ge "$timeout" ]; then
      return 1
    fi
    log_info "  等待 $label ... (${elapsed}s/${timeout}s)"
  done
  return 0
}

# 在宿主机执行安装
host_install() {
  local agent="$1" hub_ip="$2"
  local connect_url="http://${hub_ip}:1880/agent-link/connect"
  local script_url="http://${hub_ip}:1880/agent-link/install/openclaw-aimoo-link.sh"

  log_info "  在宿主机执行安装..."
  log_info "  AGENT_ID=$agent"
  log_info "  CONNECT_URL=$connect_url"

  local output
  output=$(AGENT_ID="$agent" CONNECT_URL="$connect_url" curl -fsSL "$script_url" | bash 2>&1) || {
    log_err "宿主机安装失败"
    echo "$output" | tail -20
    return 1
  }

  echo "$output" | while IFS= read -r line; do
    log_info "  [host] $line"
  done
  return 0
}

# 等待 MQTT 上线
wait_mqtt_online() {
  local agent="$1" timeout="$2"
  local state_file="$HOME/.openclaw/channels/aimoo/$agent/state.json"
  local elapsed=0

  while true; do
    local status
    status=$(jq -r '.status // "none"' "$state_file" 2>/dev/null || echo "none")
    if [ "$status" = "online" ]; then
      return 0
    fi
    sleep "$POLL_INTERVAL"
    elapsed=$((elapsed + POLL_INTERVAL))
    if [ $elapsed -ge "$timeout" ]; then
      return 1
    fi
    log_info "  MQTT 状态: $status (${elapsed}s/${timeout}s)"
  done
}

# 打印 agent 最终状态
print_agent_status() {
  local agent="$1"
  local state_file="$HOME/.openclaw/channels/aimoo/$agent/state.json"
  local install_file="$HOME/.openclaw/workspace/$agent/.agent-link/install-result.json"

  divider
  if [ -f "$state_file" ]; then
    local status agent_id tenant_id topic
    status=$(jq -r '.status' "$state_file" 2>/dev/null)
    agent_id=$(jq -r '.agentId' "$state_file" 2>/dev/null)
    tenant_id=$(jq -r '.tenantId' "$state_file" 2>/dev/null)
    topic=$(jq -r '.topic' "$state_file" 2>/dev/null)

    if [ "$status" = "online" ]; then
      log_ok "MQTT 状态: $status"
    else
      log_err "MQTT 状态: $status"
    fi
    log_info "  Agent ID:   $agent_id"
    log_info "  Tenant ID:  $tenant_id"
    log_info "  Topic:      $topic"
  else
    log_err "state.json 不存在"
  fi

  if [ -f "$install_file" ]; then
    local install_status install_summary
    install_status=$(jq -r '.status' "$install_file" 2>/dev/null)
    install_summary=$(jq -r '.summary' "$install_file" 2>/dev/null)
    log_info "  安装状态:   $install_status"
    log_info "  安装摘要:   $install_summary"
  fi
  divider
}

# 测试 remove 命令
test_remove() {
  local agent="$1"
  log_step "remove" "测试 remove 命令: $agent"

  # 执行 remove
  log_info "执行 openclaw aimoo --agent $agent remove..."
  local output
  output=$("$OPENCLAW_BIN" aimoo --agent "$agent" remove 2>&1) || true
  echo "$output" | while IFS= read -r line; do
    [ -z "$line" ] && continue
    log_info "  [remove] ${line:0:120}"
  done

  # 验证清理结果
  divider
  log_info "验证 remove 结果..."

  # 检查 state.json 是否已删除
  if [ -f "$HOME/.openclaw/channels/aimoo/$agent/state.json" ]; then
    log_warn "state.json 仍然存在（可能未完全清理）"
  else
    log_ok "state.json 已清理"
  fi

  # 检查 install-result.json 是否已删除
  if [ -f "$HOME/.openclaw/workspace/$agent/.agent-link/install-result.json" ]; then
    log_warn "install-result.json 仍然存在"
  else
    log_ok "install-result.json 已清理"
  fi

  # 检查 openclaw.json 中是否已移除 instance
  local has_instance
  has_instance=$(node -e "
const fs = require('fs');
const cfg = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const instances = cfg.channels && cfg.channels.aimoo && Array.isArray(cfg.channels.aimoo.instances) ? cfg.channels.aimoo.instances : [];
const found = instances.some(i => i.localAgentId === process.argv[2]);
process.stdout.write(found ? 'yes' : 'no');
" "$HOME/.openclaw/openclaw.json" "$agent" 2>/dev/null || echo "yes")

  if [ "$has_instance" = "no" ]; then
    log_ok "openclaw.json 中 $agent 的 instance 已移除"
  else
    log_warn "openclaw.json 中 $agent 的 instance 仍然存在"
  fi
}

# 测试批量安装
test_batch_install() {
  log_step "batch" "测试批量安装（不传 AGENT_ID）"

  log_info "发送批量安装命令..."
  local output
  output=$(curl -fsSL "$HUB_URL/agent-link/install/openclaw-aimoo-link.sh" | bash 2>&1) || true
  echo "$output" | while IFS= read -r line; do
    [ -z "$line" ] && continue
    log_info "  [batch] ${line:0:120}"
  done

  # 检查是否进入批量模式
  if echo "$output" | grep -q "检测到.*个待安装的 agent"; then
    log_ok "已进入批量安装模式"
  else
    log_info "未进入批量模式（可能所有 agent 已接入）"
  fi

  # 检查服务注册
  log_info "检查服务注册..."
  local services
  services=$(curl -fsS "https://test.aihub.com/v1/docs-test/services" 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
items = d.get('data', [])
print(f'Services: {len(items)}')
for s in items:
    print(f'  {s[\"title\"]} | {s[\"status\"]}')
" 2>/dev/null || echo "无法获取服务列表")
  echo "$services"
}

# ============================================================
# 主测试流程
# ============================================================

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   Agent Link 插件安装测试（Agent 对话方式）       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
log_info "Hub URL:     $HUB_URL"
log_info "OpenClaw:    $OPENCLAW_BIN"
log_info "超时时间:    ${TIMEOUT_SEC}s"
log_info "强制清理:    $FORCE_CLEAN"
log_info "测试 remove: $TEST_REMOVE"
log_info "测试批量:    $TEST_BATCH"
echo ""

# ---- 步骤 1: 检查环境 ----
log_step 1 "检查环境"

# 检查 Hub 健康状态
log_info "检查 Hub 健康状态..."
if curl -fsS -m 5 "$HUB_URL/health" >/dev/null 2>&1; then
  log_ok "Hub 在线: $(curl -s "$HUB_URL/health" | jq -r '.version // "?"')"
else
  log_err "Hub 不可达: $HUB_URL"
  exit 1
fi

# 检查 openclaw 命令
if [ ! -x "$OPENCLAW_BIN" ]; then
  log_err "openclaw 命令不可用: $OPENCLAW_BIN"
  exit 1
fi
log_ok "OpenClaw CLI 可用"

# 获取要测试的 agent 列表
TEST_AGENTS="${TEST_AGENTS:-mia}"
log_info "测试 agents: $TEST_AGENTS"
echo ""

# ---- 步骤 2: 清理旧状态（可选） ----
if [ "$FORCE_CLEAN" = "true" ]; then
  log_step 2 "清理旧安装状态"
  for agent in $TEST_AGENTS; do
    log_info "清理 $agent..."
    rm -f "$HOME/.openclaw/workspace/$agent/.agent-link/install-result.json" 2>/dev/null || true
    rm -f "$HOME/.openclaw/channels/aimoo/$agent/state.json" 2>/dev/null || true
    rm -f "$HOME/.openclaw/workspace/$agent/.agent-link/host-install.sh" 2>/dev/null || true
    log_ok "$agent 旧状态已清理"
  done
  echo ""
fi

# ---- 步骤 3: 逐个 agent 测试 ----
AGENT_NUM=0
TOTAL_AGENTS=$(echo "$TEST_AGENTS" | wc -w)
PASS_COUNT=0
FAIL_COUNT=0
SANDBOX_COUNT=0

for agent in $TEST_AGENTS; do
  AGENT_NUM=$((AGENT_NUM + 1))
  log_step 3.${AGENT_NUM} "测试 agent: $agent (${AGENT_NUM}/${TOTAL_AGENTS})"

  # 检查是否已有在线状态
  existing_status=$(jq -r '.status // "none"' "$HOME/.openclaw/channels/aimoo/$agent/state.json" 2>/dev/null || echo "none")
  if [ "$existing_status" = "online" ] && [ "$FORCE_CLEAN" != "true" ]; then
    log_warn "$agent 已在线，跳过安装测试"
    print_agent_status "$agent"
    PASS_COUNT=$((PASS_COUNT + 1))
    continue
  fi

  # ---- 3.1 发送安装命令 ----
  divider
  log_info "[3.1] 发送安装命令给 $agent..."

  AGENT_MSG="请安装 A2A Hub 的 aimoo-link 插件：curl -fsSL '$HUB_URL/agent-link/install/openclaw-aimoo-link.sh' | bash"
  log_info "消息: $AGENT_MSG"
  log_info "等待 agent 响应..."

  AGENT_OUTPUT=$("$OPENCLAW_BIN" agent --agent "$agent" -m "$AGENT_MSG" 2>&1) || true

  # 显示 agent 输出（截取关键行）
  echo "$AGENT_OUTPUT" | while IFS= read -r line; do
    # 跳过空行和过长的行
    [ -z "$line" ] && continue
    log_info "  [agent] ${line:0:120}"
  done
  divider

  # ---- 3.2 检查安装结果 ----
  log_info "[3.2] 检查安装结果..."
  sleep 3

  INSTALL_RESULT="$HOME/.openclaw/workspace/$agent/.agent-link/install-result.json"
  HOST_INSTALL="$HOME/.openclaw/workspace/$agent/.agent-link/host-install.sh"

  if [ -f "$INSTALL_RESULT" ]; then
    install_status=$(jq -r '.status' "$INSTALL_RESULT" 2>/dev/null)
    log_info "安装状态: $install_status"

    case "$install_status" in
      success)
        log_ok "安装成功（直接安装）"
        ;;
      sandbox_pending)
        log_warn "检测到 Docker 沙箱环境，需要宿主机补装"
        SANDBOX_COUNT=$((SANDBOX_COUNT + 1))

        # 显示沙箱检测详情
        hub_url=$(jq -r '.detail.hubUrl // "?"' "$INSTALL_RESULT" 2>/dev/null)
        log_info "  Hub URL: $hub_url"

        # 显示宿主机安装命令
        if [ -f "$HOST_INSTALL" ]; then
          log_info "  宿主机安装脚本: $HOST_INSTALL"
          log_info "  脚本内容:"
          cat "$HOST_INSTALL" | while IFS= read -r line; do
            log_info "    $line"
          done
        fi

        # ---- 3.3 在宿主机执行安装 ----
        divider
        log_info "[3.3] 在宿主机执行补装..."

        # 从 install-result.json 中提取网关 IP
        hub_ip=$(jq -r '.detail.hubUrl // empty' "$INSTALL_RESULT" 2>/dev/null | sed 's|http://||;s|:1880||')
        if [ -z "$hub_ip" ]; then
          log_err "无法获取 Hub 网关 IP"
          FAIL_COUNT=$((FAIL_COUNT + 1))
          continue
        fi

        log_info "Hub 网关 IP: $hub_ip"

        if host_install "$agent" "$hub_ip"; then
          log_ok "宿主机安装完成"
        else
          log_err "宿主机安装失败"
          FAIL_COUNT=$((FAIL_COUNT + 1))
          continue
        fi
        ;;
      running)
        # running 是中间状态（checker 在后台等待 MQTT 上线），需要轮询等待最终结果
        log_info "安装进行中（checker 后台运行），等待最终结果..."
        poll_elapsed=0
        poll_timeout=180
        while [ "$install_status" = "running" ] && [ $poll_elapsed -lt $poll_timeout ]; do
          sleep 5
          poll_elapsed=$((poll_elapsed + 5))
          install_status=$(jq -r '.status' "$INSTALL_RESULT" 2>/dev/null || echo "unknown")
          log_info "  状态: $install_status (${poll_elapsed}s/${poll_timeout}s)"
        done
        if [ "$install_status" = "success" ]; then
          log_ok "安装成功（后台 checker 完成）"
        elif [ "$install_status" = "failed" ]; then
          log_err "安装失败（checker 报告失败）"
          detail=$(jq -r '.detail // .summary // "?"' "$INSTALL_RESULT" 2>/dev/null)
          log_err "  详情: $detail"
          FAIL_COUNT=$((FAIL_COUNT + 1))
          continue
        else
          log_warn "安装仍在进行中或状态未知: $install_status（继续后续步骤）"
        fi
        ;;
      *)
        log_err "安装失败: $install_status"
        detail=$(jq -r '.detail // .summary // "?"' "$INSTALL_RESULT" 2>/dev/null)
        log_err "  详情: $detail"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        continue
        ;;
    esac
  else
    # 没有 install-result.json，检查是否直接通过 agent 对话安装成功
    log_warn "未找到 install-result.json，检查 agent 输出..."

    if echo "$AGENT_OUTPUT" | grep -qi "sandbox\|沙箱"; then
      log_warn "agent 报告了沙箱环境但未生成 install-result.json"
      log_info "尝试在宿主机直接安装..."

      # 获取 Docker 网关 IP
      docker_gw=$(ip route | awk '/default/ {print $3}' | head -1)
      if [ -n "$docker_gw" ] && curl -fsS -m 3 "http://${docker_gw}:1880/health" >/dev/null 2>&1; then
        host_install "$agent" "$docker_gw"
      else
        # 直接用 localhost（宿主机上）
        host_install "$agent" "localhost"
      fi
    else
      log_err "无法确定安装状态"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      continue
    fi
  fi

  # ---- 3.4 等待 Gateway 重启和 MQTT 上线 ----
  divider
  log_info "[3.4] 等待 Gateway 重启和 MQTT 上线..."

  # 等待 Gateway 恢复
  GW_READY=false
  for i in $(seq 1 12); do
    sleep 5
    if curl -fsS -m 3 "$HUB_URL/health" >/dev/null 2>&1; then
      log_ok "Gateway 已恢复 (${i}x5s)"
      GW_READY=true
      break
    fi
    log_info "  等待 Gateway ... (${i}x5s)"
  done

  if [ "$GW_READY" != "true" ]; then
    log_err "Gateway 未在 60s 内恢复"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    continue
  fi

  # 等待 MQTT 上线
  log_info "等待 $agent MQTT 上线..."
  if wait_mqtt_online "$agent" "$TIMEOUT_SEC"; then
    log_ok "$agent MQTT 已上线"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    log_err "$agent MQTT 未在 ${TIMEOUT_SEC}s 内上线"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi

  # ---- 3.5 打印最终状态 ----
  print_agent_status "$agent"
  echo ""
done

# ---- 步骤 4: 测试 remove 命令 ----
if [ "$TEST_REMOVE" = "true" ] && [ "$PASS_COUNT" -gt 0 ]; then
  # 使用第一个成功的 agent 测试 remove
  FIRST_AGENT=$(echo "$TEST_AGENTS" | awk '{print $1}')
  test_remove "$FIRST_AGENT"

  # 重新安装以恢复状态
  log_info "重新安装 $FIRST_AGENT 以恢复状态..."
  AGENT_MSG="请安装 A2A Hub 的 aimoo-link 插件：curl -fsSL '$HUB_URL/agent-link/install/openclaw-aimoo-link.sh' | bash"
  "$OPENCLAW_BIN" agent --agent "$FIRST_AGENT" -m "$AGENT_MSG" 2>&1 | tail -5 || true
  sleep 10
  echo ""
fi

# ---- 步骤 5: 测试批量安装 ----
if [ "$TEST_BATCH" = "true" ]; then
  # 批量测试需要先清理本地状态，否则所有 agent 已在线不会触发批量模式
  if [ "$FORCE_CLEAN" = "true" ]; then
    log_info "批量测试前重新清理本地状态..."
    for agent in $TEST_AGENTS; do
      rm -f "$HOME/.openclaw/channels/aimoo/$agent/state.json" 2>/dev/null || true
    done
    rm -rf "$HOME/.openclaw/channels/aimoo" 2>/dev/null || true
    rm -rf "$HOME/.openclaw/plugins/aimoo-link" 2>/dev/null || true
    # 清理 openclaw.json 中的 aimoo 配置
    python3 -c "
import json
cfg_path = '$HOME/.openclaw/openclaw.json'
with open(cfg_path) as f:
    d = json.load(f)
if 'channels' in d and 'aimoo' in d['channels']:
    del d['channels']['aimoo']
if 'plugins' in d:
    p = d['plugins']
    if 'allow' in p: p['allow'] = [x for x in p['allow'] if x != 'aimoo-link']
    if 'load' in p: p['load']['paths'] = [x for x in p['load'].get('paths',[]) if 'aimoo-link' not in x]
    if 'entries' in p and 'aimoo-link' in p['entries']: del p['entries']['aimoo-link']
with open(cfg_path, 'w') as f:
    json.dump(d, f, indent=2)
    f.write('\n')
" 2>/dev/null || true
    sleep 2
  fi
  test_batch_install
  echo ""
fi

# ============================================================
# 汇总报告
# ============================================================
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                  测试汇总报告                     ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
log_info "测试 agents:  $TEST_AGENTS"
log_info "总计:         $TOTAL_AGENTS"
log_ok   "通过:         $PASS_COUNT"
if [ $FAIL_COUNT -gt 0 ]; then
  log_err "失败:         $FAIL_COUNT"
else
  log_info "失败:         0"
fi
log_info "沙箱补装:     $SANDBOX_COUNT"
echo ""

# 逐个显示最终状态
for agent in $TEST_AGENTS; do
  status=$(jq -r '.status // "none"' "$HOME/.openclaw/channels/aimoo/$agent/state.json" 2>/dev/null || echo "none")
  if [ "$status" = "online" ]; then
    log_ok "$agent: online ✅"
  else
    log_err "$agent: $status ❌"
  fi
done
echo ""

if [ $FAIL_COUNT -eq 0 ]; then
  echo -e "${GREEN}🎉 所有 agent 安装测试通过！${NC}"
  exit 0
else
  echo -e "${RED}⚠️  有 $FAIL_COUNT 个 agent 安装失败，请检查上方日志${NC}"
  exit 1
fi
