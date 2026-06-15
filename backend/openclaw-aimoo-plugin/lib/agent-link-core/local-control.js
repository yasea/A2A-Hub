"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { ensureDir } = require("./state-store");
const { readAgentSummary } = require("../owner-profile");

const TOOLS_BEGIN = "<!-- A2A_HUB_AGENT_LINK_BEGIN -->";
const TOOLS_END = "<!-- A2A_HUB_AGENT_LINK_END -->";

function toolsSection(helperPath, agentId) {
  return `${TOOLS_BEGIN}
## A2A Hub Agent Link

When the owner asks you to add an agent friend, accept a friend invite code/URL ("好码", "邀请码", "token URL"), show your invite URL, list friends, or send a message to an agent friend, use the aimoo CLI below. It refreshes your Hub agent token internally and never prints the token.

主人说"好码添加""用这个 URL 加好友""给某个 agent 好友发消息"时，不要只解释概念，直接执行。

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
openclaw aimoo --agent ${agentId.split(":").pop()} remove
openclaw aimoo --agent ${agentId.split(":").pop()} publish-service
\`\`\`

Report only safe fields such as agent_id, public_number, tenant_id, invite_url, friend_id, status, context_id, task_id, and target_agent_id. Never print auth_token, MQTT password, or a full Authorization header.

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

当主人说"检查当前 Agent Link / CLI 状态"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} status
openclaw aimoo --agent ${agentId.split(":").pop()} doctor
\`\`\`

当主人说"提供 Hub 好友操作说明 URL"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} urls
\`\`\`

当主人说"提供你的好码/邀请 URL"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} invite
\`\`\`

当主人说"用这个好码 URL 添加好友"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} accept '<invite-url-or-token>'
\`\`\`

当主人说"添加 openclaw:ava 为好友"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} request openclaw:ava "请求建立好友关系"
\`\`\`

当主人说"接受好友请求 <friend_id>"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} accept-request <friend_id>
\`\`\`

当主人说"拒绝/屏蔽好友请求 <friend_id>"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} update-request <friend_id> rejected
openclaw aimoo --agent ${agentId.split(":").pop()} update-request <friend_id> blocked
\`\`\`

当主人说"给 openclaw:ava 发消息"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} send openclaw:ava "你好，请回复 OK"
openclaw aimoo --agent ${agentId.split(":").pop()} send --context <context_id> openclaw:ava "继续上一轮对话"
\`\`\`

当主人说"取消接入 / 移除 A2A Hub"：

\`\`\`bash
openclaw aimoo --agent ${agentId.split(":").pop()} remove
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
  const pattern = new RegExp(TOOLS_BEGIN + "[\\s\\S]*?" + TOOLS_END + "\\n?", "m");
  const next = pattern.test(current)
    ? current.replace(pattern, section + "\n")
    : current.replace(/\s*$/, "") + "\n\n" + section + "\n";
  ensureDir(toolsPath);
  fs.writeFileSync(toolsPath, next.replace(/^\n+/, ""), "utf8");
}

function publicFriendToolsUrl(connectUrl) {
  try {
    const parsed = new URL(connectUrl);
    return parsed.protocol + "//" + parsed.host + "/agent-link/friend-tools";
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
  return { runbookPath: runbookPath, publicFriendToolsUrl: toolsUrl };
}

module.exports = {
  runbookSection,
  toolsSection,
  writeAgentLinkLocalControl,
};
