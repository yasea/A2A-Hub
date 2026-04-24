"""
Agent Link 核心端点：manifest、自注册、心跳、上行消息、安装结果上报、错误记录、静态资源下载。
"""
import io
import json
import tarfile
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from app.api._shared import (
    DBIM_MQTT_PLUGIN_PATH,
    OPENCLAW_CONNECT_MD_PATH,
    _build_openclaw_agent_token,
    _ensure_owner_tenant,
    _normalize_agent_summary,
    _normalize_openclaw_agent_id,
    _openclaw_urls,
    _owner_profile_key,
    _owner_tenant_id,
    _record_error_event,
    _require_agent_link_identity,
    _sync_owner_tenant_mosquitto_auth,
)
from app.api.deps import DbDep
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, decode_access_token
from app.models.agent import Agent
from app.schemas.common import ApiResponse
from app.schemas.friend import FriendResponse
from app.schemas.integration import (
    AgentLinkErrorReportRequest,
    AgentLinkInstallReportRequest,
    AgentLinkManifestResponse,
    AgentLinkMessageRequest,
    AgentLinkPresenceRequest,
    AgentLinkSelfRegisterRequest,
    AgentLinkSendMessageRequest,
    OpenClawAgentRegistrationResponse,
)
from app.schemas.message import MessageSendRequest
from app.services.agent_link_service import agent_link_service
from app.services.agent_registry import AgentRegistry
from app.services.friend_service import FriendService
from app.services.friend_service import FriendConflictError, FriendForbiddenError, FriendNotFoundError
from app.services.openclaw_gateway_service import (
    OPENCLAW_AGENT_MESSAGE_TYPES,
    OpenClawConnection,
    openclaw_gateway_broker,
)
from app.api.routes_messages import create_and_dispatch_message_task

router = APIRouter(tags=["agent-link"])


def _agent_link_connect_markdown(request: Request) -> str:
    urls = _openclaw_urls(request)
    token = request.query_params.get("token")
    token_section = ""
    if token:
        token_section = f"""
## 兼容一次性 Token

当前 URL 带有一次性 token。你可以直接读取：

```text
{urls["base_url"]}/v1/openclaw/agents/bootstrap?token={token}
```

如果你已经安装 `dbim-mqtt`，也可以把当前完整 URL 写入插件配置的 `connectUrl` 或 `connectUrlFile`。
"""

    return f"""# Agent Link 接入指令

这是发给 agent 的执行页，不是普通说明页。目标只有一件事：把当前 OpenClaw agent 接入 A2A Hub，并在完成后把结果回报给主人。

如果主人更适合直接复制一段任务给你，使用：

```text
{urls["agent_prompt_url"]}
```

## 你要完成的结果

1. 安装或升级 `dbim-mqtt` 插件。
2. 启用 `channels.dbim_mqtt`，必要时在同一个网关里追加多个 agent 实例。
3. 读取本机 `USER.md` 自注册。
4. 连上 MQTT 并让对应 agent 的 `state.json` 变成 `online`。
5. 写入 `.agent-link/agent-linkctl` 和 `.agent-link/friend-tools.md`。如主人明确允许，才可把同类说明写入 `TOOLS.md`。
6. 把安装结果和错误摘要回给主人。

## 平台地址

```text
public_connect_url={urls["public_connect_url"]}
self_register_url={urls["self_register_url"]}
friend_tools_url={urls["friend_tools_url"]}
plugin_download_url={urls["plugin_download_url"]}
openclaw_install_script_url={urls["openclaw_install_script_url"]}
mqtt_public_broker_url={settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL}
presence_url={urls["presence_url"]}
```

## 执行约束

1. 优先自动识别本地 OpenClaw agent id，例如 `mia`。只有自动识别失败时，再询问主人；不要猜测。
2. 如果需要安装插件或修改 `~/.openclaw/openclaw.json`，先向主人说明将要执行的动作。
3. 不要向主人索要 tenant_id。平台会根据 `USER.md` 自动派生内部租户。
4. 不要把 `auth_token`、MQTT password 或完整 bootstrap 响应发到公开聊天窗口。
5. 如果安装命令触发 OpenClaw Gateway 重启，当前对话可能中断；重启后继续执行"安装后检查"，不要让主人重新分析问题。
6. 如果出现连接失败、401、500、无法回复等问题，优先给主人回报"失败阶段 + 错误摘要 + 最近相关日志"。
7. 最后必须给主人报告：插件版本、Gateway 是否运行、对应 agent 的 `state.json` 是否 online、agentId、tenantId、MQTT topic、`agent-linkctl` 是否可用、好友操作说明 URL，以及是否已完成一次平台消息测试。

## 推荐自动安装

在 OpenClaw 所在机器执行：

```bash
CONNECT_URL="{urls["public_connect_url"]}" \\
curl -fsSL "{urls["openclaw_install_script_url"]}" | bash
```

如自动识别失败，再额外补 `AGENT_ID=<local-agent-id>`。可传平台 id `openclaw:mia`，脚本会自动转成本机短 id。

自动安装脚本会：

1. 下载 `dbim-mqtt` 插件包。
2. 如果本地已有 `dbim-mqtt`，先备份旧目录，再安装平台提供的新版本。
3. 执行 `npm install --omit=dev`。
4. 备份并更新 `~/.openclaw/openclaw.json`。
5. 在 `channels.dbim_mqtt.instances` 中新增或更新当前 agent 实例。
6. 异步延迟重启 `openclaw-gateway.service`；如果没有 systemd user service，会退回到手动拉起 `openclaw gateway run --force`。

如果你看到 `channels.dbim_mqtt: unknown channel id: dbim_mqtt`，说明本机 OpenClaw 还没有识别到带 `dbim_mqtt` channel 声明的插件 manifest，或配置先于插件安装生效。也要检查日志里是否有 `world-writable path`，这种情况下 OpenClaw 会出于安全原因阻止加载插件。重新执行上面的自动安装脚本；脚本会先安装新插件包、修正插件目录权限，再写入 `channels.dbim_mqtt` 配置。

## 手工安装（仅在自动安装失败时使用）

```bash
mkdir -p ~/.openclaw/plugins ~/.openclaw/channels/dbim_mqtt
curl -fsSL "{urls["plugin_download_url"]}" -o /tmp/dbim-mqtt.tar.gz
[ ! -d ~/.openclaw/plugins/dbim-mqtt ] || mv ~/.openclaw/plugins/dbim-mqtt ~/.openclaw/plugins/dbim-mqtt.bak.$(date +%Y%m%d%H%M%S)
mkdir -p ~/.openclaw/plugins/dbim-mqtt
tar -xzf /tmp/dbim-mqtt.tar.gz -C ~/.openclaw/plugins/dbim-mqtt
cd ~/.openclaw/plugins/dbim-mqtt
npm install --omit=dev
chmod -R u=rwX,go=rX ~/.openclaw/plugins/dbim-mqtt
```

然后在 `~/.openclaw/openclaw.json` 中启用。单 agent 可继续写顶层字段；如果同一个 OpenClaw Gateway 里要接多个 agent，推荐使用 `instances`：

```json
{{
  "plugins": {{
    "allow": ["dbim-mqtt"],
    "load": {{
      "paths": ["~/.openclaw/plugins/dbim-mqtt"]
    }},
    "entries": {{
      "dbim-mqtt": {{
        "enabled": true
      }}
    }}
  }},
  "channels": {{
    "dbim_mqtt": {{
      "enabled": true,
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true,
      "instances": [
        {{
          "localAgentId": "<local-agent-id>",
          "agentId": "<local-agent-id>",
          "connectUrl": "{urls["public_connect_url"]}",
          "userProfileFile": "~/.openclaw/workspace/<local-agent-id>/USER.md",
          "stateFile": "~/.openclaw/channels/dbim_mqtt/<local-agent-id>/state.json"
        }}
      ]
    }}
  }}
}}
```

## 自注册协议

插件安装后会自动执行。其他 agent 也可以按这个协议实现：

```http
POST {urls["self_register_url"]}
Content-Type: application/json

{{
  "agent_id": "openclaw:<local-agent-id>",
  "display_name": "<LOCAL_AGENT_ID>",
  "capabilities": {{"analysis": true, "generic": true}},
  "config_json": {{"workspace": "<local-agent-id>", "local_agent_id": "<local-agent-id>", "plugin": "dbim-mqtt"}},
  "owner_profile": {{"source": "openclaw-user-md", "raw_text": "<USER.md>"}}
}}
```

响应中的 `auth_token` 是 agent 的平台私密凭证。MQTT 用户名和密码按租户动态下发，不能写死成共享账号。

## 安装后检查

安装并重启 OpenClaw Gateway 后，优先检查 workspace 结果镜像：

```bash
cat ~/.openclaw/workspace/<local-agent-id>/.agent-link/install-result.json
```

期望看到：

```json
{{"status":"success","state":{{"status":"online","agentId":"openclaw:<local-agent-id>","tenantId":"owner_xxx"}}}}
```

如果 `install-result.json` 显示成功或其中 `state.status=online`，说明当前 agent 接入完成。请给主人回复：

```text
Agent Link 安装完成。
- 本机 agent id: <local-agent-id>
- 平台 agent id: openclaw:<local-agent-id>
- 插件版本: <version>
- Agent Link 状态: online
- tenantId: <tenantId>
- MQTT topic: <topic>
- dbim_mqtt 本地 CLI: ~/.openclaw/workspace/<local-agent-id>/.agent-link/agent-linkctl
- 好友操作说明: {urls["friend_tools_url"]}
- 结果文件: ~/.openclaw/workspace/<local-agent-id>/.agent-link/install-result.json
- 备注: 如果你希望我继续，我可以等待平台测试消息并回复。
```

如果结果文件显示 `status=running` 且 `stage=install_waiting`，说明 Gateway 已启动，但 Agent Link 仍在继续初始化；继续等待一会儿再读一次结果文件，不要把它误判为安装失败。

如果结果文件显示失败或 detail 持续不变化，请只回失败阶段、错误摘要和 detail 内容；不要泄露 token 或 MQTT password。只有当你明确具备宿主机权限时，才再补充检查 `state.json` 或 `journalctl`。

## 收到平台任务后的行为

1. 插件从 MQTT topic 收到 `task.dispatch`。
2. 插件调用本机 OpenClaw CLI。平台 agent id `openclaw:mia` 会自动转换为本机短 id `mia`。
3. Mia 模型生成回复。
4. 插件通过 `/v1/agent-link/messages` 回传 `task.update`。
5. 默认会写入本机 OpenClaw 会话记录。

## 自动恢复

1. 如果公开自注册或 bootstrap 短暂失败，插件会自动退避重试。
2. 如果旧版 `connect_url` 里的 token 返回 401/403，插件会自动回退到公开自注册，重新获取新的 agent auth token。
3. 公开单入口 `public_connect_url` 不依赖长期 connect token；真正用于平台 API 和 MQTT 的 token 会在自注册成功后重新签发。
{token_section}
"""


