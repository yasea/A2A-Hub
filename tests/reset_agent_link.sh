#!/usr/bin/env bash
# Agent Link (aimoo-link) 状态清理脚本
# 合并了 reset_openclaw_user_agent.sh 和 reset_client_agent_link_state.sh 的功能
#
# 用法:
#   bash reset_agent_link.sh [选项]
#
# 示例:
#   # 清理单个 agent（默认 main）
#   bash reset_agent_link.sh
#
#   # 清理指定 agent
#   bash reset_agent_link.sh --agent mia
#
#   # 清理所有 agent
#   bash reset_agent_link.sh --all
#
#   # 清理所有 agent 并删除插件和 skill
#   bash reset_agent_link.sh --all --remove-plugin
#
#   # 清理所有 agent 并远程注销 Hub 侧记录
#   bash reset_agent_link.sh --all --remove-remote
#
#   # 使用 auth token 远程注销（当本地配置已丢失时）
#   bash reset_agent_link.sh --all --remove-remote --auth-token <token>
#
#   # 指定 OPENCLAW_HOME
#   OPENCLAW_HOME=/home/openclaw/.openclaw bash reset_agent_link.sh --all --remove-plugin
set -euo pipefail

# 默认值
MODE="agent"
TARGET_AGENT="main"
REMOVE_PLUGIN="false"
REMOVE_REMOTE="false"
AUTH_TOKEN=""
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
HUB_URL="${HUB_URL:-https://test.aihub.com}"

usage() {
  cat <<'EOF'
Agent Link (aimoo-link) 状态清理脚本

用法:
  reset_agent_link.sh [选项]

选项:
  --agent <id>        指定要清理的 agent 短 id（默认 main）
  --all               清理所有 agent 的 Agent Link 状态
  --remove-plugin     同时删除 aimoo-link 插件目录和 skill 目录
  --remove-remote     远程注销 Hub 侧 agent 记录
  --auth-token <token> 使用指定的 auth token 远程注销（需配合 --remove-remote）
  --hub-url <url>     指定 Hub URL（默认 https://test.aihub.com）
  -h, --help          显示此帮助信息

环境变量:
  OPENCLAW_HOME       OpenClaw 配置目录（默认 ~/.openclaw）
  HUB_URL             Hub 服务地址（默认 https://test.aihub.com）

影响范围:
  - 更新 openclaw.json 中的 channels.aimoo / plugins.aimoo-link 配置
  - 删除 channels/aimoo/<agent> 和 workspace*/.agent-link
  - 清理 sessions.json 中 sessionId 以 aimoo: 开头的会话
  - 删除 TOOLS.md 中 A2A_HUB_AGENT_LINK_BEGIN/END 标记区段
  - 删除插件目录 plugins/aimoo-link（--remove-plugin）
  - 删除 skill 目录 skills/aimoo（--remove-plugin）
  - 远程注销 Hub 侧 agent 记录（--remove-remote）

示例:
  # 清理单个 agent
  bash reset_agent_link.sh --agent mia

  # 清理所有 agent 并删除插件
  bash reset_agent_link.sh --all --remove-plugin

  # 清理所有 agent 并远程注销
  bash reset_agent_link.sh --all --remove-remote

  # 使用 auth token 远程注销
  bash reset_agent_link.sh --all --remove-remote --auth-token <token>
EOF
}

# 参数解析
while [ "$#" -gt 0 ]; do
  case "$1" in
    --all)
      MODE="all"
      shift
      ;;
    --agent)
      TARGET_AGENT="${2:?--agent requires a value}"
      shift 2
      ;;
    --remove-plugin)
      REMOVE_PLUGIN="true"
      shift
      ;;
    --remove-remote)
      REMOVE_REMOTE="true"
      shift
      ;;
    --auth-token)
      AUTH_TOKEN="${2:?--auth-token requires a value}"
      shift 2
      ;;
    --hub-url)
      HUB_URL="${2:?--hub-url requires a value}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

