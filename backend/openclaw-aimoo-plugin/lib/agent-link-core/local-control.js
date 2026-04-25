"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { ensureDir } = require("./state-store");

const TOOLS_BEGIN = "<!-- A2A_HUB_AGENT_LINK_BEGIN -->";
const TOOLS_END = "<!-- A2A_HUB_AGENT_LINK_END -->";

function helperSource() {
  return `#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

function usage() {
  console.log(\`A2A Hub Agent Link aimoo-link CLI bridge

Usage:
  openclaw aimoo --agent <local-agent-id> me
  openclaw aimoo --agent <local-agent-id> status
  openclaw aimoo --agent <local-agent-id> urls
  openclaw aimoo --agent <local-agent-id> doctor
  openclaw aimoo --agent <local-agent-id> invite
  openclaw aimoo --agent <local-agent-id> friends
  openclaw aimoo --agent <local-agent-id> request <target_agent_id> [message]
  openclaw aimoo --agent <local-agent-id> accept-request <friend_id>
  openclaw aimoo --agent <local-agent-id> update-request <friend_id> <accepted|rejected|blocked>
  openclaw aimoo --agent <local-agent-id> accept <invite_url_or_token>
  openclaw aimoo --agent <local-agent-id> send [--context <context_id>] <target_agent_id> <message>

Notes:
  - This CLI refreshes the agent token via public self-register and never prints it.
  - Do not paste auth_token, MQTT password, or Authorization headers into chat.
\`);
}

function loadConfig() {
  if (process.env.AIMOO_LINK_CLI_CONFIG_JSON) {
    const config = JSON.parse(process.env.AIMOO_LINK_CLI_CONFIG_JSON);
    config.configPath = "(env:AIMOO_LINK_CLI_CONFIG_JSON)";
    return config;
  }
  const configPath = path.join(__dirname, "aimoo-link-cli.config.json");
  const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
  config.configPath = configPath;
  return config;
}

function baseUrlFromConnectUrl(connectUrl) {
  const parsed = new URL(connectUrl);
  return \`\${parsed.protocol}//\${parsed.host}\`;
}

function platformAgentId(agentId) {
  const value = String(agentId || "").trim();
  if (!value) throw new Error("agent_id is required");
  return value.includes(":") ? value : \`openclaw:\${value}\`;
}

function readText(file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch {
    return "";
  }
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function controlDir(config) {
  if (config.configPath && !String(config.configPath).startsWith("(env:")) {
    return path.dirname(config.configPath);
  }
  if (config.userProfileFile) {
    return path.join(path.dirname(config.userProfileFile), ".agent-link");
  }
  return process.cwd();
}

function localStatus(config) {
  const dir = controlDir(config);
  const installResult = readJson(path.join(dir, "install-result.json"));
  return {
    agent_id: config.agentId,
    local_agent_id: config.localAgentId,
    connect_url: config.connectUrl,
    public_friend_tools_url: config.publicFriendToolsUrl || publicFriendToolsUrl(config.connectUrl),
    config_path: config.configPath,
    runbook_path: path.join(dir, "friend-tools.md"),
    install_result_path: path.join(dir, "install-result.json"),
    install_status: installResult && installResult.status,
    install_stage: installResult && installResult.stage,
    runtime_status: installResult && installResult.state && installResult.state.status,
    updated_at: installResult && installResult.updatedAt,
  };
}

async function requestJson(url, options = {}) {
  const timeoutMs = Number(options.timeoutMs || 15000);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const text = await response.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {}
    if (!response.ok) {
      const detail = json && (json.detail || json.message || JSON.stringify(json));
      throw new Error(\`\${response.status} \${response.statusText}: \${detail || text}\`);
    }
    return json || {};
  } finally {
    clearTimeout(timer);
  }
}

function publicFriendToolsUrl(connectUrl) {
  try {
    const parsed = new URL(connectUrl);
    return \`\${parsed.protocol}//\${parsed.host}/agent-link/friend-tools\`;
  } catch {
    return "/agent-link/friend-tools";
  }
}

function publicUrls(config) {
  const baseUrl = baseUrlFromConnectUrl(config.connectUrl);
  return {
    connect_url: config.connectUrl,
    manifest_url: \`\${baseUrl}/v1/agent-link/manifest\`,
    friend_tools_url: config.publicFriendToolsUrl || publicFriendToolsUrl(config.connectUrl),
    self_register_url: \`\${baseUrl}/v1/agent-link/self-register\`,
  };
}

async function register(config) {
  const baseUrl = baseUrlFromConnectUrl(config.connectUrl);
  const localAgentId = String(config.localAgentId || config.agentId || "").split(":").pop();
  const rawText = readText(config.userProfileFile);
  const body = {
    agent_id: platformAgentId(config.agentId || localAgentId),
    display_name: localAgentId.toUpperCase(),
    capabilities: { analysis: true, generic: true },
    config_json: {
      workspace: localAgentId,
      local_agent_id: localAgentId,
      plugin: "aimoo-link",
      local_helper: "openclaw aimoo",
    },
    owner_profile: rawText ? { source: "openclaw-user-md", raw_text: rawText } : {},
  };
  const response = await requestJson(\`\${baseUrl}/v1/agent-link/self-register\`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
    timeoutMs: config.httpTimeoutMs,
  });
  const data = response.data || {};
  if (!data.auth_token || !data.agent_id) throw new Error("self-register response missing auth_token or agent_id");
  return { baseUrl, data };
}

function tokenFromInvite(input) {
  const value = String(input || "").trim();
  if (!value) throw new Error("invite URL or token is required");
  try {
    const parsed = new URL(value);
    return parsed.searchParams.get("token") || value;
  } catch {
    return value;
  }
}

function safeFriend(data) {
  const item = data || {};
  return {
    id: item.id,
    friend_id: item.id,
    status: item.status,
    requester_agent_id: item.requester_agent_id,
    target_agent_id: item.target_agent_id,
    context_id: item.context_id,
  };
}

async function authed(config, fn) {
  const { baseUrl, data } = await register(config);
  return await fn({
    baseUrl,
    token: data.auth_token,
    agentId: data.agent_id,
    tenantId: data.tenant_id,
    inviteUrl: data.invite_url,
    timeoutMs: config.httpTimeoutMs,
  });
}

async function main() {
  const command = process.argv[2] || "help";
  if (command === "help" || command === "--help" || command === "-h") {
    usage();
    return;
  }
  const config = loadConfig();
  if (command === "status") {
    console.log(JSON.stringify(localStatus(config), null, 2));
    return;
  }
  if (command === "urls") {
    console.log(JSON.stringify(publicUrls(config), null, 2));
    return;
  }
  const result = await authed(config, async (ctx) => {
    const headers = {
      "content-type": "application/json",
      accept: "application/json",
      authorization: \`Bearer \${ctx.token}\`,
    };
    if (command === "me" || command === "invite") {
      return {
        agent_id: ctx.agentId,
        tenant_id: ctx.tenantId,
        invite_url: ctx.inviteUrl,
      };
    }
    if (command === "doctor") {
      const response = await requestJson(\`\${ctx.baseUrl}/v1/agents/\${encodeURIComponent(ctx.agentId)}/friends\`, {
        headers,
        timeoutMs: ctx.timeoutMs,
      });
      return {
        agent_id: ctx.agentId,
        tenant_id: ctx.tenantId,
        self_register: "ok",
        friends_list: "ok",
        friend_count: (response.data || []).length,
        public_friend_tools_url: config.publicFriendToolsUrl || publicFriendToolsUrl(config.connectUrl),
      };
    }
    if (command === "friends") {
      const response = await requestJson(\`\${ctx.baseUrl}/v1/agents/\${encodeURIComponent(ctx.agentId)}/friends\`, {
        headers,
        timeoutMs: ctx.timeoutMs,
      });
      return { agent_id: ctx.agentId, friends: (response.data || []).map(safeFriend) };
    }
    if (command === "request") {
      const target = process.argv[3];
      const message = process.argv.slice(4).join(" ") || "请求建立好友关系";
      if (!target) throw new Error("target_agent_id is required");
      const response = await requestJson(\`\${ctx.baseUrl}/v1/agents/\${encodeURIComponent(ctx.agentId)}/friends\`, {
        method: "POST",
        headers,
        body: JSON.stringify({ target_agent_id: target, message }),
        timeoutMs: ctx.timeoutMs,
      });
      return safeFriend(response.data);
    }
    if (command === "accept-request" || command === "update-request") {
      const friendId = process.argv[3];
      if (!friendId) throw new Error("friend_id is required");
      const status = command === "accept-request" ? "accepted" : process.argv[4];
      if (!["accepted", "rejected", "blocked"].includes(status)) {
        throw new Error("status must be one of: accepted, rejected, blocked");
      }
      const response = await requestJson(\`\${ctx.baseUrl}/v1/agents/\${encodeURIComponent(ctx.agentId)}/friends/\${encodeURIComponent(friendId)}\`, {
        method: "PATCH",
        headers,
        body: JSON.stringify({ status }),
        timeoutMs: ctx.timeoutMs,
      });
      return safeFriend(response.data);
    }
    if (command === "accept") {
      const token = tokenFromInvite(process.argv[3]);
      const response = await requestJson(\`\${ctx.baseUrl}/v1/agents/invite/accept?token=\${encodeURIComponent(token)}\`, {
        method: "POST",
        headers,
        timeoutMs: ctx.timeoutMs,
      });
      return safeFriend(response.data);
    }
    if (command === "send") {
      let index = 3;
      let contextId = null;
      if (process.argv[index] === "--context") {
        contextId = process.argv[index + 1];
        index += 2;
        if (!contextId) throw new Error("context_id is required after --context");
      }
      const target = process.argv[index];
      const text = process.argv.slice(index + 1).join(" ");
      if (!target || !text) throw new Error("target_agent_id and message are required");
      const response = await requestJson(\`\${ctx.baseUrl}/v1/agent-link/messages/send\`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          context_id: contextId,
          target_agent_id: target,
          parts: [{ type: "text/plain", text }],
        }),
        timeoutMs: ctx.timeoutMs,
      });
      const data = response.data || {};
      return {
        task_id: data.task_id,
        context_id: data.context_id,
        target_agent_id: target,
      };
    }
    throw new Error(\`unknown command: \${command}\`);
  });
  console.log(JSON.stringify(result, null, 2));
}

main().catch((error) => {
  console.error(JSON.stringify({ error: String(error && error.message ? error.message : error) }, null, 2));
  process.exit(1);
});
`;
}