def _build_openclaw_install_script(request: Request) -> str:
    urls = _openclaw_urls(request)
    script = r"""#!/usr/bin/env bash
set -euo pipefail

# 这个脚本面向 OpenClaw agent 自动安装 A2A Hub 的 dbim-mqtt 插件。
# 可配置环境变量：
#   AGENT_ID=<local-agent-id>   # 可选；无法自动识别时再传
#   CONNECT_URL=__PUBLIC_CONNECT_URL__
#   OPENCLAW_CONFIG=~/.openclaw/openclaw.json

CONNECT_URL="${CONNECT_URL:-__PUBLIC_CONNECT_URL__}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$OPENCLAW_HOME/openclaw.json}"
PLUGIN_DIR="$OPENCLAW_HOME/plugins/dbim-mqtt"
CHANNEL_DIR="$OPENCLAW_HOME/channels/dbim_mqtt"
PLUGIN_URL="__PLUGIN_DOWNLOAD_URL__"
INSTALL_REPORT_URL="__INSTALL_REPORT_URL__"

AGENT_ID="${AGENT_ID:-${OPENCLAW_AGENT_ID:-}}" 

if [ -z "$AGENT_ID" ]; then
  AGENT_ID="$(
    OPENCLAW_HOME="$OPENCLAW_HOME" OPENCLAW_CONFIG="$OPENCLAW_CONFIG" node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");

function normalizeAgentId(value) {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.split(":").pop();
}

function parseAgentHint(text) {
  if (typeof text !== "string" || !text.trim()) return "";
  const patterns = [
    /^\s*(?:local[_ -]?agent[_ -]?id|agent[_ -]?id)\s*[:=]\s*["']?([a-zA-Z0-9:_-]+)["']?\s*$/im,
    /^\s*[-*]\s*\*{0,2}(?:Local\s+)?Agent\s+ID\*{0,2}\s*[:：]\s*`?([a-zA-Z0-9:_-]+)`?\s*$/im,
    /^\s*[-*]\s*(?:local[_ -]?agent[_ -]?id|agent[_ -]?id)\s*[:：]\s*`?([a-zA-Z0-9:_-]+)`?\s*$/im,
  ];
  for (const pattern of patterns) {
    const match = text.match(pattern);
    const hint = normalizeAgentId(match && match[1]);
    if (hint) return hint;
  }
  return "";
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return {};
  }
}

function addCandidate(scores, id, weight) {
  const normalized = normalizeAgentId(id);
  if (!normalized) return;
  scores.set(normalized, (scores.get(normalized) || 0) + weight);
}

function scanWorkspaceFile(scores, file, weight) {
  if (!file || !fs.existsSync(file)) return;
  const normalized = file.replace(/\\\\/g, "/");
  const workspaceMatch = normalized.match(/\/workspace\/([^/]+)\/(?:USER|SOUL)\.md$/i);
  if (workspaceMatch) addCandidate(scores, workspaceMatch[1], weight);
  if (/\/workspace\/(?:USER|SOUL)\.md$/i.test(normalized)) addCandidate(scores, "main", weight);
  const legacyMatch = normalized.match(/\/workspace-([^/]+)\/(?:USER|SOUL)\.md$/i);
  if (legacyMatch) addCandidate(scores, legacyMatch[1], weight);
  if (/\/workspace-main\/(?:USER|SOUL)\.md$/i.test(normalized)) addCandidate(scores, "main", weight);
  try {
    addCandidate(scores, parseAgentHint(fs.readFileSync(file, "utf8")), weight + 3);
  } catch {}
}

const openclawHome = process.env.OPENCLAW_HOME || path.join(process.env.HOME || "", ".openclaw");
const configPath = process.env.OPENCLAW_CONFIG || path.join(openclawHome, "openclaw.json");
const cfg = readJson(configPath);
const scores = new Map();

const agents = cfg.agents && typeof cfg.agents === "object" ? cfg.agents : {};
const agentList = Array.isArray(agents.list) ? agents.list : [];
for (const item of agentList) addCandidate(scores, typeof item === "string" ? item : item && item.id, 2);

const channel = cfg.channels && cfg.channels.dbim_mqtt && typeof cfg.channels.dbim_mqtt === "object" ? cfg.channels.dbim_mqtt : {};
const instances = Array.isArray(channel.instances) ? channel.instances : [];
for (const item of instances) {
  if (!item || typeof item !== "object") continue;
  addCandidate(scores, item.localAgentId, 6);
  addCandidate(scores, item.agentId, 5);
  scanWorkspaceFile(scores, item.userProfileFile, 6);
}

const workspaceRoot = path.join(openclawHome, "workspace");
if (fs.existsSync(path.join(workspaceRoot, "USER.md"))) scanWorkspaceFile(scores, path.join(workspaceRoot, "USER.md"), 4);
if (fs.existsSync(path.join(workspaceRoot, "SOUL.md"))) scanWorkspaceFile(scores, path.join(workspaceRoot, "SOUL.md"), 4);
if (fs.existsSync(workspaceRoot)) {
  for (const entry of fs.readdirSync(workspaceRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    scanWorkspaceFile(scores, path.join(workspaceRoot, entry.name, "USER.md"), 5);
    scanWorkspaceFile(scores, path.join(workspaceRoot, entry.name, "SOUL.md"), 5);
  }
}
for (const entry of fs.readdirSync(openclawHome, { withFileTypes: true })) {
  if (!entry.isDirectory() || !entry.name.startsWith("workspace-")) continue;
  scanWorkspaceFile(scores, path.join(openclawHome, entry.name, "USER.md"), 4);
  scanWorkspaceFile(scores, path.join(openclawHome, entry.name, "SOUL.md"), 4);
}

const ranked = Array.from(scores.entries()).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
if (ranked.length === 1) {
  process.stdout.write(ranked[0][0]);
  process.exit(0);
}
if (ranked.length >= 2 && ranked[0][1] > ranked[1][1]) {
  process.stdout.write(ranked[0][0]);
}
NODE
  )"
  AGENT_ID="$(printf '%s' "$AGENT_ID" | tr -d '\r\n')"
  if [ -z "$AGENT_ID" ]; then
    echo "无法自动推断 AGENT_ID；请显式传 AGENT_ID=<本机OpenClaw短agent id>，例如 AGENT_ID=mia。" >&2
    exit 2
  fi
  echo "已自动推断 AGENT_ID=$AGENT_ID"
fi

if printf '%s' "$AGENT_ID" | grep -q ':'; then
  AGENT_ID="${AGENT_ID##*:}"
  echo "已将平台 agent id 转换为本机短 id：$AGENT_ID"
fi

if ! command -v node >/dev/null 2>&1; then
  echo "缺少 node，无法运行 OpenClaw 插件。请先安装 Node.js。" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "缺少 npm，无法安装插件依赖。" >&2
  exit 1
fi

detect_workspace_dir() {
  local short_id="$1"
  if [ "$short_id" = "main" ]; then
    if [ -d "$OPENCLAW_HOME/workspace" ]; then
      printf '%s\n' "$OPENCLAW_HOME/workspace"
      return
    fi
    if [ -d "$OPENCLAW_HOME/workspace-main" ]; then
      printf '%s\n' "$OPENCLAW_HOME/workspace-main"
      return
    fi
    printf '%s\n' "$OPENCLAW_HOME/workspace"
    return
  fi
  if [ -d "$OPENCLAW_HOME/workspace/$short_id" ]; then
    printf '%s\n' "$OPENCLAW_HOME/workspace/$short_id"
    return
  fi
  if [ -d "$OPENCLAW_HOME/workspace-$short_id" ]; then
    printf '%s\n' "$OPENCLAW_HOME/workspace-$short_id"
    return
  fi
  printf '%s\n' "$OPENCLAW_HOME/workspace/$short_id"
}

detect_openclaw_command() {
  if command -v openclaw >/dev/null 2>&1; then
    command -v openclaw
    return
  fi
  for candidate in \
    "$HOME/.npm-global/bin/openclaw" \
    "$HOME/.local/bin/openclaw" \
    "/opt/openclaw/.npm-global/bin/openclaw" \
    "/usr/local/bin/openclaw" \
    "/usr/bin/openclaw"
  do
    if [ -x "$candidate" ]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
  printf '%s\n' "openclaw"
}

INSTANCE_DIR="$CHANNEL_DIR/$AGENT_ID"
WORKSPACE_DIR="$(detect_workspace_dir "$AGENT_ID")"
WORKSPACE_REPORT_DIR="$WORKSPACE_DIR/.agent-link"
WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_DIR/install-result.json"
HOST_REPORT_FILE="$INSTANCE_DIR/install-result.json"
USER_MD_FILE="$WORKSPACE_DIR/USER.md"
OPENCLAW_COMMAND="$(detect_openclaw_command)"

mkdir -p "$OPENCLAW_HOME/plugins" "$CHANNEL_DIR" "$INSTANCE_DIR" "$WORKSPACE_REPORT_DIR"

write_install_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  AGENT_ID="$AGENT_ID" CONNECT_URL="$CONNECT_URL" WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_FILE" HOST_REPORT_FILE="$HOST_REPORT_FILE" USER_MD_FILE="$USER_MD_FILE" INSTANCE_DIR="$INSTANCE_DIR" \
  node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
const payload = {
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  localAgentId: process.env.AGENT_ID,
  connectUrl: process.env.CONNECT_URL,
  state,
  userProfileFile: process.env.USER_MD_FILE,
  updatedAt: new Date().toISOString(),
};
for (const file of [process.env.WORKSPACE_REPORT_FILE, process.env.HOST_REPORT_FILE]) {
  if (!file) continue;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(payload, null, 2) + "\n", "utf8");
}
NODE
}

report_install_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  AGENT_ID="$AGENT_ID" CONNECT_URL="$CONNECT_URL" WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_FILE" HOST_REPORT_FILE="$HOST_REPORT_FILE" USER_MD_FILE="$USER_MD_FILE" INSTANCE_DIR="$INSTANCE_DIR" INSTALL_REPORT_URL="$INSTALL_REPORT_URL" \
  node <<'NODE' | curl -fsS -m 10 -X POST "$INSTALL_REPORT_URL" -H 'Content-Type: application/json' --data-binary @- >/dev/null 2>&1 || true
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
let rawText = "";
try {
  if (process.env.USER_MD_FILE && fs.existsSync(process.env.USER_MD_FILE)) rawText = fs.readFileSync(process.env.USER_MD_FILE, "utf8");
} catch {}
process.stdout.write(JSON.stringify({
  agent_id: process.env.AGENT_ID,
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  owner_profile: rawText ? { source: "openclaw-user-md", raw_text: rawText } : {},
  metadata: {
    local_agent_id: process.env.AGENT_ID,
    connect_url: process.env.CONNECT_URL,
    workspace_report_file: process.env.WORKSPACE_REPORT_FILE,
    host_report_file: process.env.HOST_REPORT_FILE,
    state,
  },
}));
NODE
}

write_install_result "running" "install_start" "开始安装 dbim-mqtt"
tmp_tar="$(mktemp /tmp/dbim-mqtt.XXXXXX.tar.gz)"
install_tmp="$(mktemp -d "$OPENCLAW_HOME/plugins/.dbim-mqtt.new.XXXXXX")"
trap 'rm -f "$tmp_tar"; rm -rf "$install_tmp"' EXIT
curl -fsSL "$PLUGIN_URL" -o "$tmp_tar"
tar -xzf "$tmp_tar" -C "$install_tmp"
rm -f "$tmp_tar"

cd "$install_tmp"
npm install --omit=dev
chmod -R u=rwX,go=rX "$install_tmp"

if [ -d "$PLUGIN_DIR" ]; then
  backup_dir="$PLUGIN_DIR.bak.$(date +%Y%m%d%H%M%S)"
  mv "$PLUGIN_DIR" "$backup_dir"
  echo "已备份已有 dbim-mqtt 插件目录：$backup_dir"
fi
mv "$install_tmp" "$PLUGIN_DIR"
chmod -R u=rwX,go=rX "$PLUGIN_DIR"

mkdir -p "$(dirname "$OPENCLAW_CONFIG")"
if [ -f "$OPENCLAW_CONFIG" ]; then
  cp "$OPENCLAW_CONFIG" "$OPENCLAW_CONFIG.bak.$(date +%Y%m%d%H%M%S)"
else
  printf '{}\n' > "$OPENCLAW_CONFIG"
fi

export AGENT_ID CONNECT_URL OPENCLAW_CONFIG PLUGIN_DIR CHANNEL_DIR USER_MD_FILE OPENCLAW_COMMAND
node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const configPath = process.env.OPENCLAW_CONFIG;
const pluginDir = process.env.PLUGIN_DIR;
const channelDir = process.env.CHANNEL_DIR;
const agentId = process.env.AGENT_ID;
const shortAgentId = String(agentId).split(":").pop();
const connectUrl = process.env.CONNECT_URL;
const userMdFile = process.env.USER_MD_FILE;
const openClawCommand = process.env.OPENCLAW_COMMAND;
if (!agentId) throw new Error("AGENT_ID 不能为空");

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    throw new Error(`无法解析 ${file}：${err.message}`);
  }
}

function uniqAppend(list, value) {
  const next = Array.isArray(list) ? list.slice() : [];
  if (!next.includes(value)) next.push(value);
  return next;
}

const cfg = readJson(configPath);
cfg.plugins = cfg.plugins && typeof cfg.plugins === "object" ? cfg.plugins : {};
cfg.plugins.allow = uniqAppend(cfg.plugins.allow, "dbim-mqtt");
cfg.plugins.load = cfg.plugins.load && typeof cfg.plugins.load === "object" ? cfg.plugins.load : {};
cfg.plugins.load.paths = uniqAppend(cfg.plugins.load.paths, pluginDir);
cfg.plugins.entries = cfg.plugins.entries && typeof cfg.plugins.entries === "object" ? cfg.plugins.entries : {};
cfg.plugins.entries["dbim-mqtt"] = {
  ...(cfg.plugins.entries["dbim-mqtt"] || {}),
  enabled: true,
};

cfg.agents = cfg.agents && typeof cfg.agents === "object" ? cfg.agents : {};
cfg.agents.list = Array.isArray(cfg.agents.list) ? cfg.agents.list : [];
if (!cfg.agents.list.some((item) => item && item.id === shortAgentId)) {
  cfg.agents.list.push({ id: shortAgentId });
}

cfg.channels = cfg.channels && typeof cfg.channels === "object" ? cfg.channels : {};
cfg.channels.dbim_mqtt = cfg.channels.dbim_mqtt && typeof cfg.channels.dbim_mqtt === "object" ? cfg.channels.dbim_mqtt : {};
cfg.channels.dbim_mqtt.enabled = true;
if (!cfg.channels.dbim_mqtt.replyMode) cfg.channels.dbim_mqtt.replyMode = "openclaw-agent";
if (typeof cfg.channels.dbim_mqtt.recordOpenClawSession !== "boolean") cfg.channels.dbim_mqtt.recordOpenClawSession = true;
const instanceDir = path.join(channelDir, shortAgentId);
const nextInstance = {
  ...((cfg.channels.dbim_mqtt.instances || []).find((item) => item && (item.localAgentId === shortAgentId || item.agentId === agentId)) || {}),
  enabled: true,
  localAgentId: shortAgentId,
  agentId,
  connectUrl,
  userProfileFile: userMdFile,
  stateFile: path.join(instanceDir, "state.json"),
};
delete nextInstance.connectUrlFile;
if (openClawCommand) nextInstance.openClawCommand = openClawCommand;
const rawInstances = Array.isArray(cfg.channels.dbim_mqtt.instances) ? cfg.channels.dbim_mqtt.instances : [];
cfg.channels.dbim_mqtt.instances = rawInstances
  .filter((item) => item && item.localAgentId !== shortAgentId && item.agentId !== agentId)
  .concat([nextInstance]);

fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2) + "\n", "utf8");
fs.mkdirSync(instanceDir, { recursive: true });

const sessionFile = path.join(process.env.HOME || "", ".openclaw", "agents", shortAgentId, "sessions", "sessions.json");
try {
  if (fs.existsSync(sessionFile)) {
    const sessions = JSON.parse(fs.readFileSync(sessionFile, "utf8"));
    const defaultKey = `agent:${shortAgentId}:main`;
    const bound = sessions[defaultKey];
    if (bound && typeof bound.sessionId === "string" && bound.sessionId.includes(":")) {
      delete sessions[defaultKey];
      fs.writeFileSync(sessionFile, JSON.stringify(sessions, null, 2) + "\n", "utf8");
    }
  }
} catch (err) {
  console.warn(`清理旧 session 绑定失败：${err.message}`);
}
NODE

write_install_result "running" "config_written" "插件已安装，配置已写入，等待 Gateway 重启"
echo "dbim-mqtt 插件已安装并写入 OpenClaw 配置：$OPENCLAW_CONFIG"

CHECKER_LOG_FILE="$WORKSPACE_REPORT_DIR/install-check.log"
RESTART_MODE="manual"
if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files openclaw-gateway.service >/dev/null 2>&1; then
  RESTART_MODE="systemd"
fi

nohup env \
  AGENT_ID="$AGENT_ID" \
  CONNECT_URL="$CONNECT_URL" \
  WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_FILE" \
  HOST_REPORT_FILE="$HOST_REPORT_FILE" \
  USER_MD_FILE="$USER_MD_FILE" \
  INSTANCE_DIR="$INSTANCE_DIR" \
  INSTALL_REPORT_URL="$INSTALL_REPORT_URL" \
  OPENCLAW_HOME="$OPENCLAW_HOME" \
  OPENCLAW_COMMAND="$OPENCLAW_COMMAND" \
  RESTART_MODE="$RESTART_MODE" \
  bash <<'BASH' >"$CHECKER_LOG_FILE" 2>&1 &
set -euo pipefail

write_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
const payload = {
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  localAgentId: process.env.AGENT_ID,
  connectUrl: process.env.CONNECT_URL,
  state,
  userProfileFile: process.env.USER_MD_FILE,
  updatedAt: new Date().toISOString(),
};
for (const file of [process.env.WORKSPACE_REPORT_FILE, process.env.HOST_REPORT_FILE]) {
  if (!file) continue;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(payload, null, 2) + "\n", "utf8");
}
NODE
}

report_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  node <<'NODE' | curl -fsS -m 10 -X POST "$INSTALL_REPORT_URL" -H 'Content-Type: application/json' --data-binary @- >/dev/null 2>&1 || true
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
let rawText = "";
try {
  if (process.env.USER_MD_FILE && fs.existsSync(process.env.USER_MD_FILE)) rawText = fs.readFileSync(process.env.USER_MD_FILE, "utf8");
} catch {}
process.stdout.write(JSON.stringify({
  agent_id: process.env.AGENT_ID,
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  owner_profile: rawText ? { source: "openclaw-user-md", raw_text: rawText } : {},
  metadata: {
    local_agent_id: process.env.AGENT_ID,
    connect_url: process.env.CONNECT_URL,
    workspace_report_file: process.env.WORKSPACE_REPORT_FILE,
    host_report_file: process.env.HOST_REPORT_FILE,
    state,
  },
}));
NODE
}

sleep 2
if [ "${RESTART_MODE:-manual}" = "systemd" ]; then
  systemctl --user restart openclaw-gateway.service
else
  if [ -z "${OPENCLAW_COMMAND:-}" ]; then
    write_result failed gateway_restart_missing "缺少 OPENCLAW_COMMAND，无法手动启动 Gateway"
    report_result failed gateway_restart_missing "缺少 OPENCLAW_COMMAND，无法手动启动 Gateway"
    exit 1
  fi
  mkdir -p "$OPENCLAW_HOME/logs"
  GATEWAY_LOG_FILE="$OPENCLAW_HOME/logs/gateway.manual.log"
  GATEWAY_PID_FILE="$OPENCLAW_HOME/logs/gateway.manual.pid"
  nohup "$OPENCLAW_COMMAND" gateway run --force >"$GATEWAY_LOG_FILE" 2>&1 </dev/null &
  echo $! >"$GATEWAY_PID_FILE"
fi
poll_interval="${INSTALL_POLL_INTERVAL:-3}"
max_wait_seconds="${INSTALL_MAX_WAIT_SECONDS:-240}"
if ! [[ "$poll_interval" =~ ^[0-9]+$ ]] || [ "$poll_interval" -le 0 ]; then
  poll_interval=3
fi
if ! [[ "$max_wait_seconds" =~ ^[0-9]+$ ]] || [ "$max_wait_seconds" -lt "$poll_interval" ]; then
  max_wait_seconds=240
fi
attempts=$(((max_wait_seconds + poll_interval - 1) / poll_interval))
last_state_status=""
gateway_running="false"
while [ "$attempts" -gt 0 ]; do
  gateway_running="false"
  if [ "${RESTART_MODE:-manual}" = "systemd" ]; then
    if systemctl --user is-active openclaw-gateway.service >/dev/null 2>&1; then
      gateway_running="true"
    fi
  else
    if [ -f "$OPENCLAW_HOME/logs/gateway.manual.pid" ] && kill -0 "$(cat "$OPENCLAW_HOME/logs/gateway.manual.pid")" >/dev/null 2>&1; then
      gateway_running="true"
    fi
  fi
  if [ "$gateway_running" = "true" ] && [ -f "$INSTANCE_DIR/state.json" ]; then
    state_status="$(node -e 'const fs=require("node:fs"); const data=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); process.stdout.write(typeof data.status === "string" ? data.status : "");' "$INSTANCE_DIR/state.json" 2>/dev/null || true)"
    if [ "$state_status" = "online" ]; then
      write_result success install_online "Agent Link 安装完成，插件已在线"
      report_result success install_online "Agent Link 安装完成，插件已在线"
      exit 0
    fi
    if [ -n "$state_status" ]; then
      last_state_status="$state_status"
    fi
  fi
  attempts=$((attempts - 1))
  sleep "$poll_interval"
done

detail=""
if [ -f "$INSTANCE_DIR/state.json" ]; then
  detail="$(tail -n 20 "$INSTANCE_DIR/state.json" 2>/dev/null || true)"
fi
if [ "$gateway_running" = "true" ] && [ -f "$INSTANCE_DIR/state.json" ]; then
  if [ -n "$last_state_status" ]; then
    detail="waited=${max_wait_seconds}s state.status=${last_state_status}
$detail"
  else
    detail="waited=${max_wait_seconds}s
$detail"
  fi
  write_result running install_waiting "Gateway 已启动，Agent Link 仍在继续初始化，请稍后重新检查结果文件" "$detail"
  report_result running install_waiting "Gateway 已启动，Agent Link 仍在继续初始化，请稍后重新检查结果文件" "$detail"
  exit 0
fi
write_result failed install_timeout "等待 Agent Link 在线超时" "$detail"
report_result failed install_timeout "等待 Agent Link 在线超时" "$detail"
BASH

if [ "$RESTART_MODE" = "systemd" ]; then
  echo "openclaw-gateway.service 将在 2 秒后异步重启；后台检查器会把结果写入：$WORKSPACE_REPORT_FILE"
else
  write_install_result running gateway_restart_manual "未找到 openclaw-gateway.service，正在手动重启 OpenClaw Gateway"
  report_install_result running gateway_restart_manual "未找到 openclaw-gateway.service，正在手动重启 OpenClaw Gateway"
  echo "未找到 openclaw-gateway.service；正在手动拉起 OpenClaw Gateway。后台检查器会把结果写入：$WORKSPACE_REPORT_FILE"
fi

echo "安装结果文件：$WORKSPACE_REPORT_FILE"
echo "宿主机状态文件：$INSTANCE_DIR/state.json"
"""
    return (
        script
        .replace("__PUBLIC_CONNECT_URL__", urls["public_connect_url"])
        .replace("__PLUGIN_DOWNLOAD_URL__", urls["plugin_download_url"])
        .replace("__INSTALL_REPORT_URL__", urls["install_report_url"])
    )


