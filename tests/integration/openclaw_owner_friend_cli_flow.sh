#!/usr/bin/env bash
set -euo pipefail

# Real owner-to-agent smoke flow.
#
# This script does not edit OpenClaw config, does not write TOOLS.md, and does
# not restart the Gateway. It simulates the owner sending natural-language
# instructions to already-connected OpenClaw agents, asking them to use the
# formal `openclaw aimoo --agent ...` CLI surface.

OPENCLAW_CMD=${OPENCLAW_CMD:-openclaw}
MAIN_OPENCLAW_HOST=${MAIN_OPENCLAW_HOST:-}
MAIN_OPENCLAW_BIN=${MAIN_OPENCLAW_BIN:-$OPENCLAW_CMD}
MAIN_AGENT=${MAIN_AGENT:-main}
TARGET_OPENCLAW_HOST=${TARGET_OPENCLAW_HOST:-}
TARGET_OPENCLAW_BIN=${TARGET_OPENCLAW_BIN:-$OPENCLAW_CMD}
TARGET_AGENT=${TARGET_AGENT:-ava}
PUBLIC_FRIEND_TOOLS_URL=${PUBLIC_FRIEND_TOOLS_URL:-}
OPENCLAW_HOME=${OPENCLAW_HOME:-$HOME/.openclaw}
TIMEOUT=${TIMEOUT:-180}
RUN_ID=${RUN_ID:-$(date +%Y%m%d%H%M%S)}
WORK_DIR=${WORK_DIR:-/tmp/a2a-hub-owner-flow-$RUN_ID}

mkdir -p "$WORK_DIR"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "缺少命令: $1" >&2
    exit 2
  fi
}

run_remote_openclaw() {
  local host="$1"
  local bin="$2"
  shift 2
  local cmd=()
  local part
  cmd+=("$(printf '%q' "$bin")")
  for part in "$@"; do
    cmd+=("$(printf '%q' "$part")")
  done
  ssh "$host" "bash -lc $(printf '%q' "${cmd[*]}")"
}

run_main_cli() {
  if [ -n "$MAIN_OPENCLAW_HOST" ]; then
    run_remote_openclaw "$MAIN_OPENCLAW_HOST" "$MAIN_OPENCLAW_BIN" aimoo --agent "$MAIN_AGENT" "$@"
  else
    "$MAIN_OPENCLAW_BIN" aimoo --agent "$MAIN_AGENT" "$@"
  fi
}

run_target_cli() {
  if [ -n "$TARGET_OPENCLAW_HOST" ]; then
    run_remote_openclaw "$TARGET_OPENCLAW_HOST" "$TARGET_OPENCLAW_BIN" aimoo --agent "$TARGET_AGENT" "$@"
  else
    "$TARGET_OPENCLAW_BIN" aimoo --agent "$TARGET_AGENT" "$@"
  fi
}

agent_message_main() {
  local session="$1"
  local message="$2"
  local output="$3"
  if [ -n "$MAIN_OPENCLAW_HOST" ]; then
    run_remote_openclaw "$MAIN_OPENCLAW_HOST" "$MAIN_OPENCLAW_BIN" \
      agent \
      --agent "$MAIN_AGENT" \
      --session-id "$session" \
      --message "$message" \
      --timeout "$TIMEOUT" \
      --json | tee "$output"
  else
    "$MAIN_OPENCLAW_BIN" agent \
      --agent "$MAIN_AGENT" \
      --session-id "$session" \
      --message "$message" \
      --timeout "$TIMEOUT" \
      --json | tee "$output"
  fi
}

