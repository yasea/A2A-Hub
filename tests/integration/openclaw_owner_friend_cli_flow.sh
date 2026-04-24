#!/usr/bin/env bash
set -euo pipefail

# Real owner-to-agent smoke flow.
#
# This script does not edit OpenClaw config, does not write TOOLS.md, and does
# not restart the Gateway. It simulates the owner sending natural-language
# instructions to already-connected OpenClaw agents, asking them to use the
# dbim_mqtt local CLI generated under .agent-link.

OPENCLAW_CMD=${OPENCLAW_CMD:-openclaw}
MAIN_AGENT=${MAIN_AGENT:-main}
TARGET_AGENT=${TARGET_AGENT:-ava}
PUBLIC_FRIEND_TOOLS_URL=${PUBLIC_FRIEND_TOOLS_URL:-}
OPENCLAW_HOME=${OPENCLAW_HOME:-$HOME/.openclaw}
TIMEOUT=${TIMEOUT:-180}
RUN_ID=${RUN_ID:-$(date +%Y%m%d%H%M%S)}
WORK_DIR=${WORK_DIR:-/tmp/a2a-hub-owner-flow-$RUN_ID}

MAIN_CLI=${MAIN_CLI:-$OPENCLAW_HOME/workspace-$MAIN_AGENT/.agent-link/agent-linkctl}
TARGET_CLI=${TARGET_CLI:-$OPENCLAW_HOME/workspace-$TARGET_AGENT/.agent-link/agent-linkctl}

mkdir -p "$WORK_DIR"

require_file() {
  if [ ! -x "$1" ]; then
    echo "缺少可执行 CLI: $1" >&2
    echo "请先完成 Agent Link 安装，或通过 MAIN_CLI/TARGET_CLI 指定路径。" >&2
    exit 2
  fi
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少命令: $1" >&2
    exit 2
  fi
}

agent_message() {
  local agent="$1"
  local session="$2"
  local message="$3"
  local output="$4"
  "$OPENCLAW_CMD" agent \
    --agent "$agent" \
    --session-id "$session" \
    --message "$message" \
    --timeout "$TIMEOUT" \
    --json | tee "$output"
}

extract_first_invite_url() {
  python3 - "$1" <<'PY'
import re
import sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
match = re.search(r'https?://[^\s"\']+/v1/agents/invite\?token=[A-Za-z0-9._~:+/\-=%]+', text)
if match:
    print(match.group(0))
PY
}

extract_first_number_after_friend() {
  python3 - "$1" <<'PY'
import re
import sys
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
patterns = [
    r'"friend_id"\s*:\s*(\d+)',
    r'"id"\s*:\s*(\d+)',
    r'friend[_ -]?id[:= ]+(\d+)',
]
for pattern in patterns:
    match = re.search(pattern, text, re.I)
    if match:
        print(match.group(1))
        break
PY
}

extract_existing_accepted_friend() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
path = sys.argv[1]
target = sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
for item in data.get("friends", []):
    peer = item.get("target_agent_id") or item.get("peer_agent_id") or ""
    requester = item.get("requester_agent_id") or ""
    if item.get("status") == "ACCEPTED" and (peer == target or requester == target):
        print(item.get("id") or item.get("friend_id") or "")
        break
PY
}

echo "== A2A Hub owner-to-agent CLI flow =="
echo "main=$MAIN_AGENT target=$TARGET_AGENT work_dir=$WORK_DIR"

require_command "$OPENCLAW_CMD"
require_command python3
require_command curl
require_file "$MAIN_CLI"
require_file "$TARGET_CLI"

if [ -z "$PUBLIC_FRIEND_TOOLS_URL" ]; then
  PUBLIC_FRIEND_TOOLS_URL="$("$MAIN_CLI" urls | python3 -c 'import json,sys; print(json.load(sys.stdin).get("friend_tools_url",""))' 2>/dev/null || true)"
fi
if [ -z "$PUBLIC_FRIEND_TOOLS_URL" ]; then
  echo "无法从 $MAIN_CLI urls 解析 friend_tools_url，请显式传 PUBLIC_FRIEND_TOOLS_URL。" >&2
  exit 2
fi
if ! curl -fsS "$PUBLIC_FRIEND_TOOLS_URL" >/dev/null; then
  echo "公开好友说明 URL 不可访问: $PUBLIC_FRIEND_TOOLS_URL" >&2
  exit 2
fi