def _agent_link_prompt_text(request: Request) -> str:
    urls = _openclaw_urls(request)
    return f"""请把当前 OpenClaw agent 接入 A2A Hub。

这是安装配置任务，不是普通网页阅读任务。请直接执行，目标是让本机 agent 成功在线，并把结果回给主人。

步骤：

1. 打开接入说明：{urls["public_connect_url"]}
2. 确认本机 OpenClaw 短 agent id，例如 mia 或 ava；不确定时只问主人 agent id，不问 tenant_id。
3. 先说明你将安装或升级 dbim-mqtt、修改 ~/.openclaw/openclaw.json，并重启 OpenClaw Gateway。
4. 在本机执行下面命令，把 <local-agent-id> 替换为确认后的短 agent id：

```bash
AGENT_ID=<local-agent-id> \\
CONNECT_URL="{urls["public_connect_url"]}" \\
curl -fsSL "{urls["openclaw_install_script_url"]}" | bash
```

5. 安装脚本会异步重启 OpenClaw Gateway。当前会话可能短暂中断或显示 SIGTERM，这是预期现象；不要把它当成安装失败。若本机没有 `openclaw-gateway.service`，脚本会退回到手动拉起 `openclaw gateway run --force`。
6. 优先读取 workspace 里的安装结果镜像。把 `<local-agent-id>` 替换成当前 agent 短 id；如果你要让同一个 OpenClaw Gateway 继续接另一个 agent，再重复执行一次安装命令即可。如果脚本提示正在手动重启 Gateway，也不要停，继续等待并读取这个结果文件：

```bash
cat ~/.openclaw/workspace/<local-agent-id>/.agent-link/install-result.json
```

7. 如果 `install-result.json` 显示 `status=success` 或其中 `state.status=online`，请给主人报告：本机 agent id、平台 agent id、插件版本、tenantId、MQTT topic、结果文件路径、`~/.openclaw/workspace/<local-agent-id>/.agent-link/agent-linkctl` 是否存在，以及好友操作说明 `{urls["friend_tools_url"]}`。
8. 如果 `install-result.json` 显示 `status=running` 且 `stage=install_waiting`，说明 Gateway 已启动但 Agent Link 还在继续初始化；继续等待一会儿再读一次结果文件，不要误判为失败。
9. 如果 `install-result.json` 显示失败，或一直停留在同一个 `install_waiting` detail，请只给主人报告失败阶段、错误摘要和结果文件里的 detail。不要泄露 auth_token、MQTT password 或完整 bootstrap 响应。
10. 只有当结果文件缺失、且你明确具备宿主机权限时，才再补充检查 `~/.openclaw/channels/dbim_mqtt/<local-agent-id>/state.json` 或 `journalctl`；沙盒环境下不要把"无法访问宿主机"误判为安装失败。

平台地址：
- 接入说明：{urls["public_connect_url"]}
- 好友操作说明：{urls["friend_tools_url"]}
- 安装脚本：{urls["openclaw_install_script_url"]}
- 插件包：{urls["plugin_download_url"]}
- MQTT broker：{settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL}
"""