echo "=========================================="
echo "Agent Link (aimoo-link) 状态清理"
echo "=========================================="
echo "OPENCLAW_HOME: $OPENCLAW_HOME"
echo "模式: $MODE"
echo "目标 agent: $TARGET_AGENT"
echo "删除插件: $REMOVE_PLUGIN"
echo "远程注销: $REMOVE_REMOTE"
echo "Hub URL: $HUB_URL"
echo ""

# 确定需要清理的用户
OWNER_USER=$(stat -c '%U' "$OPENCLAW_HOME" 2>/dev/null || stat -f '%Su' "$OPENCLAW_HOME" 2>/dev/null || echo "unknown")
CURRENT_USER=$(whoami)
echo "目标用户: $OWNER_USER, 当前用户: $CURRENT_USER"

# 如果当前用户不是目标用户所有者，使用 sudo
if [ "$OWNER_USER" != "$CURRENT_USER" ]; then
    echo "使用 sudo 清理..."
    SUDO_CMD="sudo"
else
    SUDO_CMD=""
fi

# ─────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────

# 查找 openclaw 命令
find_openclaw_cmd() {
  local cmd=""
  for candidate in \
      "$HOME/.local/share/nvm/"*/bin/openclaw \
      "$HOME/.npm-global/bin/openclaw" \
      "$HOME/.local/bin/openclaw" \
      "/opt/homebrew/bin/openclaw" \
      "/usr/local/bin/openclaw" \
      "/usr/bin/openclaw"; do
      if [ -x "$candidate" ]; then
          cmd="$candidate"
          # 同时设置 PATH 确保 node 可用（aimoo remove 内部需要 node）
          export PATH="$(dirname "$candidate"):$PATH"
          break
      fi
  done
  if [ -z "$cmd" ] && command -v openclaw >/dev/null 2>&1; then
      cmd="openclaw"
  fi
  echo "$cmd"
}