agent_message_target() {
  local session="$1"
  local message="$2"
  local output="$3"
  if [ -n "$TARGET_OPENCLAW_HOST" ]; then
    run_remote_openclaw "$TARGET_OPENCLAW_HOST" "$TARGET_OPENCLAW_BIN" \
      agent \
      --agent "$TARGET_AGENT" \
      --session-id "$session" \
      --message "$message" \
      --timeout "$TIMEOUT" \
      --json | tee "$output"
  else
    "$TARGET_OPENCLAW_BIN" agent \
      --agent "$TARGET_AGENT" \
      --session-id "$session" \
      --message "$message" \
      --timeout "$TIMEOUT" \
      --json | tee "$output"
  fi
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
target_number = int(target) if target.isdigit() else None
text = open(path, encoding="utf-8", errors="replace").read()
decoder = json.JSONDecoder()
data = {}
for index, ch in enumerate(text):
    if ch != "{":
        continue
    try:
        obj, _ = decoder.raw_decode(text[index:])
    except Exception:
        continue
    if isinstance(obj, dict) and "friends" in obj:
        data = obj
        break
for item in data.get("friends", []):
    peer = item.get("target_agent_id") or item.get("peer_agent_id") or ""
    requester = item.get("requester_agent_id") or ""
    peer_number = item.get("peer_public_number") or item.get("target_public_number") or item.get("requester_public_number")
    if item.get("status") == "ACCEPTED" and (peer == target or requester == target or (target_number and peer_number == target_number)):
        print(item.get("id") or item.get("friend_id") or "")
        break
PY
}

extract_json_field_from_mixed_output() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

field = sys.argv[1]
text = sys.argv[2]
decoder = json.JSONDecoder()
candidates = []
for index, ch in enumerate(text):
    if ch != "{":
        continue
    try:
        obj, _ = decoder.raw_decode(text[index:])
    except Exception:
        continue
    candidates.append(obj)
for obj in reversed(candidates):
    value = obj.get(field, "")
    if value:
        print(value)
        break
PY
}

echo "== A2A Hub owner-to-agent CLI flow =="
echo "main=$MAIN_AGENT target=$TARGET_AGENT work_dir=$WORK_DIR"

require_command "$OPENCLAW_CMD"
require_command python3
require_command curl
if [ -n "$MAIN_OPENCLAW_HOST" ] || [ -n "$TARGET_OPENCLAW_HOST" ]; then
  require_command ssh
fi

if [ -z "$PUBLIC_FRIEND_TOOLS_URL" ]; then
  PUBLIC_FRIEND_TOOLS_URL="$(extract_json_field_from_mixed_output friend_tools_url "$(run_main_cli urls 2>/dev/null || true)")"
fi
if [ -z "$PUBLIC_FRIEND_TOOLS_URL" ]; then
  echo "无法从 openclaw aimoo --agent $MAIN_AGENT urls 解析 friend_tools_url，请显式传 PUBLIC_FRIEND_TOOLS_URL。" >&2
  exit 2
fi
if ! curl -fsS "$PUBLIC_FRIEND_TOOLS_URL" >/dev/null; then
  echo "公开好友说明 URL 不可访问: $PUBLIC_FRIEND_TOOLS_URL" >&2
  exit 2
fi

MAIN_PUBLIC_NUMBER="$(extract_json_field_from_mixed_output public_number "$(run_main_cli me 2>/dev/null || true)")"
TARGET_PUBLIC_NUMBER="$(extract_json_field_from_mixed_output public_number "$(run_target_cli me 2>/dev/null || true)")"
MAIN_REF="${MAIN_PUBLIC_NUMBER:-openclaw:$MAIN_AGENT}"
TARGET_REF="${TARGET_PUBLIC_NUMBER:-openclaw:$TARGET_AGENT}"
echo "main_ref=$MAIN_REF target_ref=$TARGET_REF"

DIRECT_FRIEND_JSON="$WORK_DIR/00-main-friends.json"
EXISTING_FRIEND_ID=""
if run_main_cli friends >"$DIRECT_FRIEND_JSON" 2>/dev/null; then
  EXISTING_FRIEND_ID="$(extract_existing_accepted_friend "$DIRECT_FRIEND_JSON" "$TARGET_REF" || true)"
fi

echo
echo "1) 主人让 $MAIN_AGENT 做最小诊断"
agent_message_main "a2a-owner-cli-$RUN_ID-main-check" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本机 OpenClaw CLI：openclaw aimoo --agent $MAIN_AGENT 依次执行 status、urls、doctor。只向我报告安全字段，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/01-main-check.json"

if [ -n "$EXISTING_FRIEND_ID" ]; then
  echo
  echo "2) 已存在 accepted 好友关系 friend_id=$EXISTING_FRIEND_ID，跳过 invite/accept"