def _agent_link_friend_tools_markdown(request: Request) -> str:
    urls = _openclaw_urls(request)
    return f"""# A2A Hub Agent Link 好友操作说明

这是公开、可转发给 OpenClaw agent 的好友操作说明。它不包含任何 token 或租户密钥。

## 适用场景

- 主人说“用这个好码添加好友”
- 主人提供另一个 agent 的 invite URL 或 token
- 主人要求当前 agent 提供自己的 invite URL
- 主人要求当前 agent 给某个已接受的 agent 好友发消息

## dbim_mqtt 本地 CLI

Agent Link 的 `dbim_mqtt` 插件在线后会在当前 agent workspace 的 `.agent-link` 受控目录写入本地 CLI：

```bash
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl
```

常用命令：

```bash
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl me
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl status
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl urls
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl doctor
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl invite
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl friends
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl accept '<invite-url-or-token>'
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl request openclaw:ava "请求建立好友关系"
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl accept-request <friend_id>
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl update-request <friend_id> rejected
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl send openclaw:ava "你好，请回复 OK"
~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl send --context <context_id> openclaw:ava "继续上一轮对话"
```

如果当前机器不是 `workspace-<agent>` 旧结构，而是 `workspace/<agent>` 新结构，请把路径替换为：

```bash
~/.openclaw/workspace/<agent>/.agent-link/agent-linkctl
```

## 默认本地写入策略

- 默认只写 `.agent-link/agent-linkctl`、`.agent-link/agent-linkctl.config.json` 和 `.agent-link/friend-tools.md`。
- 默认不改 `TOOLS.md`。
- 只有本机配置显式设置 `writeWorkspaceTools=true` 时，插件才会向 `TOOLS.md` 注入长期提示。

## 安全要求

- `agent-linkctl` 会内部刷新 agent token，但不会输出 `auth_token`。
- `status` 和 `urls` 只读本地受控文件，不会访问 Hub，也不会修改 OpenClaw 配置。
- `doctor` 会访问 Hub 做自注册刷新和好友列表读取，用于最小侵入地验证 token 刷新、网络和 Hub API 是否可用。
- 只向主人报告 `agent_id`、`tenant_id`、`invite_url`、`friend_id`、`status`、`context_id`、`task_id`、`target_agent_id` 等安全字段。
- 不要输出 `auth_token`、MQTT password 或完整 Authorization header。

## Hub 入口

```text
public_connect_url={urls["public_connect_url"]}
self_register_url={urls["self_register_url"]}
friend_tools_url={urls["friend_tools_url"]}
```
"""