DIRECT_FRIEND_JSON="$WORK_DIR/00-main-friends.json"
EXISTING_FRIEND_ID=""
if "$MAIN_CLI" friends >"$DIRECT_FRIEND_JSON" 2>/dev/null; then
  EXISTING_FRIEND_ID="$(extract_existing_accepted_friend "$DIRECT_FRIEND_JSON" "openclaw:$TARGET_AGENT" || true)"
fi

echo
echo "1) 主人让 $MAIN_AGENT 做最小诊断"
agent_message "$MAIN_AGENT" "a2a-owner-cli-$RUN_ID-main-check" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本地 dbim_mqtt CLI：$MAIN_CLI 依次执行 status、urls、doctor。只向我报告安全字段，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/01-main-check.json"

if [ -n "$EXISTING_FRIEND_ID" ]; then
  echo
  echo "2) 已存在 accepted 好友关系 friend_id=$EXISTING_FRIEND_ID，跳过 invite/accept"
else
  echo
  echo "2) 主人让 $MAIN_AGENT 提供 invite URL"
  agent_message "$MAIN_AGENT" "a2a-owner-cli-$RUN_ID-main-invite" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本地 dbim_mqtt CLI：$MAIN_CLI invite。只输出 invite_url、agent_id、tenant_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/02-main-invite.json"
  INVITE_URL=$(extract_first_invite_url "$WORK_DIR/02-main-invite.json" || true)
  if [ -z "$INVITE_URL" ]; then
    echo "未能从 $MAIN_AGENT 回复中解析 invite_url，详见 $WORK_DIR/02-main-invite.json" >&2
    exit 3
  fi
  echo "解析到 invite_url: $INVITE_URL"

  echo
  echo "3) 主人把 invite URL 发给 $TARGET_AGENT 添加好友"
  agent_message "$TARGET_AGENT" "a2a-owner-cli-$RUN_ID-target-accept" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本地 dbim_mqtt CLI：$TARGET_CLI accept '$INVITE_URL'。只报告 friend_id、status、context_id、requester_agent_id、target_agent_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/03-target-accept.json"
  FRIEND_ID=$(extract_first_number_after_friend "$WORK_DIR/03-target-accept.json" || true)
  if [ -z "$FRIEND_ID" ]; then
    echo "未能从 $TARGET_AGENT 回复中解析 friend_id，详见 $WORK_DIR/03-target-accept.json" >&2
    exit 4
  fi
  echo "解析到 friend_id: $FRIEND_ID"
fi

echo
echo "4) 主人让 $MAIN_AGENT 列好友并发起对话"
agent_message "$MAIN_AGENT" "a2a-owner-cli-$RUN_ID-main-send" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本地 dbim_mqtt CLI：先执行 $MAIN_CLI friends，确认好友 $TARGET_AGENT 或 openclaw:$TARGET_AGENT 已 accepted；然后执行 $MAIN_CLI send openclaw:$TARGET_AGENT '来自主人真实会话测试，请回复 OWNER_AGENT_CLI_OK。'。只报告 friend_id、task_id、context_id、target_agent_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/04-main-send.json"

if ! grep -Eq 'task_id|OWNER_AGENT_CLI_OK|context_id' "$WORK_DIR/04-main-send.json"; then
  echo "未能确认 $MAIN_AGENT 已创建对话任务，详见 $WORK_DIR/04-main-send.json" >&2
  exit 5
fi

echo
echo "5) 主人让 $TARGET_AGENT 做最小诊断"
agent_message "$TARGET_AGENT" "a2a-owner-cli-$RUN_ID-target-check" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本地 dbim_mqtt CLI：$TARGET_CLI 依次执行 status、doctor。只向我报告安全字段，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/05-target-check.json"

echo
echo "6) 主人让 $TARGET_AGENT 反向给 $MAIN_AGENT 发消息"
agent_message "$TARGET_AGENT" "a2a-owner-cli-$RUN_ID-target-send" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本地 dbim_mqtt CLI：先执行 $TARGET_CLI friends，确认好友 $MAIN_AGENT 或 openclaw:$MAIN_AGENT 已 accepted；然后执行 $TARGET_CLI send openclaw:$MAIN_AGENT '来自主人反向真实会话测试，请回复 OWNER_AGENT_CLI_REPLY_OK。'。只报告 friend_id、task_id、context_id、target_agent_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/06-target-send.json"

if ! grep -Eq 'task_id|OWNER_AGENT_CLI_REPLY_OK|context_id' "$WORK_DIR/06-target-send.json"; then
  echo "未能确认 $TARGET_AGENT 已创建反向对话任务，详见 $WORK_DIR/06-target-send.json" >&2
  exit 6
fi

echo
echo "真实 owner-to-agent CLI 双向 flow 已完成。日志目录：$WORK_DIR"
