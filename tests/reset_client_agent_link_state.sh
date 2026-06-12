#!/usr/bin/env bash
set -euo pipefail

MODE="agent"
TARGET_AGENT="main"
REMOVE_PLUGIN="false"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
PLUGIN_DIR="$OPENCLAW_HOME/plugins/aimoo-link"
CONFIG_FILE="$OPENCLAW_HOME/openclaw.json"

usage() {
  cat <<'EOF'
用法:
  reset_client_agent_link_state.sh [--agent <id> | --all] [--remove-plugin]

说明:
  清理 OpenClaw 客户端的 Agent Link (aimoo-link) 测试状态。
  --agent  指定要清理的 agent 短 id（默认 main）
  --all    清理所有 agent 的 Agent Link 状态
  --remove-plugin  同时删除 aimoo-link 插件目录

影响范围:
  - 使用 OPENCLAW_HOME（默认 ~/.openclaw）
  - 更新 openclaw.json 中的 channels.aimoo / plugins.aimoo-link 配置
  - 删除 channels/aimoo/<agent> 和 workspace*/.agent-link
  - 清理 sessions.json 中 sessionId 以 aimoo: 开头的会话
  - 删除 TOOLS.md 中 A2A_HUB_AGENT_LINK_BEGIN/END 标记区段
EOF
}

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

# ─────────────────────────────────────────────────────────────────────────
# 1. 清理 openclaw.json 中的 channels.aimoo 和 plugins.aimoo-link 配置
# ─────────────────────────────────────────────────────────────────────────
clean_openclaw_json() {
  python3 - "$CONFIG_FILE" "$MODE" "$TARGET_AGENT" "$PLUGIN_DIR" <<'PY'
import json
import sys
from pathlib import Path

config_file = Path(sys.argv[1]).expanduser()
mode = sys.argv[2]
target = sys.argv[3]
plugin_dir = sys.argv[4]

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

# Clean plugins section
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
        plugins.pop("aimoo-link", None)
        changed = True
        if not entries:
            plugins.pop("entries", None)

    if not plugins:
        data.pop("plugins", None)

if changed:
    config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
# 主逻辑
# ─────────────────────────────────────────────────────────────────────────
if [ "$MODE" = "all" ]; then
  echo "清理所有 Agent Link 状态..."
  clean_openclaw_json

  # All channels
  rm -rf "$OPENCLAW_HOME/channels/aimoo" 2>/dev/null || true

  # All .agent-link dirs
  if [ -d "$OPENCLAW_HOME/workspace" ]; then
    find "$OPENCLAW_HOME/workspace" -maxdepth 3 -type d -name .agent-link -exec rm -rf {} + 2>/dev/null || true
    find "$OPENCLAW_HOME/workspace" -maxdepth 3 -type f -name install-result.json -path "*/.agent-link/*" -delete 2>/dev/null || true
  fi
  if [ -d "$OPENCLAW_HOME" ]; then
    find "$OPENCLAW_HOME" -maxdepth 1 -type d -name "workspace-*" -exec rm -rf {}/.agent-link 2>/dev/null || true
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
  clean_openclaw_json
  remove_agent_paths "$TARGET_AGENT"
fi

# Remove plugin directory if requested
if [ "$REMOVE_PLUGIN" = "true" ]; then
  rm -rf "$PLUGIN_DIR"
  echo "已删除插件目录: $PLUGIN_DIR"
fi

echo ""
echo "✅ Agent Link (aimoo-link) 测试状态已清理完成。"
echo "   模式: $MODE"
echo "   目标: $TARGET_AGENT"
echo "   删除插件: $REMOVE_PLUGIN"
echo ""
echo "如需完全重装，请在 agent 会话中发送安装命令。"