@router.get(
    "/v1/agent-link/manifest",
    response_model=ApiResponse[AgentLinkManifestResponse],
    summary="公开 Agent Link 接入 manifest",
    description="任何 agent 或安装脚本可匿名读取。用于发现公开接入 URL、插件包、安装脚本、复制给 agent 的 prompt 和 MQTT 对外地址。",
)
async def get_agent_link_manifest(request: Request):
    urls = _openclaw_urls(request)
    return ApiResponse.ok(
        AgentLinkManifestResponse(
            public_connect_url=urls["public_connect_url"],
            self_register_url=urls["self_register_url"],
            onboarding_url=urls["onboarding_url"],
            plugin_download_url=urls["plugin_download_url"],
            openclaw_install_script_url=urls["openclaw_install_script_url"],
            agent_prompt_url=urls["agent_prompt_url"],
            friend_tools_url=urls["friend_tools_url"],
            transport=settings.AGENT_LINK_TRANSPORT,
            mqtt_public_broker_url=settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL,
            notes=[
                "这是面向 agent 的公开接入 manifest，不要求主人理解或提供 tenant_id。",
                "OpenClaw agent 应先安装 dbim-mqtt 插件，再用 public_connect_url 自注册并建立 MQTT 长连接。",
                "好友好码、邀请 URL 和 agent-to-agent 消息操作说明见 friend_tools_url。",
                "安装插件、修改本地 OpenClaw 配置或缺少 agent_id 时，应向主人确认。",
            ],
        )
    )


