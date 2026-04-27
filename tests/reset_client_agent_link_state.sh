#!/usr/bin/env bash
set -euo pipefail

MODE="agent"
TARGET_AGENT="main"
REMOVE_PLUGIN="false"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
PLUGIN_DIR="$OPENCLAW_HOME/plugins/aimoo-link"

usage() {
  cat <<'EOF'
用法:
  reset_client_agent_link_state.sh [--agent <id> | --all] [--remove-plugin]

说明:
  清理 OpenClaw 客户端的 Agent Link (aimoo-link) 状态。
  --agent  指定要清理的 agent 短 id（默认 main）
  --all    清理所有 agent 的 Agent Link 状态
  --remove-plugin  同时删除 aimoo-link 插件目录
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

CONFIG_FILE="$OPENCLAW_HOME/openclaw.json"

python3 - "$CONFIG_FILE" "$MODE" "$TARGET_AGENT" <<'PY'
import json
import sys
from pathlib import Path

config_file = Path(sys.argv[1]).expanduser()
mode = sys.argv[2]
target = sys.argv[3]
plugin_dir = str((config_file.parent / "plugins" / "aimoo-link").expanduser())

if not config_file.exists():
    raise SystemExit(0)

data = json.loads(config_file.read_text(encoding="utf-8"))
remove_aimoo_config = mode == "all"

channels = data.get("channels")
if isinstance(channels, dict):
    aimoo = channels.get("aimoo")
    if isinstance(aimoo, dict):
        instances = aimoo.get("instances")
        if isinstance(instances, list):
            kept = []
            for item in instances:
                local_agent = str(item.get("localAgentId") or item.get("agentId") or "").split(":")[-1]
                remove = mode == "all" or local_agent == target
                if not remove:
                    kept.append(item)
            if kept:
                aimoo["instances"] = kept
            else:
                aimoo.pop("instances", None)

        if mode == "all":
            channels.pop("aimoo", None)
        else:
            top_agent = str(aimoo.get("localAgentId") or aimoo.get("agentId") or "").split(":")[-1]
            if top_agent == target:
                channels.pop("aimoo", None)
            elif not aimoo.get("instances") and not top_agent:
                channels.pop("aimoo", None)

        if not channels.get("aimoo"):
            channels.pop("aimoo", None)
            remove_aimoo_config = True

plugins = data.get("plugins")
if remove_aimoo_config and isinstance(plugins, dict):
    allow = plugins.get("allow")
    if isinstance(allow, list):
        allow = [item for item in allow if item != "aimoo-link"]
        if allow:
            plugins["allow"] = allow
        else:
            plugins.pop("allow", None)

    load = plugins.get("load")
    if isinstance(load, dict):
        paths = load.get("paths")
        if isinstance(paths, list):
            kept_paths = []
            for item in paths:
                if not isinstance(item, str):
                    kept_paths.append(item)
                    continue
                normalized = str(Path(item).expanduser())
                if normalized != plugin_dir:
                    kept_paths.append(item)
            if kept_paths:
                load["paths"] = kept_paths
            else:
                load.pop("paths", None)
        if not load:
            plugins.pop("load", None)

    entries = plugins.get("entries")
    if isinstance(entries, dict):
        entries.pop("aimoo-link", None)
        if not entries:
            plugins.pop("entries", None)

    if not plugins:
        data.pop("plugins", None)

# Clean up empty top-level sections
if isinstance(data.get("channels"), dict) and not data["channels"]:
    data.pop("channels", None)
if isinstance(data.get("plugins"), dict) and not data["plugins"]:
    data.pop("plugins", None)

config_file.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

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
    raise SystemExit(0)

changed = False
for key, value in list(data.items()):
    if isinstance(value, dict) and str(value.get("sessionId") or "").startswith("aimoo:"):
        data.pop(key, None)
        changed = True

if changed:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

remove_agent_paths() {
  local short_id="$1"
  rm -rf "$OPENCLAW_HOME/channels/aimoo/$short_id"
  # Remove aimoo channel dir entirely if empty after removing agent subdir
  if [ -d "$OPENCLAW_HOME/channels/aimoo" ] && [ -z "$(ls -A "$OPENCLAW_HOME/channels/aimoo" 2>/dev/null)" ]; then
    rmdir "$OPENCLAW_HOME/channels/aimoo" 2>/dev/null || true
  fi
  rm -rf "$OPENCLAW_HOME/workspace/$short_id/.agent-link"
  rm -rf "$OPENCLAW_HOME/workspace-$short_id/.agent-link"
  if [ -f "$OPENCLAW_HOME/agents/$short_id/sessions/sessions.json" ]; then
    strip_aimoo_sessions "$OPENCLAW_HOME/agents/$short_id/sessions/sessions.json"
  fi
  # Remove agent-link section from TOOLS.md (workspace or workspace-<id>)
  for ws in "$OPENCLAW_HOME/workspace/$short_id" "$OPENCLAW_HOME/workspace-$short_id" "$OPENCLAW_HOME/workspace"; do
    local tools="$ws/TOOLS.md"
    if [ -f "$tools" ]; then
      python3 - "$tools" <<'PY_TOOLS'
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    text = path.read_text(encoding="utf-8")
except Exception:
    raise SystemExit(0)

begin = "<!-- A2A_HUB_AGENT_LINK_BEGIN -->"
end = "<!-- A2A_HUB_AGENT_LINK_END -->"
idx = text.find(begin)
if idx < 0:
    raise SystemExit(0)
end_idx = text.find(end)
if end_idx < 0 or end_idx <= idx:
    raise SystemExit(0)
new_text = text[:idx].rstrip() + "\n" + text[end_idx + len(end):].lstrip("\n")
path.write_text(new_text, encoding="utf-8")
PY_TOOLS
    fi
  done
}

if [ "$MODE" = "all" ]; then
  rm -rf "$OPENCLAW_HOME/channels/aimoo"
  if [ -d "$OPENCLAW_HOME/workspace" ]; then
    find "$OPENCLAW_HOME/workspace" -maxdepth 2 -type d -name .agent-link -exec rm -rf {} + 2>/dev/null || true
    find "$OPENCLAW_HOME/workspace" -maxdepth 2 -type f -name install-result.json -path '*/.agent-link/*' -exec rm -f {} + 2>/dev/null || true
  fi
  if [ -d "$OPENCLAW_HOME" ]; then
    find "$OPENCLAW_HOME" -maxdepth 1 -type d -name 'workspace-*' -print0 2>/dev/null | while IFS= read -r -d '' dir; do
      rm -rf "$dir/.agent-link"
    done
  fi
  if [ -d "$OPENCLAW_HOME/agents" ]; then
    find "$OPENCLAW_HOME/agents" -path '*/sessions/sessions.json' -type f -print0 2>/dev/null | while IFS= read -r -d '' file; do
      strip_aimoo_sessions "$file"
    done
  fi
  # Remove agent-link sections from all workspace TOOLS.md files
  find "$OPENCLAW_HOME" -maxdepth 3 -name 'TOOLS.md' -path '*/workspace*/TOOLS.md' -print0 2>/dev/null | while IFS= read -r -d '' f; do
    python3 - "$f" <<'PY_TOOLS_ALL'
import sys
from pathlib import Path

path = Path(sys.argv[1]).expanduser()
try:
    text = path.read_text(encoding="utf-8")
except Exception:
    raise SystemExit(0)

begin = "<!-- A2A_HUB_AGENT_LINK_BEGIN -->"
end = "<!-- A2A_HUB_AGENT_LINK_END -->"
idx = text.find(begin)
if idx < 0:
    raise SystemExit(0)
end_idx = text.find(end)
if end_idx < 0 or end_idx <= idx:
    raise SystemExit(0)
new_text = text[:idx].rstrip() + "\n" + text[end_idx + len(end):].lstrip("\n")
path.write_text(new_text, encoding="utf-8")
PY_TOOLS_ALL
  done
else
  remove_agent_paths "$TARGET_AGENT"
fi

if [ "$REMOVE_PLUGIN" = "true" ]; then
  rm -rf "$PLUGIN_DIR"
fi

echo "客户端 Agent Link (aimoo-link) 测试状态已清理。"
echo "OPENCLAW_HOME=$OPENCLAW_HOME mode=$MODE target=$TARGET_AGENT remove_plugin=$REMOVE_PLUGIN"