function toolsSection(helperPath, agentId) {
  return `${TOOLS_BEGIN}
## A2A Hub Agent Link

When the owner asks you to add an agent friend, accept a friend invite code/URL ("好码", "邀请码", "token URL"), show your invite URL, list friends, or send a message to an agent friend, use the aimoo CLI below. It refreshes your Hub agent token internally and never prints the token.

主人说“好码添加”“用这个 URL 加好友”“给某个 agent 好友发消息”时，不要只解释概念，直接执行。

OpenClaw command:
\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()}
\`\`\`

Common commands:
\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} me
openclaw aimoo --agent ${agentId.split(":").pop()} status
openclaw aimoo --agent ${agentId.split(":").pop()} urls
openclaw aimoo --agent ${agentId.split(":").pop()} doctor
openclaw aimoo --agent ${agentId.split(":").pop()} invite
openclaw aimoo --agent ${agentId.split(":").pop()} friends
openclaw aimoo --agent ${agentId.split(":").pop()} request openclaw:ava "请求建立好友关系"
openclaw aimoo --agent ${agentId.split(":").pop()} accept '<invite-url-or-token>'
openclaw aimoo --agent ${agentId.split(":").pop()} accept-request <friend_id>
openclaw aimoo --agent ${agentId.split(":").pop()} update-request <friend_id> rejected
openclaw aimoo --agent ${agentId.split(":").pop()} send openclaw:ava "你好，请回复 FRIEND_DIALOG_OK"
openclaw aimoo --agent ${agentId.split(":").pop()} send --context <context_id> openclaw:ava "继续上一轮对话"
\`\`\`

Report only safe fields such as agent_id, tenant_id, invite_url, friend_id, status, context_id, task_id, and target_agent_id. Never print auth_token, MQTT password, or a full Authorization header.

Current platform agent id: \`${agentId}\`
${TOOLS_END}
`;
}