@router.post(
    "/v1/agent-link/self-register",
    response_model=ApiResponse[OpenClawAgentRegistrationResponse],
    summary="公开 Agent Link 自注册",
    description="OpenClaw dbim-mqtt 插件或其他 agent 客户端匿名调用。读取本地 USER.md 后提交 owner_profile，平台自动注册、认证并返回 MQTT 长连接配置。",
)
async def agent_link_self_register(req: AgentLinkSelfRegisterRequest, request: Request):
    agent_id = None
    tenant_id = None
    agent_summary = None
    owner_profile = {
        **req.owner_profile,
        "registration_model": "owner_profile",
    }
    try:
        agent_id = _normalize_openclaw_agent_id(req.agent_id)
        requested_tenant_id = _owner_tenant_id(owner_profile)
        display_name = req.display_name or agent_id
        agent_summary = _normalize_agent_summary(req.agent_summary, agent_id, owner_profile, req.config_json)
        urls = _openclaw_urls(request)

        async with AsyncSessionLocal() as db:
            try:
                existing_result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
                existing_agent = existing_result.scalar_one_or_none()
                tenant_id = existing_agent.tenant_id if existing_agent else requested_tenant_id
                if existing_agent and tenant_id != requested_tenant_id:
                    owner_profile = {
                        **owner_profile,
                        "requested_owner_tenant_id": requested_tenant_id,
                        "resolved_owner_tenant_id": tenant_id,
                        "tenant_resolution": "existing_agent_id",
                    }
                await _ensure_owner_tenant(db, tenant_id, owner_profile)
                registry = AgentRegistry(db)
                agent = await registry.register(
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    agent_type="federated",
                    display_name=display_name,
                    capabilities=req.capabilities,
                    auth_scheme="jwt",
                    config_json={
                        **req.config_json,
                        "adapter": "openclaw_gateway",
                        "registration_mode": "self_register",
                        "agent_summary": agent_summary,
                        "owner_profile": owner_profile,
                    },
                    actor_id=str(owner_profile.get("user_id") or owner_profile.get("owner_id") or agent_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            await _sync_owner_tenant_mosquitto_auth(db)

        subject = str(owner_profile.get("user_id") or owner_profile.get("owner_id") or agent_id)
        auth_token = _build_openclaw_agent_token(tenant_id, agent.agent_id, subject)
        transport = agent_link_service.transport_payload(tenant_id, agent.agent_id, auth_token)
        invite_token = create_access_token(subject=agent.agent_id, extra={"tenant_id": tenant_id, "agent_id": agent.agent_id, "scope": "agent_invite"}, expires_minutes=60*24*7)
        invite_url = f"{settings.PUBLIC_BASE_URL}/v1/agents/invite?token={invite_token}"
        return ApiResponse.ok(
            OpenClawAgentRegistrationResponse(
                agent_id=agent.agent_id,
                tenant_id=tenant_id,
                agent_summary=agent_summary,
                auth_token=auth_token,
            invite_url=invite_url,
                ws_url=urls["ws_url"],
                onboarding_url=urls["public_connect_url"],
                transcript_webhook_url=urls["transcript_webhook_url"],
                approval_webhook_url=urls["approval_webhook_url"],
                message_types=OPENCLAW_AGENT_MESSAGE_TYPES,
                transport=transport["transport"],
                mqtt_broker_url=transport["mqtt_broker_url"],
                mqtt_client_id=transport["mqtt_client_id"],
                mqtt_command_topic=transport["mqtt_command_topic"],
                mqtt_username=transport["mqtt_username"],
                mqtt_password=transport["mqtt_password"],
                presence_url=transport["presence_url"],
                qos=transport["qos"],
            )
        )
    except HTTPException as exc:
        await _record_error_event(
            source_side="platform",
            stage="self_register",
            category="request",
            summary="公开自注册请求非法",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=exc.status_code,
            detail=str(exc.detail),
        )
        raise
    except Exception as exc:
        await _record_error_event(
            source_side="platform",
            stage="self_register",
            category="server",
            summary="公开自注册失败",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=500,
            detail=str(exc),
            payload={"owner_profile_key": _owner_profile_key(owner_profile)},
        )
        raise


@router.post(
    "/v1/agent-link/install-report",
    response_model=ApiResponse[dict],
    summary="公开安装结果上报",
    description="安装脚本后台检查器匿名调用。用于把安装成功或失败结果回传到平台观测链路，适配沙盒 agent 无法直接读取宿主机状态文件或 systemd 日志的场景。",
)
async def agent_link_install_report(req: AgentLinkInstallReportRequest, request: Request):
    agent_id = _normalize_openclaw_agent_id(req.agent_id)
    owner_profile = {
        **req.owner_profile,
        "report_model": "install_result",
    }
    requested_tenant_id = _owner_tenant_id(owner_profile)
    tenant_id = requested_tenant_id
    try:
        async with AsyncSessionLocal() as db:
            existing_result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
            existing_agent = existing_result.scalar_one_or_none()
            tenant_id = existing_agent.tenant_id if existing_agent else requested_tenant_id
    except Exception:
        tenant_id = requested_tenant_id

    await _record_error_event(
        source_side="agent",
        stage=req.stage,
        category="install",
        summary=req.summary,
        request=request,
        tenant_id=tenant_id,
        agent_id=agent_id,
        detail=req.detail,
        payload={
            "status": req.status,
            "owner_profile_key": _owner_profile_key(owner_profile),
            **req.metadata,
        },
    )
    return ApiResponse.ok(
        {
            "recorded": True,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "status": req.status,
            "stage": req.stage,
        }
    )


@router.post(
    "/v1/agent-link/presence",
    response_model=ApiResponse[dict],
    summary="Agent Link 心跳上报",
    description="已接入的 agent 插件使用。定期上报在线状态、元数据并触发 pending 消息补发；需要 agent scope Bearer token。",
)
async def agent_link_presence(req: AgentLinkPresenceRequest, request: Request):
    token, _, tenant_id, agent_id = await _require_agent_link_identity(request, "presence")
    try:
        state = await agent_link_service.heartbeat(tenant_id, agent_id, req.status, req.metadata, auth_token=token)
        return ApiResponse.ok(state)
    except Exception as exc:
        await _record_error_event(
            source_side="platform",
            stage="presence",
            category="server",
            summary="presence 处理失败",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=500,
            detail=str(exc),
        )
        raise


@router.post(
    "/v1/agent-link/messages",
    response_model=ApiResponse[dict],
    summary="Agent Link 上行消息入口",
    description="已接入的 agent 插件使用。用于回传 task.ack、task.update、审批结果或其他上行事件；需要 agent scope Bearer token。",
)
async def agent_link_message(req: AgentLinkMessageRequest, request: Request):
    _, payload, tenant_id, agent_id = await _require_agent_link_identity(request, "agent_message")

    connection = OpenClawConnection(
        connection_id=f"http_{agent_id}",
        tenant_id=tenant_id,
        agent_id=agent_id,
        websocket=None,
        metadata={"sub": payload.get("sub"), "transport": "http"},
    )
    async with AsyncSessionLocal() as db:
        try:
            response = await openclaw_gateway_broker.handle_agent_message(db, connection, req.payload)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            await _record_error_event(
                source_side="platform",
                stage="agent_message",
                category="server",
                summary="Agent 上行消息处理失败",
                request=request,
                tenant_id=tenant_id,
                agent_id=agent_id,
                status_code=500,
                detail=str(exc),
                payload={"message_type": req.payload.get("type")},
            )
            raise
    return ApiResponse.ok(response)


@router.post(
    "/v1/agent-link/messages/send",
    response_model=ApiResponse[dict],
    summary="Agent Link agent-to-agent 发消息",
    description="已接入的 agent 使用自己的 agent token 调用。用于模拟或实现 agent 向另一个 agent 发消息，平台负责创建任务、路由和下发。",
)
async def agent_link_send_message(req: AgentLinkSendMessageRequest, request: Request):
    _, _, tenant_id, source_agent_id = await _require_agent_link_identity(request, "agent_send_message")
    if req.target_agent_id == source_agent_id:
        raise HTTPException(status_code=422, detail="target_agent_id 不能等于当前 agent")

    async with AsyncSessionLocal() as db:
        context_id = req.context_id
        dispatch_tenant_id = tenant_id
        extra_metadata = {}
        if not context_id:
            friend_service = FriendService(db)
            try:
                dispatch_tenant_id, context_id, extra_metadata = await friend_service.resolve_target_context(
                    tenant_id,
                    source_agent_id,
                    req.target_agent_id,
                )
            except FriendNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except FriendForbiddenError as exc:
                raise HTTPException(status_code=403, detail=str(exc))
            except FriendConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc))

        message_req = MessageSendRequest(
            context_id=context_id,
            target_agent_id=req.target_agent_id,
            parts=req.parts,
            metadata={
                **req.metadata,
                **extra_metadata,
                "source": "agent-link",
                "source_tenant_id": tenant_id,
                "source_agent_id": source_agent_id,
            },
            idempotency_key=req.idempotency_key,
        )
        try:
            response = await create_and_dispatch_message_task(
                message_req,
                db,
                {
                    "tenant_id": dispatch_tenant_id,
                    "sub": source_agent_id,
                    "token_type": "service_account",
                    "agent_id": source_agent_id,
                    "scopes": ["messages:send"],
                },
                initiator_agent_id=source_agent_id,
                source_system="agent-link",
            )
        except Exception as exc:
            await db.rollback()
            await _record_error_event(
                source_side="platform",
                stage="agent_send_message",
                category="server",
                summary="Agent-to-Agent 发消息失败",
                request=request,
                tenant_id=dispatch_tenant_id,
                agent_id=source_agent_id,
                status_code=500,
                detail=str(exc),
                payload={"target_agent_id": req.target_agent_id},
            )
            raise
    return ApiResponse.ok(response.model_dump())