else
  echo
  echo "2) 主人让 $MAIN_AGENT 按好友号发起好友请求"
  agent_message_main "a2a-owner-cli-$RUN_ID-main-request" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本机 OpenClaw CLI：openclaw aimoo --agent $MAIN_AGENT request '$TARGET_REF' '来自主人真实会话测试，请求建立好友关系'。只报告 friend_id、status、peer_public_number、peer_agent_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/02-main-request.json"
  FRIEND_ID=$(extract_first_number_after_friend "$WORK_DIR/02-main-request.json" || true)
  if [ -z "$FRIEND_ID" ]; then
    echo "未能从 $MAIN_AGENT 回复中解析 friend_id，详见 $WORK_DIR/02-main-request.json" >&2
    exit 3
  fi
  echo "解析到 pending friend_id: $FRIEND_ID"

  echo
  echo "3) $TARGET_AGENT 收到好友请求后，由主人确认通过"
  agent_message_target "a2a-owner-cli-$RUN_ID-target-accept" "你可能已经收到 A2A Hub friend.request 通知。请阅读 $PUBLIC_FRIEND_TOOLS_URL ，先执行 openclaw aimoo --agent $TARGET_AGENT friends 查看 friend_id=$FRIEND_ID 的 PENDING 请求；现在主人明确同意通过，所以执行 openclaw aimoo --agent $TARGET_AGENT accept-request '$FRIEND_ID'。只报告 friend_id、status、context_id、requester_public_number、target_public_number，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/03-target-accept.json"
  FRIEND_ID=$(extract_first_number_after_friend "$WORK_DIR/03-target-accept.json" || true)
  if [ -z "$FRIEND_ID" ]; then
    echo "未能从 $TARGET_AGENT 回复中解析 friend_id，详见 $WORK_DIR/03-target-accept.json" >&2
    exit 4
  fi
  echo "解析到 friend_id: $FRIEND_ID"
fi

echo
echo "4) 主人让 $MAIN_AGENT 列好友并发起对话"
agent_message_main "a2a-owner-cli-$RUN_ID-main-send" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本机 OpenClaw CLI：先执行 openclaw aimoo --agent $MAIN_AGENT friends，确认好友 $TARGET_REF 已 accepted；然后执行 openclaw aimoo --agent $MAIN_AGENT send '$TARGET_REF' '来自主人真实会话测试，请回复 OWNER_AGENT_CLI_OK。'。只报告 friend_id、task_id、context_id、target_agent_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/04-main-send.json"

if ! grep -Eq 'task_id|OWNER_AGENT_CLI_OK|context_id' "$WORK_DIR/04-main-send.json"; then
  echo "未能确认 $MAIN_AGENT 已创建对话任务，详见 $WORK_DIR/04-main-send.json" >&2
  exit 5
fi

echo
echo "5) 主人让 $TARGET_AGENT 做最小诊断"
agent_message_target "a2a-owner-cli-$RUN_ID-target-check" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本机 OpenClaw CLI：openclaw aimoo --agent $TARGET_AGENT 依次执行 status、doctor。只向我报告安全字段，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/05-target-check.json"

echo
echo "6) 主人让 $TARGET_AGENT 反向给 $MAIN_AGENT 发消息"
agent_message_target "a2a-owner-cli-$RUN_ID-target-send" "请阅读 $PUBLIC_FRIEND_TOOLS_URL ，然后使用本机 OpenClaw CLI：先执行 openclaw aimoo --agent $TARGET_AGENT friends，确认好友 $MAIN_REF 已 accepted；然后执行 openclaw aimoo --agent $TARGET_AGENT send '$MAIN_REF' '来自主人反向真实会话测试，请回复 OWNER_AGENT_CLI_REPLY_OK。'。只报告 friend_id、task_id、context_id、target_agent_id，不要输出 auth_token、MQTT password 或 Authorization header。" "$WORK_DIR/06-target-send.json"

if ! grep -Eq 'task_id|OWNER_AGENT_CLI_REPLY_OK|context_id' "$WORK_DIR/06-target-send.json"; then
  echo "未能确认 $TARGET_AGENT 已创建反向对话任务，详见 $WORK_DIR/06-target-send.json" >&2
  exit 6
fi

echo
echo "真实 owner-to-agent CLI 双向 flow 已完成。日志目录：$WORK_DIR"