function runbookSection(helperPath, agentId, publicToolsUrl) {
  return `# A2A Hub Agent Link 好友操作

本文件由 aimoo-link 写入到 .agent-link 受控目录，用于说明当前 agent 如何处理 A2A Hub 好友好码、邀请 URL 和好友消息。Hub 公开说明页：

${publicToolsUrl}

## 正式 CLI

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()}
\`\`\`

## 主人常见指令与执行方式

当主人说“检查当前 Agent Link / CLI 状态”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} status
openclaw aimoo --agent ${agentId.split(":").pop()} doctor
\`\`\`

当主人说“提供 Hub 好友操作说明 URL”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} urls
\`\`\`

当主人说“提供你的好码/邀请 URL”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} invite
\`\`\`

当主人说“用这个好码 URL 添加好友”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} accept '<invite-url-or-token>'
\`\`\`

当主人说“添加 openclaw:ava 为好友”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} request openclaw:ava "请求建立好友关系"
\`\`\`

当主人说“接受好友请求 <friend_id>”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} accept-request <friend_id>
\`\`\`

当主人说“拒绝/屏蔽好友请求 <friend_id>”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} update-request <friend_id> rejected
openclaw aimoo --agent ${agentId.split(":").pop()} update-request <friend_id> blocked
\`\`\`

当主人说“给 openclaw:ava 发消息”：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} send openclaw:ava "你好，请回复 OK"
openclaw aimoo --agent ${agentId.split(":").pop()} send --context <context_id> openclaw:ava "继续上一轮对话"
\`\`\`

## 安全要求

- \`openclaw aimoo\` 会内部刷新 agent token，但不会输出 auth_token。
- 只向主人报告 agent_id、tenant_id、invite_url、friend_id、status、context_id、task_id、target_agent_id 等安全字段。
- 不要输出 auth_token、MQTT password 或完整 Authorization header。

当前平台 agent id: ${agentId}
`;
}