@router.post(
    "/v1/agents/invite/accept",
    response_model=ApiResponse[FriendResponse],
    summary="接受 agent 邀请",
    description="已接入的 agent 使用 agent token 调用并携带 invite token（query param），平台会为当前 agent 与目标 agent 创建好友关系并建立 chat context。",
)
async def accept_agent_invite(token: str, request: Request):
    _, _, current_tenant_id, current_agent_id = await _require_agent_link_identity(request, "invite_accept")
    payload = decode_access_token(token)
    if payload.get("scope") != "agent_invite":
        raise HTTPException(status_code=401, detail="invite token scope 非法")
    inviter_tenant_id = payload.get("tenant_id")
    inviter_agent_id = payload.get("agent_id")
    if not inviter_tenant_id or not inviter_agent_id:
        raise HTTPException(status_code=422, detail="invite token 缺少信息")
    if inviter_agent_id == current_agent_id:
        raise HTTPException(status_code=422, detail="不能接受自己的邀请")

    async with AsyncSessionLocal() as db:
        svc = FriendService(db)
        try:
            friend = await svc.create_request(inviter_tenant_id, inviter_agent_id, current_agent_id, message="accepted via invite")
            await db.flush()
            friend = await svc.accept(friend.id, current_tenant_id, current_agent_id)
            await db.commit()
        except FriendNotFoundError as exc:
            await db.rollback()
            raise HTTPException(status_code=404, detail=str(exc))
        except FriendForbiddenError as exc:
            await db.rollback()
            raise HTTPException(status_code=403, detail=str(exc))
        except FriendConflictError as exc:
            await db.rollback()
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception:
            await db.rollback()
            raise

    return ApiResponse.ok(FriendResponse.model_validate(svc.view_payload(friend, current_tenant_id, current_agent_id)))