# 获取所有 agent IDs
get_agent_ids() {
  local agent_ids=""

  # 来源 1: 从 openclaw.json 读取
  if [ -f "$OPENCLAW_HOME/openclaw.json" ]; then
    local json_ids
    json_ids=$(python3 -c "
import json
try:
    with open('$OPENCLAW_HOME/openclaw.json') as f:
        cfg = json.load(f)
    channel = cfg.get('channels', {}).get('aimoo', {})
    instances = channel.get('instances', [])
    for inst in instances:
        aid = inst.get('localAgentId', '')
        if aid:
            print(aid)
except: pass
" 2>/dev/null || true)
    if [ -n "$json_ids" ]; then
      agent_ids="$json_ids"
    fi
  fi

  # 来源 2: 从 channels/aimoo/*/state.json 读取
  if [ -d "$OPENCLAW_HOME/channels/aimoo" ]; then
    for state_dir in "$OPENCLAW_HOME/channels/aimoo"/*/; do
      [ -d "$state_dir" ] || continue
      local agent_name
      agent_name=$(basename "$state_dir")
      if [ -n "$agent_name" ] && ! echo "$agent_ids" | grep -q "^$agent_name$"; then
        agent_ids="$agent_ids $agent_name"
      fi
    done
  fi

  # 去重并清理空白
  echo "$agent_ids" | tr ' ' '\n' | sort -u | grep -v '^$' | tr '\n' ' ' || true
}

# 从 state.json 读取 auth token
get_auth_token_from_state() {
  local agent_id="$1"
  local state_file="$OPENCLAW_HOME/channels/aimoo/$agent_id/state.json"
  if [ -f "$state_file" ]; then
    python3 -c "
import json
try:
    with open('$state_file') as f:
        d = json.load(f)
    print(d.get('authToken', d.get('auth_token', '')))
except: pass
" 2>/dev/null || true
  fi
}

# 从 state.json 读取 connect url
get_connect_url_from_state() {
  local agent_id="$1"
  local state_file="$OPENCLAW_HOME/channels/aimoo/$agent_id/state.json"
  local connect_url=""
  # 先从 state.json 读取
  if [ -f "$state_file" ]; then
    connect_url=$(python3 -c "
import json
try:
    with open('$state_file') as f:
        d = json.load(f)
    print(d.get('connectUrl', ''))
except: pass
" 2>/dev/null || true)
  fi
  # 如果 state.json 没有，从 openclaw.json 的 instances 读取
  if [ -z "$connect_url" ] && [ -f "$OPENCLAW_HOME/openclaw.json" ]; then
    connect_url=$(python3 -c "
import json
try:
    with open('$OPENCLAW_HOME/openclaw.json') as f:
        cfg = json.load(f)
    instances = cfg.get('channels', {}).get('aimoo', {}).get('instances', [])
    for inst in instances:
        lid = inst.get('localAgentId', '')
        if lid == '$agent_id':
            print(inst.get('connectUrl', ''))
            break
except: pass
" 2>/dev/null || true)
  fi
  echo "$connect_url"
}

# ─────────────────────────────────────────────────────────────────────────
# 1. 清理 openclaw.json 中的 channels.aimoo 和 plugins.aimoo-link 配置
# ─────────────────────────────────────────────────────────────────────────
clean_openclaw_json() {
  python3 - "$OPENCLAW_HOME/openclaw.json" "$MODE" "$TARGET_AGENT" <<'PY'
import json
import sys
from pathlib import Path

config_file = Path(sys.argv[1]).expanduser()
mode = sys.argv[2]
target = sys.argv[3]

if not config_file.exists():
    sys.exit(0)

data = json.loads(config_file.read_text(encoding="utf-8"))
changed = False

# Clean channels.aimoo
channels = data.get("channels")
if isinstance(channels, dict):
    aimoo = channels.get("aimoo")
    if isinstance(aimoo, dict):
        # Clean instances
        instances = aimoo.get("instances")
        if isinstance(instances, list):
            kept = []
            for item in instances:
                local_id = str(item.get("localAgentId") or item.get("agentId") or "").split(":")[-1]
                if mode == "all" or local_id == target:
                    continue
                kept.append(item)
            aimoo["instances"] = kept
            changed = True

        # Clean top-level config
        if mode == "all":
            channels.pop("aimoo", None)
            changed = True
        else:
            top_id = str(aimoo.get("localAgentId") or aimoo.get("agentId") or "").split(":")[-1]
            if top_id == target:
                channels.pop("aimoo", None)
                changed = True
            elif not aimoo.get("instances") and not top_id:
                channels.pop("aimoo", None)
                changed = True

    if not channels.get("aimoo"):
        channels.pop("aimoo", None)
        changed = True

    if not channels:
        data.pop("channels", None)

# Clean plugins section — only when all aimoo instances are gone
remaining_aimoo = channels.get("aimoo") if isinstance(channels, dict) else None
has_remaining_instances = isinstance(remaining_aimoo, dict) and remaining_aimoo.get("instances")
if not has_remaining_instances:
    plugins = data.get("plugins")
    if isinstance(plugins, dict):
        # Clean allow list
        allow = plugins.get("allow")
        if isinstance(allow, list):
            new_allow = [x for x in allow if x != "aimoo-link"]
            if len(new_allow) != len(allow):
                plugins["allow"] = new_allow
                changed = True
            if not new_allow:
                plugins.pop("allow", None)

        # Clean load.paths
        load = plugins.get("load")
        if isinstance(load, dict):
            paths = load.get("paths")
            if isinstance(paths, list):
                new_paths = [p for p in paths if "aimoo-link" not in str(p)]
                if len(new_paths) != len(paths):
                    load["paths"] = new_paths
                    changed = True
                if not new_paths:
                    load.pop("paths", None)
            if not load:
                plugins.pop("load", None)

        # Clean entries
        entries = plugins.get("entries")
        if isinstance(entries, dict):
            entries.pop("aimoo-link", None)
            changed = True
            if not entries:
                plugins.pop("entries", None)

        if not plugins:
            data.pop("plugins", None)

if changed:
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("✅ 已清理 openclaw.json")
else:
    print("ℹ️  无需清理 openclaw.json")
PY
}

# ─────────────────────────────────────────────────────────────────────────
# 2. 清理 sessions.json 中的 aimoo: 开头的会话
# ─────────────────────────────────────────────────────────────────────────
strip_aimoo_sessions() {
  local session_file="$1"
  python3 - "$session_file" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    sys.exit(0)

changed = False
for key, value in list(data.items()):
    if isinstance(value, dict) and str(value.get("sessionId") or "").startswith("aimoo:"):
        data.pop(key, None)
        changed = True

if changed:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

# ─────────────────────────────────────────────────────────────────────────
# 3. 清理 TOOLS.md 中的 A2A Hub 标记区段
# ─────────────────────────────────────────────────────────────────────────
strip_tools_section() {
  local tools_file="$1"
  python3 - "$tools_file" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    text = path.read_text(encoding="utf-8")
except Exception:
    sys.exit(0)

begin = "<!-- A2A_HUB_AGENT_LINK_BEGIN -->"
end = "<!-- A2A_HUB_AGENT_LINK_END -->"
idx = text.find(begin)
if idx < 0:
    sys.exit(0)
end_idx = text.find(end)
if end_idx < 0 or end_idx <= idx:
    sys.exit(0)
new_text = text[:idx].rstrip() + "\n" + text[end_idx + len(end):].lstrip("\n")
path.write_text(new_text, encoding="utf-8")
PY
}

# ─────────────────────────────────────────────────────────────────────────
# 4. 清理单个 agent 的 Agent Link 状态
# ─────────────────────────────────────────────────────────────────────────
remove_agent_paths() {
  local short_id="$1"

  # Channels
  rm -rf "$OPENCLAW_HOME/channels/aimoo/$short_id" 2>/dev/null || true
  if [ -d "$OPENCLAW_HOME/channels/aimoo" ] && [ -z "$(ls -A "$OPENCLAW_HOME/channels/aimoo" 2>/dev/null)" ]; then
    rmdir "$OPENCLAW_HOME/channels/aimoo" 2>/dev/null || true
  fi

  # Workspace .agent-link
  rm -rf "$OPENCLAW_HOME/workspace/$short_id/.agent-link" 2>/dev/null || true
  rm -rf "$OPENCLAW_HOME/workspace-$short_id/.agent-link" 2>/dev/null || true

  # Sessions
  if [ -f "$OPENCLAW_HOME/agents/$short_id/sessions/sessions.json" ]; then
    strip_aimoo_sessions "$OPENCLAW_HOME/agents/$short_id/sessions/sessions.json"
  fi

  # TOOLS.md sections
  for ws in "$OPENCLAW_HOME/workspace/$short_id" "$OPENCLAW_HOME/workspace-$short_id"; do
    tools="$ws/TOOLS.md"
    if [ -f "$tools" ]; then
      strip_tools_section "$tools"
    fi
  done
  # Also clean workspace root TOOLS.md if it has the section
  if [ -f "$OPENCLAW_HOME/workspace/TOOLS.md" ]; then
    strip_tools_section "$OPENCLAW_HOME/workspace/TOOLS.md"
  fi
}

# ─────────────────────────────────────────────────────────────────────────
# 5. 通知 Hub 注销 agent
# ─────────────────────────────────────────────────────────────────────────
unregister_from_hub() {
  local short_id="$1"
  local auth_token
  auth_token=$(get_auth_token_from_state "$short_id")
  local connect_url
  connect_url=$(get_connect_url_from_state "$short_id")

  if [ -z "$auth_token" ] || [ -z "$connect_url" ]; then
    echo "  Hub 注销跳过: authToken 或 connectUrl 为空 ($short_id)"
    return 0
  fi

  local base_url
  base_url=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$connect_url').scheme + '://' + urlparse('$connect_url').netloc)" 2>/dev/null || true)
  if [ -z "$base_url" ]; then
    echo "  Hub 注销跳过: 无法解析 baseUrl ($short_id)"
    return 0
  fi

  echo "  Hub 注销: $short_id → $base_url/v1/agent-link/unregister"
  local resp
  resp=$(curl -sS -X POST "$base_url/v1/agent-link/unregister" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $auth_token" \
    -d '{"confirm": true}' \
    --connect-timeout 5 --max-time 10 2>&1) || true
  echo "  Hub 注销响应: $resp"
}

# ─────────────────────────────────────────────────────────────────────────
# 6. 远程注销 Hub 侧 agent 记录
# ─────────────────────────────────────────────────────────────────────────
remote_unregister() {
  echo ""
  echo "=== 远程注销 Hub 侧 agent 记录 ==="

  # 查找 openclaw 命令
  local openclaw_cmd
  openclaw_cmd=$(find_openclaw_cmd)

  if [ -z "$openclaw_cmd" ]; then
    echo "⚠️  未找到 openclaw 命令，跳过远程注销"
    return 0
  fi

  echo "  使用 openclaw: $openclaw_cmd"

  # 获取所有 agent IDs
  local agent_ids
  agent_ids=$(get_agent_ids)

  echo "  找到的 agent IDs: ${agent_ids:-无}"

  # 清理 Hub 侧残留 services（必须在 agent remove 之前，否则 state.json 被删除后无法读取 authToken）
  echo ""
  echo "=== 清理 Hub 侧残留 services ==="

  # 先通过 docs-test 接口硬删除所有 INACTIVE 服务
  echo "  硬删除所有 INACTIVE 服务..."
  DELETE_RESULT=$(curl -fsS -m 10 -X DELETE "$HUB_URL/v1/docs-test/services?status=INACTIVE" 2>/dev/null || true)
  if echo "$DELETE_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('data',{}).get('deleted',0))" 2>/dev/null; then
    DELETED=$(echo "$DELETE_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('data',{}).get('deleted',0))" 2>/dev/null || echo 0)
    echo "  ✅ 已硬删除 $DELETED 个 INACTIVE 服务"
  else
    echo "  ⚠️ 批量删除失败或无需删除"
  fi

  # 再逐个停用仍为 ACTIVE 的服务
  local services_cleaned=0
  if [ -d "$OPENCLAW_HOME/channels/aimoo" ]; then
    for state_file in "$OPENCLAW_HOME/channels/aimoo/"*/state.json; do
      [ -f "$state_file" ] || continue
      local agent_dir
      agent_dir=$(dirname "$state_file")
      local agent_name
      agent_name=$(basename "$agent_dir")
      echo "  检查 $agent_name 的 ACTIVE services..."
      local auth_token
      auth_token=$(python3 -c "
import json
try:
    with open('$state_file') as f:
        d = json.load(f)
    print(d.get('authToken', d.get('auth_token', '')))
except: pass
" 2>/dev/null || true)
      if [ -z "$auth_token" ]; then
        echo "    ⚠️ 无 auth token，跳过"
        continue
      fi
      # 列出该 agent 的 services
      local services
      services=$(curl -fsS -m 10 -H "Authorization: Bearer $auth_token" "$HUB_URL/v1/services" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    items = data.get('data', [])
    for s in items:
        print(s.get('service_id', ''))
except: pass
" 2>/dev/null || true)
      if [ -n "$services" ]; then
        for svc_id in $services; do
          echo "    停用 service: $svc_id"
          curl -fsS -m 10 -X PATCH "$HUB_URL/v1/services/$svc_id" \
            -H "Authorization: Bearer $auth_token" \
            -H "Content-Type: application/json" \
            -d '{"status":"INACTIVE"}' 2>/dev/null && echo "      ✅ 已停用" || echo "      ⚠️ 停用失败"
          services_cleaned=$((services_cleaned + 1))
        done
      else
        echo "    无 ACTIVE services"
      fi
    done
  fi
  if [ "$services_cleaned" -gt 0 ]; then
    echo "✅ 已停用 $services_cleaned 个 services"
  else
    echo "ℹ️  无残留 ACTIVE services"
  fi

  # 远程注销 agent（在 services 清理之后）
  echo ""
  echo "=== 远程注销 Hub 侧 agent 记录 ==="
  if [ -n "$agent_ids" ]; then
    for agent_id in $agent_ids; do
      echo "  注销 agent: $agent_id"
      # 检查是否有 state.json 文件（包含 authToken）
      local state_file="$OPENCLAW_HOME/channels/aimoo/$agent_id/state.json"
      if [ -f "$state_file" ]; then
        # 优先使用 Hub API 直接注销（不依赖 node）
        local auth_token
        auth_token=$(get_auth_token_from_state "$agent_id")
        local connect_url
        connect_url=$(get_connect_url_from_state "$agent_id")
        if [ -n "$auth_token" ] && [ -n "$connect_url" ]; then
          local base_url
          base_url=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$connect_url').scheme + '://' + urlparse('$connect_url').netloc)" 2>/dev/null || true)
          if [ -n "$base_url" ]; then
            echo "  通过 Hub API 注销: $agent_id → $base_url/v1/agent-link/unregister"
            local resp
            resp=$(curl -sS -X POST "$base_url/v1/agent-link/unregister" \
              -H "Content-Type: application/json" \
              -H "Authorization: Bearer $auth_token" \
              -d '{"confirm": true}' \
              --connect-timeout 5 --max-time 10 2>&1) || true
            if echo "$resp" | python3 -c "import json,sys; d=json.load(sys.stdin); data=d.get('data',{}); print('ok' if data.get('status')=='INACTIVE' or d.get('success') else 'fail')" 2>/dev/null | grep -q "ok"; then
              echo "  ✅ $agent_id 已通过 API 注销"
            else
              echo "  ⚠️ $agent_id API 注销失败: $resp"
              echo "  尝试 openclaw CLI 方式..."
              $SUDO_CMD "$openclaw_cmd" aimoo --agent "$agent_id" remove 2>&1 || echo "  ⚠️ $agent_id CLI 注销也失败"
            fi
          else
            echo "  ⚠️ 无法解析 baseUrl，尝试 openclaw CLI..."
            $SUDO_CMD "$openclaw_cmd" aimoo --agent "$agent_id" remove 2>&1 || echo "  ⚠️ $agent_id 远程注销失败"
          fi
        else
          echo "  ⚠️ authToken 或 connectUrl 为空，尝试 openclaw CLI..."
          $SUDO_CMD "$openclaw_cmd" aimoo --agent "$agent_id" remove 2>&1 || echo "  ⚠️ $agent_id 远程注销失败"
        fi
      elif [ -n "$AUTH_TOKEN" ]; then
        # 使用用户提供的 auth token 调用 Hub API 注销
        echo "  使用提供的 auth token 调用 Hub API 注销..."
        local unregister_result
        unregister_result=$(curl -fsS -m 10 -X POST "$HUB_URL/v1/agent-link/unregister" \
          -H "Authorization: Bearer $AUTH_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"confirm": true}' 2>/dev/null || true)
        if echo "$unregister_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null | grep -q "True"; then
          echo "  ✅ $agent_id 已通过 API 注销"
        else
          echo "  ⚠️ $agent_id API 注销失败: $unregister_result"
        fi
      else
        echo "  ⚠️ $agent_id 无 state.json 文件，无法执行远程注销"
        echo "     如需手动注销，请在 Hub Web UI 或执行: curl -X POST '$HUB_URL/v1/agent-link/unregister' -H 'Authorization: Bearer <token>'"
      fi
    done
    echo "✅ 远程注销完成"
  elif [ -n "$AUTH_TOKEN" ]; then
    # 没有找到 agent IDs，但有 auth token，尝试注销所有 agent
    echo "  未找到本地 agent 配置，使用 auth token 查询 Hub 侧 agent 列表..."
    local agents_list
    agents_list=$(curl -fsS -m 10 -H "Authorization: Bearer $AUTH_TOKEN" "$HUB_URL/v1/agents" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    items = data.get('data', [])
    for a in items:
        print(a.get('agent_id', ''))
except: pass
" 2>/dev/null || true)
    if [ -n "$agents_list" ]; then
      for agent_id in $agents_list; do
        echo "  注销 agent: $agent_id"
        local unregister_result
        unregister_result=$(curl -fsS -m 10 -X POST "$HUB_URL/v1/agent-link/unregister" \
          -H "Authorization: Bearer $AUTH_TOKEN" \
          -H "Content-Type: application/json" \
          -d '{"confirm": true}' 2>/dev/null || true)
        if echo "$unregister_result" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null | grep -q "True"; then
          echo "  ✅ $agent_id 已通过 API 注销"
        else
          echo "  ⚠️ $agent_id API 注销失败: $unregister_result"
        fi
      done
      echo "✅ 远程注销完成"
    else
      echo "ℹ️  Hub 侧未找到 agent 记录"
    fi
  else
    echo "ℹ️  未找到 aimoo instance，跳过远程注销"
    echo "   如需手动注销 Hub 侧 agent，请访问: $HUB_URL/docs"
    echo "   或使用: bash tests/reset_agent_link.sh --all --remove-remote --auth-token <token>"
  fi
}

# ─────────────────────────────────────────────────────────────────────────
# 7. 删除插件和 skill 目录
# ─────────────────────────────────────────────────────────────────────────
remove_plugin_and_skill() {
  local plugin_dir="$OPENCLAW_HOME/plugins/aimoo-link"
  local skill_dir="$OPENCLAW_HOME/skills/aimoo"

  $SUDO_CMD rm -rf "$plugin_dir" 2>/dev/null || true
  echo "✅ 已删除插件目录: $plugin_dir"

  if [ -d "$skill_dir" ]; then
    $SUDO_CMD rm -rf "$skill_dir" 2>/dev/null || true
    echo "✅ 已删除 skill 目录: $skill_dir"
  fi
}

# ─────────────────────────────────────────────────────────────────────────
# 主逻辑
# ─────────────────────────────────────────────────────────────────────────

# 1. 远程注销（先做，因为需要 openclaw 命令可用）
if [ "$REMOVE_REMOTE" = "true" ]; then
  remote_unregister
fi

# 2. 本地清理
echo ""
echo "=== 本地清理 ==="

if [ "$MODE" = "all" ]; then
  echo "清理所有 Agent Link 状态..."

  # All channels
  rm -rf "$OPENCLAW_HOME/channels/aimoo" 2>/dev/null || true

  # All .agent-link dirs
  if [ -d "$OPENCLAW_HOME/workspace" ]; then
    find "$OPENCLAW_HOME/workspace" -maxdepth 3 -type d -name .agent-link -exec rm -rf {} + 2>/dev/null || true
    find "$OPENCLAW_HOME/workspace" -maxdepth 3 -type f -name install-result.json -path "*/.agent-link/*" -delete 2>/dev/null || true
  fi
  if [ -d "$OPENCLAW_HOME" ]; then
    for d in "$OPENCLAW_HOME"/workspace-*/.agent-link; do
      [ -d "$d" ] && rm -rf "$d"
    done
  fi

  # All sessions
  if [ -d "$OPENCLAW_HOME/agents" ]; then
    find "$OPENCLAW_HOME/agents" -name sessions.json -path "*/sessions/sessions.json" -print0 2>/dev/null | while IFS= read -r -d '' f; do
      strip_aimoo_sessions "$f"
    done
  fi

  # All TOOLS.md sections
  if [ -d "$OPENCLAW_HOME/workspace" ]; then
    find "$OPENCLAW_HOME/workspace" -maxdepth 4 -name TOOLS.md -print0 2>/dev/null | while IFS= read -r -d '' f; do
      strip_tools_section "$f"
    done
  fi

else
  echo "清理 agent $TARGET_AGENT 的 Agent Link 状态..."
  remove_agent_paths "$TARGET_AGENT"
fi

# Clean openclaw.json
clean_openclaw_json

# 3. 删除插件和 skill（如果请求）
if [ "$REMOVE_PLUGIN" = "true" ]; then
  echo ""
  echo "=== 删除插件和 skill ==="
  remove_plugin_and_skill
fi

echo ""
echo "=========================================="
echo "✅ Agent Link (aimoo-link) 状态清理完成"
echo "=========================================="
echo "模式: $MODE"
echo "目标 agent: $TARGET_AGENT"
echo "删除插件: $REMOVE_PLUGIN"
echo "远程注销: $REMOVE_REMOTE"
echo ""
echo "重启 Gateway："
echo "  systemctl --user restart openclaw-gateway.service"
echo ""
echo "如需完全重装，请在 agent 会话中发送安装命令。"
