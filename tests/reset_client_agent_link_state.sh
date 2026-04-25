#!/usr/bin/env bash
set -euo pipefail

MODE="agent"
TARGET_AGENT="main"
REMOVE_PLUGIN="false"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
PLUGIN_DIR="$OPENCLAW_HOME/plugins/aimoo-link"

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
    *)
      echo "未知参数: $1" >&2
      echo "用法: $0 [--agent <id> | --all] [--remove-plugin]" >&2
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
remove_aimoo_plugin_config = mode == "all"

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
                # An empty channel without plugin load config makes OpenClaw abort
                # with: channels.aimoo: unknown channel id: aimoo.
                channels.pop("aimoo", None)

        if not channels.get("aimoo"):
            channels.pop("aimoo", None)
            remove_aimoo_plugin_config = True

plugins = data.get("plugins")
if remove_aimoo_plugin_config and isinstance(plugins, dict):
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
  rm -rf "$OPENCLAW_HOME/workspace/$short_id/.agent-link"
  rm -rf "$OPENCLAW_HOME/workspace-$short_id/.agent-link"
  rm -f "$OPENCLAW_HOME/workspace/$short_id/.agent-link/install-check.log"
  rm -f "$OPENCLAW_HOME/workspace/$short_id/.agent-link/install-check.log"
  if [ -f "$OPENCLAW_HOME/agents/$short_id/sessions/sessions.json" ]; then
    strip_aimoo_sessions "$OPENCLAW_HOME/agents/$short_id/sessions/sessions.json"
  fi
}

if [ "$MODE" = "all" ]; then
  rm -rf "$OPENCLAW_HOME/channels/aimoo"
  if [ -d "$OPENCLAW_HOME/workspace" ]; then
    find "$OPENCLAW_HOME/workspace" -maxdepth 2 -type d -name .agent-link -exec rm -rf {} + 2>/dev/null || true
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
else
  remove_agent_paths "$TARGET_AGENT"
fi

if [ "$REMOVE_PLUGIN" = "true" ]; then
  rm -rf "$PLUGIN_DIR"
fi

echo "客户端 Agent Link 测试状态已清理。"
echo "OPENCLAW_HOME=$OPENCLAW_HOME mode=$MODE target=$TARGET_AGENT remove_plugin=$REMOVE_PLUGIN"