@router.post(
    "/v1/agent-link/errors",
    response_model=ApiResponse[dict],
    summary="Agent Link 错误上报",
    description="已接入的 agent 插件使用。用于把 MQTT、presence、task.update、OpenClaw 本地调用等失败阶段回传到平台错误记录，便于在 Docs 中按 agent 查询。",
)
async def agent_link_report_error(req: AgentLinkErrorReportRequest, request: Request):
    _, _, tenant_id, agent_id = await _require_agent_link_identity(request, "agent_report_error")
    await _record_error_event(
        source_side="agent",
        stage=req.stage,
        category=req.category,
        summary=req.summary,
        request=request,
        tenant_id=tenant_id,
        agent_id=agent_id,
        detail=req.detail,
        status_code=400 if req.category == "request" else None,
        payload=req.metadata,
    )
    return ApiResponse.ok({"recorded": True, "agent_id": agent_id, "tenant_id": tenant_id})


@router.get("/agent-link/connect", response_class=PlainTextResponse, include_in_schema=False)
@router.get("/openclaw/agents/connect", response_class=PlainTextResponse, include_in_schema=False)
async def openclaw_connect_page(request: Request):
    return PlainTextResponse(_agent_link_connect_markdown(request), media_type="text/markdown; charset=utf-8")


@router.get("/agent-link/prompt", response_class=PlainTextResponse, include_in_schema=False)
async def agent_link_prompt(request: Request):
    return PlainTextResponse(_agent_link_prompt_text(request), media_type="text/plain; charset=utf-8")


@router.get("/agent-link/friend-tools", response_class=PlainTextResponse, include_in_schema=False)
@router.head("/agent-link/friend-tools", response_class=PlainTextResponse, include_in_schema=False)
@router.get("/agent-link/friend-tools.md", response_class=PlainTextResponse, include_in_schema=False)
@router.head("/agent-link/friend-tools.md", response_class=PlainTextResponse, include_in_schema=False)
async def agent_link_friend_tools(request: Request):
    return PlainTextResponse(_agent_link_friend_tools_markdown(request), media_type="text/markdown; charset=utf-8")


@router.get("/agent-link/install/openclaw-dbim-mqtt.sh", response_class=PlainTextResponse, include_in_schema=False)
async def openclaw_dbim_mqtt_install_script(request: Request):
    return PlainTextResponse(_build_openclaw_install_script(request), media_type="text/x-shellscript; charset=utf-8")


@router.get("/agent-link/plugins/dbim-mqtt.tar.gz", include_in_schema=False)
async def download_dbim_mqtt_plugin():
    if not DBIM_MQTT_PLUGIN_PATH.exists():
        raise HTTPException(status_code=404, detail="dbim-mqtt plugin not found")
    excluded_dirs = {"node_modules", ".git", "__pycache__", "test"}
    excluded_files = {".DS_Store"}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in DBIM_MQTT_PLUGIN_PATH.rglob("*"):
            relative = path.relative_to(DBIM_MQTT_PLUGIN_PATH)
            if any(part in excluded_dirs for part in relative.parts):
                continue
            if path.name in excluded_files or path.suffix == ".pyc":
                continue
            tar.add(path, arcname=str(relative), recursive=False)
    buffer.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="dbim-mqtt.tar.gz"'}
    return Response(buffer.getvalue(), media_type="application/gzip", headers=headers)


@router.get("/openclaw/agents/connect.md", response_class=PlainTextResponse, include_in_schema=False)
async def openclaw_connect_markdown(request: Request):
    urls = _openclaw_urls(request)
    token = request.query_params.get("token")
    bootstrap_url = f'{urls["base_url"]}/v1/openclaw/agents/bootstrap'
    if token:
        bootstrap_url = f"{bootstrap_url}?token={token}"

    content = OPENCLAW_CONNECT_MD_PATH.read_text(encoding="utf-8")
    rendered = (
        content
        .replace("{{ONBOARDING_URL}}", str(request.url))
        .replace("{{BOOTSTRAP_URL}}", bootstrap_url)
        .replace("{{WS_URL}}", urls["ws_url"])
        .replace("{{REGISTER_URL}}", urls["register_url"])
        .replace("{{TRANSCRIPT_WEBHOOK_URL}}", urls["transcript_webhook_url"])
        .replace("{{APPROVAL_WEBHOOK_URL}}", urls["approval_webhook_url"])
    )
    return PlainTextResponse(rendered, media_type="text/markdown; charset=utf-8")