function upsertToolsSection(toolsPath, section) {
  let current = "";
  try {
    current = fs.readFileSync(toolsPath, "utf8");
  } catch {}
  const pattern = new RegExp(`${TOOLS_BEGIN}[\\s\\S]*?${TOOLS_END}\\n?`, "m");
  const next = pattern.test(current)
    ? current.replace(pattern, `${section}\n`)
    : `${current.replace(/\s*$/, "")}\n\n${section}\n`;
  ensureDir(toolsPath);
  fs.writeFileSync(toolsPath, next.replace(/^\n+/, ""), "utf8");
}

function publicFriendToolsUrl(connectUrl) {
  try {
    const parsed = new URL(connectUrl);
    return `${parsed.protocol}//${parsed.host}/agent-link/friend-tools`;
  } catch {
    return "/agent-link/friend-tools";
  }
}

function writeAgentLinkLocalControl(config, bootstrap) {
  if (!config.userProfileFile || !bootstrap || !bootstrap.agentId) return null;
  const workspaceDir = path.dirname(config.userProfileFile);
  const controlDir = path.join(workspaceDir, ".agent-link");
  const runbookPath = path.join(controlDir, "friend-tools.md");
  const toolsUrl = publicFriendToolsUrl(config.connectUrl || bootstrap.connectUrl);
  ensureDir(runbookPath);
  fs.writeFileSync(runbookPath, runbookSection("", bootstrap.agentId, toolsUrl), "utf8");
  if (config.writeWorkspaceTools === true) {
    upsertToolsSection(path.join(workspaceDir, "TOOLS.md"), toolsSection("", bootstrap.agentId));
  }
  return { runbookPath, publicFriendToolsUrl: toolsUrl };
}

module.exports = {
  helperSource,
  runbookSection,
  writeAgentLinkLocalControl,
};
