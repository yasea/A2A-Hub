#!/usr/bin/env node
"use strict";

// A2A Hub Agent Link CLI bridge for aimoo-link plugin
// This file is generated and managed by the aimoo-link plugin.

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

// Load config from environment
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

function usage() {
  console.log(`A2A Hub Agent Link aimoo-link CLI bridge

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
`);
}

function baseUrlFromConnectUrl(connectUrl) {
  const parsed = new URL(connectUrl);
  return parsed.protocol + "//" + parsed.host;
}

function platformAgentId(agentId) {
  const value = String(agentId || "").trim();
  if (!value) throw new Error("agent_id is required");
  return value.includes(":") ? value : "openclaw:" + value;
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
  const pubNum = installResult && installResult.state && (installResult.state.public_number || installResult.state.publicNumber);
  return {
    agent_id: platformAgentId(config.agentId || config.localAgentId),
    local_agent_id: config.localAgentId,
    public_number: pubNum || null,
    connect_url: config.connectUrl,
    public_friend_tools_url: publicFriendToolsUrl(config.connectUrl),
    config_path: config.configPath,
    runbook_path: path.join(dir, "friend-tools.md"),
    install_result_path: path.join(dir, "install-result.json"),
    install_status: installResult && installResult.status,
    install_stage: installResult && installResult.stage,
    runtime_status: installResult && installResult.state && installResult.state.status,
    updated_at: installResult && installResult.updatedAt,
  };
}

async function requestJson(url, options) {
  options = options || {};
  const timeoutMs = Number(options.timeoutMs || 15000);
  const controller = new AbortController();
  const timer = setTimeout(function() { controller.abort(); }, timeoutMs);
  try {
    const response = await fetch(url, Object.assign({ signal: controller.signal }, options));
    const text = await response.text();
    let json = null;
    try {
      json = text ? JSON.parse(text) : null;
    } catch {}
    if (!response.ok) {
      const detail = json && (json.detail || json.message || JSON.stringify(json));
      throw new Error(response.status + " " + response.statusText + ": " + (detail || text));
    }
    return json || {};
  } finally {
    clearTimeout(timer);
  }
}

function publicFriendToolsUrl(connectUrl) {
  try {
    const parsed = new URL(connectUrl);
    return parsed.protocol + "//" + parsed.host + "/agent-link/friend-tools";
  } catch {
    return "/agent-link/friend-tools";
  }
}

function publicUrls(config) {
  const baseUrl = baseUrlFromConnectUrl(config.connectUrl);
  return {
    connect_url: config.connectUrl,
    manifest_url: baseUrl + "/v1/agent-link/manifest",
    friend_tools_url: publicFriendToolsUrl(config.connectUrl),
    self_register_url: baseUrl + "/v1/agent-link/self-register",
  };
}

function ensureRuntimeIdentityKey(config) {
  if (typeof config !== "object" || !config) return;
  if (config.runtimeIdentityKey) return;
  const keyFile = config.runtimeIdentityKeyFile;
  if (!keyFile) return;
  try {
    const existing = fs.readFileSync(keyFile, "utf8").trim();
    if (existing) { config.runtimeIdentityKey = existing; return; }
  } catch {}
  const { randomUUID } = require("node:crypto");
  const newKey = String(randomUUID()).split("-").join("");
  try {
    const dir = path.dirname(keyFile);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(keyFile, newKey, "utf8");
    config.runtimeIdentityKey = newKey;
  } catch {}
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

async function register(config) {
  ensureRuntimeIdentityKey(config);
  const baseUrl = baseUrlFromConnectUrl(config.connectUrl);
  const localAgentId = String(config.localAgentId || config.agentId || "").split(":").pop();
  const rawText = readText(config.userProfileFile);
  const agentSummary = "OpenClaw agent " + (localAgentId || "unknown");
  const body = {
    agent_id: platformAgentId(config.agentId || localAgentId),
    display_name: String(localAgentId || config.agentId || "agent").toUpperCase(),
    capabilities: { analysis: true, generic: true },
    config_json: {
      workspace: localAgentId,
      local_agent_id: localAgentId,
      plugin: "aimoo-link",
      local_helper: "openclaw aimoo",
      agent_summary: agentSummary,
      runtime_identity_key: config.runtimeIdentityKey || "",
    },
    owner_profile: rawText ? { source: "openclaw-user-md", raw_text: rawText, local_agent_id: localAgentId } : {},
  };
  const response = await requestJson(baseUrl + "/v1/agent-link/self-register", {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify(body),
    timeoutMs: config.httpTimeoutMs,
  });
  const data = response.data || {};
  if (!data.auth_token || !data.agent_id) throw new Error("self-register response missing auth_token or agent_id");
  return { baseUrl: baseUrl, data: data };
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
  const result = await register(config).then(function(ctx) {
    const headers = {
      "content-type": "application/json",
      accept: "application/json",
      authorization: "Bearer " + ctx.data.auth_token,
    };
    const agentId = ctx.data.agent_id;
    const timeoutMs = config.httpTimeoutMs || 15000;
    if (command === "me" || command === "invite") {
      return {
        agent_id: agentId,
        tenant_id: ctx.data.tenant_id,
        public_number: ctx.data.public_number || null,
        invite_url: ctx.data.invite_url,
      };
    }
    if (command === "doctor") {
      return requestJson(ctx.baseUrl + "/v1/agents/" + encodeURIComponent(agentId) + "/friends", {
        headers: headers,
        timeoutMs: timeoutMs,
      }).then(function(response) {
        return {
          agent_id: agentId,
          tenant_id: ctx.data.tenant_id,
          self_register: "ok",
          friends_list: "ok",
          friend_count: (response.data || []).length,
          public_friend_tools_url: publicFriendToolsUrl(config.connectUrl),
        };
      });
    }
    if (command === "friends") {
      return requestJson(ctx.baseUrl + "/v1/agents/" + encodeURIComponent(agentId) + "/friends", {
        headers: headers,
        timeoutMs: timeoutMs,
      }).then(function(response) {
        return { agent_id: agentId, friends: (response.data || []).map(safeFriend) };
      });
    }
    if (command === "request") {
      const target = process.argv[3];
      const message = process.argv.slice(4).join(" ") || "请求建立好友关系";
      if (!target) throw new Error("target_agent_id is required");
      return requestJson(ctx.baseUrl + "/v1/agents/" + encodeURIComponent(agentId) + "/friends", {
        method: "POST",
        headers: headers,
        body: JSON.stringify({ target_agent_id: target, message: message }),
        timeoutMs: timeoutMs,
      }).then(function(response) {
        return safeFriend(response.data);
      });
    }
    if (command === "accept-request" || command === "update-request") {
      const friendId = process.argv[3];
      if (!friendId) throw new Error("friend_id is required");
      const status = command === "accept-request" ? "accepted" : process.argv[4];
      if (["accepted", "rejected", "blocked"].indexOf(status) === -1) {
        throw new Error("status must be one of: accepted, rejected, blocked");
      }
      return requestJson(ctx.baseUrl + "/v1/agents/" + encodeURIComponent(agentId) + "/friends/" + encodeURIComponent(friendId), {
        method: "PATCH",
        headers: headers,
        body: JSON.stringify({ status: status }),
        timeoutMs: timeoutMs,
      }).then(function(response) {
        return safeFriend(response.data);
      });
    }
    if (command === "accept") {
      const token = tokenFromInvite(process.argv[3]);
      return requestJson(ctx.baseUrl + "/v1/agents/invite/accept?token=" + encodeURIComponent(token), {
        method: "POST",
        headers: headers,
        timeoutMs: timeoutMs,
      }).then(function(response) {
        return safeFriend(response.data);
      });
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
      return requestJson(ctx.baseUrl + "/v1/agent-link/messages/send", {
        method: "POST",
        headers: headers,
        body: JSON.stringify({
          context_id: contextId,
          target_agent_id: target,
          parts: [{ type: "text/plain", text: text }],
        }),
        timeoutMs: timeoutMs,
      }).then(function(response) {
        const data = response.data || {};
        return {
          task_id: data.task_id,
          context_id: data.context_id,
          target_agent_id: target,
        };
      });
    }
    throw new Error("unknown command: " + command);
  });
  console.log(JSON.stringify(result, null, 2));
}

main().catch(function(error) {
  console.error(JSON.stringify({ error: String(error && error.message ? error.message : error) }, null, 2));
  process.exit(1);
});
