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
  console.log(`A2A Hub Agent Link CLI aimoo-link bridge

Usage:
  openclaw aimoo list [--pending] [--online]     List all agents and their status
  openclaw aimoo status                          Show all agents status (no --agent)
  openclaw aimoo --agent <id> status             Show specific agent status
  openclaw aimoo doctor                          Run diagnostics for all agents
  openclaw aimoo --agent <id> doctor             Run diagnostics for specific agent
  openclaw aimoo --agent <id> me                 Show agent_id, tenant_id, invite_url
  openclaw aimoo --agent <id> urls               Show public Agent Link URLs
  openclaw aimoo --agent <id> invite             Show invite_url
  openclaw aimoo --agent <id> friends            List current agent friends
  openclaw aimoo --agent <id> remove             Remove agent from A2A Hub
  openclaw aimoo --agent <id> publish-service    Publish agent as a service
  openclaw aimoo --agent <id> services           List available services on Hub
  openclaw aimoo --agent <id> services register  Register current agent as a service
  openclaw aimoo --agent <id> services delete <id> Delete a service
  openclaw aimoo --agent <id> services info <id> View service details
  openclaw aimoo --agent <id> services update <id> Update service title/summary
  openclaw aimoo --agent <id> chat <svc> <msg>   Chat with a service agent
  openclaw aimoo setup [agentId] [--connect-url] Configure agent and restart Gateway
  openclaw aimoo --agent <id> request <target>   Create friend request
  openclaw aimoo --agent <id> accept-request <id> Accept pending friend request
  openclaw aimoo --agent <id> accept <url>       Accept invite URL or token
  openclaw aimoo --agent <id> send <target> <msg> Send message to agent friend
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
    } catch (e) {
      if (response.ok) {
        throw new Error("响应不是有效的 JSON: " + text.substring(0, 200));
      }
    }
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

// ─────────────────────────────────────────────────────────────────────
// 全局命令：读取 openclaw.json，不需要 agent config
// ─────────────────────────────────────────────────────────────────────

function getOpenClawHome() {
  return process.env.OPENCLAW_HOME || path.join(process.env.HOME || "", ".openclaw");
}

function readAllAgentStates() {
  var openclawHome = getOpenClawHome();
  var configPath = path.join(openclawHome, "openclaw.json");
  if (!fs.existsSync(configPath)) return { agents: [], error: "openclaw.json not found", openclawHome: openclawHome, configPath: configPath, cfg: null, instances: [] };
  var cfg = null;
  try {
    cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch (err) {
    return { agents: [], error: "openclaw.json parse error: " + String(err.message || err), openclawHome: openclawHome, configPath: configPath, cfg: null, instances: [] };
  }
  var agents = cfg.agents && Array.isArray(cfg.agents.list) ? cfg.agents.list : [];
  var channel = cfg.channels && cfg.channels.aimoo && typeof cfg.channels.aimoo === "object" ? cfg.channels.aimoo : {};
  var instances = Array.isArray(channel.instances) ? channel.instances : [];
  var installedSet = new Set(instances.map(function(i) { return i.localAgentId; }).filter(Boolean));

  var result = [];
  for (const agent of agents) {
    const id = typeof agent === "string" ? agent : (agent && agent.id);
    if (!id) continue;
    const shortId = id.split(":").pop();
    const installed = installedSet.has(shortId);
    const instance = instances.find(function(i) { return i.localAgentId === shortId; }) || null;
    const stateFile = path.join(openclawHome, "channels", "aimoo", shortId, "state.json");
    let state = null;
    try {
      if (fs.existsSync(stateFile)) {
        state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
      }
    } catch {}
    var status = installed ? (state && state.status === "online" ? "online" : "installed") : "pending";
    var item = { id: shortId, status: status };
    if (installed && instance) {
      if (instance.connectUrl) item.connect_url = instance.connectUrl;
      item._instance = instance; // 内部用，避免重复读 config
    }
    if (state) item._state = state; // 内部用
    if (state && state.agentId) item.platform_agent_id = state.agentId;
    if (state && state.tenantId) item.tenant_id = state.tenantId;
    if (state && state.publicNumber) item.public_number = state.publicNumber;
    if (state && state.topic) item.topic = state.topic;
    if (state && state.lastError) item.last_error = state.lastError;
    result.push(item);
  }
  return { agents: result, openclawHome: openclawHome, configPath: configPath, cfg: cfg, instances: instances };
}

function handleListCommand() {
  var pendingOnly = false;
  var onlineOnly = false;
  for (var i = 3; i < process.argv.length; i++) {
    if (process.argv[i] === "--pending") pendingOnly = true;
    if (process.argv[i] === "--online") onlineOnly = true;
  }
  var data = readAllAgentStates();
  var agents = data.agents;
  if (pendingOnly) agents = agents.filter(function(a) { return a.status === "pending"; });
  if (onlineOnly) agents = agents.filter(function(a) { return a.status === "online"; });
  console.log(JSON.stringify({ agents: agents }, null, 2));
}

function handleStatusAll() {
  var data = readAllAgentStates();
  console.log(JSON.stringify({ agents: data.agents }, null, 2));
}

async function handleDoctorAll() {
  var data = readAllAgentStates();
  if (data.error) {
    console.log(JSON.stringify({ agents: [], error: data.error }, null, 2));
    return;
  }
  var results = [];
  for (const agent of data.agents) {
    var item = { id: agent.id, status: agent.status };
    if (agent.status === "pending") {
      item.diagnosis = "未安装 aimoo-link 插件";
      results.push(item);
      continue;
    }
    var instance = agent._instance || null;
    if (!instance || !instance.connectUrl) {
      item.diagnosis = "无 connectUrl 配置";
      results.push(item);
      continue;
    }
    try {
      var agentConfig = {
        localAgentId: agent.id,
        agentId: agent.platform_agent_id || ("openclaw:" + agent.id),
        connectUrl: instance.connectUrl,
        userProfileFile: instance.userProfileFile || "",
        stateFile: instance.stateFile || "",
        httpTimeoutMs: instance.httpTimeoutMs || 15000,
        runtimeIdentityKey: instance.runtimeIdentityKey || "",
        runtimeIdentityKeyFile: instance.runtimeIdentityKeyFile || "",
      };
      ensureRuntimeIdentityKey(agentConfig);
      var ctx = await register(agentConfig);
      item.hub_connect = "ok";
      item.agent_id = ctx.data.agent_id;
      item.tenant_id = ctx.data.tenant_id;

      var headers = {
        "content-type": "application/json",
        accept: "application/json",
        authorization: "Bearer " + ctx.data.auth_token,
      };
      var friendsResp = await requestJson(ctx.baseUrl + "/v1/agents/" + encodeURIComponent(ctx.data.agent_id) + "/friends", {
        headers: headers,
        timeoutMs: agentConfig.httpTimeoutMs,
      });
      item.friend_count = (friendsResp.data || []).length;
      item.diagnosis = "ok";
    } catch (err) {
      item.diagnosis = "Hub 连接失败: " + String(err.message || err);
    }
    results.push(item);
  }
  console.log(JSON.stringify({ agents: results }, null, 2));
}

// ─────────────────────────────────────────────────────────────────────
// repair / update 命令
// ─────────────────────────────────────────────────────────────────────

var EXTERNAL_CHANNELS = ["telegram", "whatsapp", "signal", "discord", "slack", "wechat", "line"];

function hasExternalChannel(cfg, agentId) {
  var channels = (cfg && cfg.channels) || {};
  for (var chName of Object.keys(channels)) {
    if (chName === "aimoo") continue;
    if (EXTERNAL_CHANNELS.indexOf(chName) === -1) continue;
    var ch = channels[chName];
    if (!ch || ch.enabled === false) continue;
    var accounts = ch.accounts || {};
    var accountKeys = Object.keys(accounts);
    if (accountKeys.length === 0) return true; // 全局启用的外部渠道
    if (accounts[agentId] || accounts["default"]) return true;
  }
  return false;
}

async function handleRepairSingle(config, doFix) {
  var agentId = config.localAgentId;
  var result = { agent_id: agentId, issues: [], fixed: [] };

  // 检查 connectUrl
  if (!config.connectUrl) {
    result.issues.push("缺少 connectUrl");
  }

  // 检查 runtimeIdentityKey
  if (!config.runtimeIdentityKey) {
    if (doFix) {
      ensureRuntimeIdentityKey(config);
      result.fixed.push("已生成 runtimeIdentityKey");
    } else {
      result.issues.push("缺少 runtimeIdentityKey（--fix 可自动修复）");
    }
  }

  // 检查 Hub 连通性
  try {
    var ctx = await register(config);
    result.hub_connect = "ok";
    result.agent_id = ctx.data.agent_id;
  } catch (err) {
    result.issues.push("Hub 连接失败: " + String(err.message || err));
  }

  // 检查 state.json
  if (config.stateFile && fs.existsSync(config.stateFile)) {
    try {
      var state = JSON.parse(fs.readFileSync(config.stateFile, "utf8"));
      if (state.status !== "online") {
        result.issues.push("state.json status=" + state.status + "（预期 online）");
      }
      if (state.lastError) {
        result.issues.push("last_error: " + JSON.stringify(state.lastError));
      }
    } catch {}
  } else {
    result.issues.push("state.json 不存在");
  }

  result.ok = result.issues.length === 0;
  console.log(JSON.stringify(result, null, 2));
}

async function handleRepairAll(doFix) {
  var data = readAllAgentStates();
  if (data.error) {
    console.log(JSON.stringify({ agents: [], error: data.error }, null, 2));
    return;
  }
  var results = [];
  for (var agent of data.agents) {
    var item = { id: agent.id, status: agent.status, issues: [], fixed: [] };

    if (agent.status === "pending") {
      item.issues.push("未安装 aimoo-link 插件");
      results.push(item);
      continue;
    }

    var instance = agent._instance;
    if (!instance || !instance.connectUrl) {
      item.issues.push("缺少 connectUrl");
      results.push(item);
      continue;
    }

    // 检查外部渠道
    if (!hasExternalChannel(data.cfg, agent.id)) {
      item.issues.push("无外部通信渠道（Telegram/WhatsApp 等），注册为 service 后外部用户无法对话");
    }

    // 检查 runtimeIdentityKey
    if (!instance.runtimeIdentityKey && !instance.runtimeIdentityKeyFile) {
      if (doFix && data.cfg) {
        // 修复：写入 runtimeIdentityKey 到 openclaw.json
        var crypto = require("node:crypto");
        var newKey = crypto.randomUUID().replace(/-/g, "");
        var instances = (data.cfg.channels && data.cfg.channels.aimoo && data.cfg.channels.aimoo.instances) || [];
        var inst = instances.find(function(i) { return i.localAgentId === agent.id; });
        if (inst) {
          var keyFile = inst.runtimeIdentityKeyFile || path.join(data.openclawHome, "channels", "aimoo", agent.id, "runtime-identity-key");
          try {
            fs.mkdirSync(path.dirname(keyFile), { recursive: true });
            fs.writeFileSync(keyFile, newKey, "utf8");
            inst.runtimeIdentityKeyFile = keyFile;
            inst.runtimeIdentityKey = newKey;
            fs.writeFileSync(data.configPath, JSON.stringify(data.cfg, null, 2) + "\n", "utf8");
            item.fixed.push("已生成 runtimeIdentityKey");
          } catch (err) {
            item.issues.push("runtimeIdentityKey 修复失败: " + String(err.message || err));
          }
        }
      } else {
        item.issues.push("缺少 runtimeIdentityKey（--fix 可自动修复）");
      }
    }

    // 检查 Hub 连通性
    try {
      var agentConfig = {
        localAgentId: agent.id,
        agentId: agent.platform_agent_id || ("openclaw:" + agent.id),
        connectUrl: instance.connectUrl,
        userProfileFile: instance.userProfileFile || "",
        stateFile: instance.stateFile || "",
        httpTimeoutMs: instance.httpTimeoutMs || 15000,
        runtimeIdentityKey: instance.runtimeIdentityKey || "",
        runtimeIdentityKeyFile: instance.runtimeIdentityKeyFile || "",
      };
      ensureRuntimeIdentityKey(agentConfig);
      await register(agentConfig);
      item.hub_connect = "ok";
    } catch (err) {
      item.issues.push("Hub 连接失败: " + String(err.message || err));
    }

    // 检查 state.json
    var stateFile = path.join(data.openclawHome, "channels", "aimoo", agent.id, "state.json");
    if (fs.existsSync(stateFile)) {
      try {
        var state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
        if (state.status !== "online") item.issues.push("state status=" + state.status);
        if (state.lastError) item.issues.push("last_error: " + JSON.stringify(state.lastError));
      } catch {}
    } else {
      item.issues.push("state.json 不存在");
    }

    item.ok = item.issues.length === 0;
    results.push(item);
  }
  console.log(JSON.stringify({ agents: results }, null, 2));
}

async function handleUpdate() {
  var openclawHome = getOpenClawHome();
  var pluginDir = path.join(openclawHome, "plugins", "aimoo-link");
  var tarball = path.join(pluginDir, "aimoo-link.tar.gz");

  var currentVersion = "unknown";
  try {
    var pkg = JSON.parse(fs.readFileSync(path.join(pluginDir, "package.json"), "utf8"));
    currentVersion = pkg.version || "unknown";
  } catch {}

  if (!fs.existsSync(tarball)) {
    console.log(JSON.stringify({ error: "aimoo-link.tar.gz not found in " + pluginDir, hint: "Download the plugin tarball first" }, null, 2));
    process.exit(1);
  }

  var { execSync } = require("child_process");
  var backupDir = pluginDir + ".bak." + Date.now();
  try {
    // 备份当前插件目录
    fs.cpSync(pluginDir, backupDir, { recursive: true });

    execSync("tar -xzf " + JSON.stringify(tarball) + " -C " + JSON.stringify(pluginDir), { timeout: 15000 });
    var newPkg = JSON.parse(fs.readFileSync(path.join(pluginDir, "package.json"), "utf8"));

    // 更新全局 skill
    var skillSrc = path.join(pluginDir, "skills", "aimoo", "SKILL.md");
    var skillDstDir = path.join(openclawHome, "skills", "aimoo");
    var skillDst = path.join(skillDstDir, "SKILL.md");
    if (fs.existsSync(skillSrc)) {
      try {
        fs.mkdirSync(skillDstDir, { recursive: true });
        fs.copyFileSync(skillSrc, skillDst);
      } catch {}
    }

    // 清理备份
    try { fs.rmSync(backupDir, { recursive: true, force: true }); } catch {}

    console.log(JSON.stringify({
      status: "updated",
      previous_version: currentVersion,
      new_version: newPkg.version || "unknown",
      plugin_dir: pluginDir,
      skill_updated: fs.existsSync(skillDst),
      hint: "Restart Gateway to load the new version: systemctl --user restart openclaw-gateway.service",
    }, null, 2));
  } catch (err) {
    // 恢复备份
    if (fs.existsSync(backupDir)) {
      try {
        fs.rmSync(pluginDir, { recursive: true, force: true });
        fs.cpSync(backupDir, pluginDir, { recursive: true });
        fs.rmSync(backupDir, { recursive: true, force: true });
      } catch {}
    }
    console.log(JSON.stringify({ error: "update failed: " + String(err.message || err), hint: "Backup restored from: " + backupDir }, null, 2));
    process.exit(1);
  }
}

async function main() {
  const command = process.argv[2] || "help";
  if (command === "help" || command === "--help" || command === "-h") {
    usage();
    return;
  }

  // 全局命令：不需要 agent config
  if (command === "list") { handleListCommand(); return; }
  if (command === "repair-all") { await handleRepairAll(); return; }
  if (command === "update") { await handleUpdate(); return; }

  // 智能命令：无 config 时走全局，有 config 时走单个
  var hasAgentConfig = !!(process.env.AIMOO_LINK_CLI_CONFIG_JSON || "").trim();
  if (command === "status") {
    if (hasAgentConfig) { console.log(JSON.stringify(localStatus(loadConfig()), null, 2)); }
    else { handleStatusAll(); }
    return;
  }
  if (command === "doctor") {
    if (hasAgentConfig) {
      var cfg = loadConfig();
      var ctx = await register(cfg);
      var headers = { "content-type": "application/json", accept: "application/json", authorization: "Bearer " + ctx.data.auth_token };
      var friendsResp = await requestJson(ctx.baseUrl + "/v1/agents/" + encodeURIComponent(ctx.data.agent_id) + "/friends", { headers: headers, timeoutMs: cfg.httpTimeoutMs || 15000 });
      console.log(JSON.stringify({ agent_id: ctx.data.agent_id, tenant_id: ctx.data.tenant_id, self_register: "ok", friends_list: "ok", friend_count: (friendsResp.data || []).length }, null, 2));
    } else { await handleDoctorAll(); }
    return;
  }
  if (command === "repair") {
    var doFix = process.argv.indexOf("--fix") !== -1;
    if (hasAgentConfig) { await handleRepairSingle(loadConfig(), doFix); }
    else { await handleRepairAll(doFix); }
    return;
  }

  // 以下命令必须有 agent config
  var config = loadConfig();
  if (command === "urls") {
    console.log(JSON.stringify(publicUrls(config), null, 2));
    return;
  }
  if (command === "setup") {
    // 配置 agent 到 openclaw.json 并重启 Gateway
    var connectUrl = "";
    var doRestart = false;
    var doWait = false;
    var autoPublishService = false;
    var targetAgentId = "";

    // 解析参数
    for (var i = 3; i < process.argv.length; i++) {
      if (process.argv[i] === "--connect-url" && i + 1 < process.argv.length) {
        connectUrl = process.argv[++i];
      } else if (process.argv[i] === "--restart") {
        doRestart = true;
      } else if (process.argv[i] === "--wait") {
        doWait = true;
      } else if (process.argv[i] === "--auto-publish-service") {
        autoPublishService = true;
      } else if (!targetAgentId) {
        targetAgentId = process.argv[i];
      }
    }

    // 如果没有指定 agent，使用 config 中的
    if (!targetAgentId && config.localAgentId) {
      targetAgentId = config.localAgentId.split(":").pop();
    }
    if (!targetAgentId) {
      console.error(JSON.stringify({ error: "agent_id is required. Pass as argument or set via --agent" }, null, 2));
      process.exit(1);
    }

    var openclawHome = process.env.OPENCLAW_HOME || path.join(process.env.HOME || "", ".openclaw");
    var configPath = path.join(openclawHome, "openclaw.json");
    var channelDir = path.join(openclawHome, "channels", "aimoo");
    var pluginDir = path.join(openclawHome, "plugins", "aimoo-link");
    var instanceDir = path.join(channelDir, targetAgentId);
    var workspaceDir = path.join(openclawHome, "workspace", targetAgentId);
    var userMdFile = path.join(workspaceDir, "USER.md");

    try {
      // 读取或创建 openclaw.json
      var cfg = {};
      try {
        if (fs.existsSync(configPath)) {
          cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
        }
      } catch {}

      // 确保 plugins 配置
      cfg.plugins = cfg.plugins && typeof cfg.plugins === "object" ? cfg.plugins : {};
      cfg.plugins.allow = Array.isArray(cfg.plugins.allow) ? cfg.plugins.allow : [];
      if (!cfg.plugins.allow.includes("aimoo-link")) {
        cfg.plugins.allow.push("aimoo-link");
      }
      cfg.plugins.load = cfg.plugins.load && typeof cfg.plugins.load === "object" ? cfg.plugins.load : {};
      cfg.plugins.load.paths = Array.isArray(cfg.plugins.load.paths) ? cfg.plugins.load.paths : [];
      if (!cfg.plugins.load.paths.includes(pluginDir)) {
        cfg.plugins.load.paths.push(pluginDir);
      }
      cfg.plugins.entries = cfg.plugins.entries && typeof cfg.plugins.entries === "object" ? cfg.plugins.entries : {};
      cfg.plugins.entries["aimoo-link"] = { ...(cfg.plugins.entries["aimoo-link"] || {}), enabled: true };

      // 确保 agents.list
      cfg.agents = cfg.agents && typeof cfg.agents === "object" ? cfg.agents : {};
      cfg.agents.list = Array.isArray(cfg.agents.list) ? cfg.agents.list : [];
      if (!cfg.agents.list.some(function(item) { return item && item.id === targetAgentId; })) {
        cfg.agents.list.push({ id: targetAgentId });
      }

      // 确保 channels.aimoo
      cfg.channels = cfg.channels && typeof cfg.channels === "object" ? cfg.channels : {};
      cfg.channels.aimoo = cfg.channels.aimoo && typeof cfg.channels.aimoo === "object" ? cfg.channels.aimoo : {};
      cfg.channels.aimoo.enabled = true;
      if (!cfg.channels.aimoo.replyMode) cfg.channels.aimoo.replyMode = "openclaw-agent";
      if (typeof cfg.channels.aimoo.recordOpenClawSession !== "boolean") cfg.channels.aimoo.recordOpenClawSession = true;

      // 更新 instance
      // 读取 runtimeIdentityKey
      var runtimeIdentityKey = cfg.meta && cfg.meta.runtimeIdentityKey || "";
      // 读取已有的 connectUrl（如果未指定新的）
      var existingConnectUrl = "";
      var rawInstances2 = Array.isArray(cfg.channels && cfg.channels.aimoo && cfg.channels.aimoo.instances) ? cfg.channels.aimoo.instances : [];
      var existingInstance = rawInstances2.find(function(i) { return i && i.localAgentId === targetAgentId; });
      if (existingInstance && existingInstance.connectUrl) existingConnectUrl = existingInstance.connectUrl;

      var nextInstance = {
        enabled: true,
        localAgentId: targetAgentId,
        agentId: config.agentId || "openclaw:" + (runtimeIdentityKey || "unknown") + ":" + targetAgentId,
        connectUrl: connectUrl || existingConnectUrl || config.connectUrl || "",
        userProfileFile: userMdFile,
        stateFile: path.join(instanceDir, "state.json"),
        tlsRejectUnauthorized: false,
      };
      var rawInstances = rawInstances2;
      cfg.channels.aimoo.instances = rawInstances
        .filter(function(item) { return item && item.localAgentId !== targetAgentId; })
        .concat([nextInstance]);

      // 写入配置
      fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2) + "\n", "utf8");
      fs.mkdirSync(instanceDir, { recursive: true });
      fs.mkdirSync(path.join(openclawHome, "logs"), { recursive: true });

      console.log(JSON.stringify({
        status: "configured",
        agent_id: targetAgentId,
        config_path: configPath,
        instance_dir: instanceDir,
        restart_requested: doRestart,
      }, null, 2));

      // 重启 Gateway
      if (doRestart) {
        var { execSync } = require("child_process");
        var restarted = false;
        // 优先使用 systemctl
        try {
          execSync("systemctl --user restart openclaw-gateway.service", { timeout: 15000 });
          console.log(JSON.stringify({ restart: "systemctl", status: "restarted" }, null, 2));
          restarted = true;
        } catch (err) {
          // systemctl 失败，尝试 openclaw 命令
          try {
            var port = (cfg.gateway && cfg.gateway.port) || "18789";
            // 先 kill 旧进程
            try { execSync("fuser -k " + port + "/tcp", { timeout: 3000 }); } catch {}
            execSync("sleep 1", { timeout: 2000 });
            // 使用 --allow-unconfigured 绕过 gateway.mode 检查
            execSync("openclaw gateway --port " + port + " --allow-unconfigured", {
              timeout: 8000,
              stdio: "ignore",
              detached: true,
            });
            console.log(JSON.stringify({ restart: "openclaw-cmd", status: "started" }, null, 2));
            restarted = true;
          } catch (err2) {
            console.log(JSON.stringify({ restart: "failed", error: String(err2.message || err2) }, null, 2));
          }
        }
        if (restarted) {
          // 等待 Gateway 启动
          try { execSync("sleep 3", { timeout: 5000 }); } catch {}
        }
      }

      // 等待上线
      if (doWait) {
        var timeout = 120;
        var interval = 3;
        var elapsed = 0;
        while (elapsed < timeout) {
          var stateFile = path.join(instanceDir, "state.json");
          try {
            if (fs.existsSync(stateFile)) {
              var state = JSON.parse(fs.readFileSync(stateFile, "utf8"));
              if (state.status === "online") {
                console.log(JSON.stringify({ wait: "completed", status: "online", elapsed: elapsed }, null, 2));
                break;
              }
            }
          } catch {}
          // 同步等待（简单实现）
          execSync("sleep " + interval, { timeout: (interval + 1) * 1000 });
          elapsed += interval;
        }
        if (elapsed >= timeout) {
          console.log(JSON.stringify({ wait: "timeout", status: "not_online", elapsed: elapsed }, null, 2));
        }
      }

      // 自动注册服务
      if (autoPublishService) {
        var cfgForChannels = null;
        try { cfgForChannels = JSON.parse(fs.readFileSync(configPath, "utf8")); } catch {}
        if (!hasExternalChannel(cfgForChannels, targetAgentId)) {
          console.log(JSON.stringify({
            publish_service: "skipped",
            reason: "no external channel configured for agent " + targetAgentId,
            hint: "Configure Telegram/WhatsApp/Signal channel first, then re-run setup with --auto-publish-service",
          }, null, 2));
          return;
        }

        var serviceHintPath = path.join(instanceDir, "service-hint");
        if (fs.existsSync(serviceHintPath) && fs.readFileSync(serviceHintPath, "utf8").trim()) {
          // 调用 publish-service 逻辑
          try {
            var authToken = null;
            var platformAgentId = null;
            var stateFile = path.join(instanceDir, "state.json");
            if (fs.existsSync(stateFile)) {
              var stateData = JSON.parse(fs.readFileSync(stateFile, "utf8"));
              authToken = stateData.authToken || null;
              platformAgentId = stateData.agentId || null;
            }
            if (!authToken || !platformAgentId) {
              // fallback: 通过 self-register 获取 token
              var ctx = await register(config);
              authToken = ctx.data.auth_token;
              platformAgentId = ctx.data.agent_id;
            }
            if (authToken && platformAgentId) {
              // 从 SOUL.md 提取标题
              var cliTitle = targetAgentId;
              var soulPaths = [
                path.join(workspaceDir, "SOUL.md"),
                path.join(openclawHome, "workspace-" + targetAgentId, "SOUL.md"),
              ];
              for (var soulPath of soulPaths) {
                try {
                  if (fs.existsSync(soulPath)) {
                    var firstLine = fs.readFileSync(soulPath, "utf8").split("\n")[0].trim();
                    var titleMatch = firstLine.match(/^#\s*SOUL\.md\s*[—\-]\s*(.+)/);
                    cliTitle = titleMatch ? titleMatch[1].trim() : firstLine.replace(/^#\s*/, "").trim();
                    break;
                  }
                } catch {}
              }

              // 获取 connectUrl（从 openclaw.json 实例配置、state.json 或 config）
              var publishConnectUrl = "";
              // 1. 从 openclaw.json 实例配置读取
              var cfg2 = {};
              try { cfg2 = JSON.parse(fs.readFileSync(configPath, "utf8")); } catch {}
              var instances2 = (cfg2.channels && cfg2.channels.aimoo && cfg2.channels.aimoo.instances) || [];
              var myInstance = instances2.find(function(i) { return i && i.localAgentId === targetAgentId; });
              if (myInstance && myInstance.connectUrl) publishConnectUrl = myInstance.connectUrl;
              // 2. 从 state.json 读取
              if (!publishConnectUrl && fs.existsSync(stateFile)) {
                var st = JSON.parse(fs.readFileSync(stateFile, "utf8"));
                publishConnectUrl = st.connectUrl || "";
              }
              // 3. fallback 到 config
              if (!publishConnectUrl) publishConnectUrl = config.connectUrl || "";
              if (!publishConnectUrl) {
                console.log(JSON.stringify({ publish_service: "skipped", error: "no connectUrl available" }, null, 2));
                return;
              }

              var baseUrl = baseUrlFromConnectUrl(publishConnectUrl);
              var headers = {
                "content-type": "application/json",
                accept: "application/json",
                authorization: "Bearer " + authToken,
              };

              // 检查是否已有服务（避免重复创建）
              var existingService = null;
              try {
                var listResp = await requestJson(baseUrl + "/v1/services?limit=50", {
                  headers: headers,
                  timeoutMs: 8000,
                });
                var existingServices = (listResp.data || listResp || []);
                if (Array.isArray(existingServices)) {
                  // 查找同一 handler_agent_id 的服务
                  for (var svc of existingServices) {
                    if (svc.handler_agent_id === platformAgentId) {
                      existingService = svc;
                      break;
                    }
                  }
                }
              } catch (listErr) {
                // 查询失败不影响创建流程
              }

              if (existingService) {
                if (existingService.status === "ACTIVE") {
                  console.log(JSON.stringify({ publish_service: "skipped", reason: "service already exists and is ACTIVE", service_id: existingService.service_id }, null, 2));
                  return;
                }
                // INACTIVE 状态，重新激活
                try {
                  var reactivateResp = await requestJson(baseUrl + "/v1/services/" + existingService.service_id, {
                    method: "PATCH",
                    headers: headers,
                    body: JSON.stringify({
                      status: "ACTIVE",
                      title: cliTitle,
                      summary: cliSummary || existingService.summary || "Auto-published via openclaw aimoo setup",
                      visibility: "listed",
                    }),
                    timeoutMs: 10000,
                  });
                  console.log(JSON.stringify({ publish_service: "reactivated", service: reactivateResp.data || reactivateResp }, null, 2));
                  return;
                } catch (reactivateErr) {
                  // 重新激活失败，继续创建新服务
                }
              }

              var response = await requestJson(baseUrl + "/v1/services", {
                method: "POST",
                headers: headers,
                body: JSON.stringify({
                  handler_agent_id: platformAgentId,
                  title: cliTitle,
                  summary: cliSummary || "Auto-published via openclaw aimoo setup",
                  visibility: "listed",
                }),
                timeoutMs: 10000,
              });
              console.log(JSON.stringify({ publish_service: "success", service: response.data || response }, null, 2));
            }
          } catch (err) {
            console.log(JSON.stringify({ publish_service: "failed", error: String(err.message || err) }, null, 2));
          }
        }
      }

    } catch (err) {
      console.error(JSON.stringify({ error: String(err.message || err) }, null, 2));
      process.exit(1);
    }
    return;
  }
  if (command === "remove") {
    // 优先从 state.json 读取 authToken，避免不必要的 self-register
    var authToken = null;
    var platformAgentId = null;
    try {
      if (config.stateFile && fs.existsSync(config.stateFile)) {
        var stateData = JSON.parse(fs.readFileSync(config.stateFile, "utf8"));
        authToken = stateData.authToken || null;
        platformAgentId = stateData.agentId || null;
      }
    } catch {}
    if (!authToken || !platformAgentId) {
      // fallback: 通过 self-register 获取 token
      var ctx = await register(config);
      authToken = ctx.data.auth_token;
      platformAgentId = ctx.data.agent_id;
    }
    var baseUrl = baseUrlFromConnectUrl(config.connectUrl);
    var headers = {
      "content-type": "application/json",
      accept: "application/json",
      authorization: "Bearer " + authToken,
    };
    var timeoutMs = config.httpTimeoutMs || 15000;
    var response = await requestJson(baseUrl + "/v1/agent-link/unregister", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({ confirm: true }),
      timeoutMs: timeoutMs,
    });
    // Clean up local config
    var cleanupResult = { hub_response: response.data || response };
    try {
      var openclawHome = process.env.OPENCLAW_HOME || path.join(process.env.HOME || "", ".openclaw");
      var configPath = path.join(openclawHome, "openclaw.json");
      if (fs.existsSync(configPath)) {
        var cfg = JSON.parse(fs.readFileSync(configPath, "utf8"));
        var shortId = String(config.localAgentId || "").split(":").pop();
        // Remove from channels.aimoo.instances
        if (cfg.channels && cfg.channels.aimoo && Array.isArray(cfg.channels.aimoo.instances)) {
          cfg.channels.aimoo.instances = cfg.channels.aimoo.instances.filter(function(item) {
            return item && item.localAgentId !== shortId && item.agentId !== platformAgentId;
          });
          if (cfg.channels.aimoo.instances.length === 0) {
            delete cfg.channels.aimoo;
            // Clean up plugins
            if (cfg.plugins && cfg.plugins.allow) {
              cfg.plugins.allow = cfg.plugins.allow.filter(function(p) { return p !== "aimoo-link"; });
            }
            if (cfg.plugins && cfg.plugins.load && cfg.plugins.load.paths) {
              cfg.plugins.load.paths = cfg.plugins.load.paths.filter(function(p) { return p.indexOf("aimoo-link") === -1; });
            }
            if (cfg.plugins && cfg.plugins.entries && cfg.plugins.entries["aimoo-link"]) {
              delete cfg.plugins.entries["aimoo-link"];
            }
          }
        }
        fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2) + "\n", "utf8");
        cleanupResult.config_cleaned = true;
        cleanupResult.config_path = configPath;
      }
    } catch (err) {
      cleanupResult.config_cleaned = false;
      cleanupResult.config_error = String(err.message || err);
    }
    // Clean up local files
    try {
      if (config.stateFile && fs.existsSync(config.stateFile)) {
        fs.unlinkSync(config.stateFile);
        cleanupResult.state_removed = true;
      }
    } catch {}
    try {
      var dir = controlDir(config);
      var installResult = path.join(dir, "install-result.json");
      if (fs.existsSync(installResult)) { fs.unlinkSync(installResult); }
      var friendTools = path.join(dir, "friend-tools.md");
      if (fs.existsSync(friendTools)) { fs.unlinkSync(friendTools); }
      cleanupResult.files_cleaned = true;
    } catch {}
    console.log(JSON.stringify(cleanupResult, null, 2));
    return;
  }
  if (command === "publish-service") {
    // 优先从 state.json 读取 authToken，避免不必要的 self-register
    var authToken = null;
    var platformAgentId = null;
    try {
      if (config.stateFile && fs.existsSync(config.stateFile)) {
        var stateData = JSON.parse(fs.readFileSync(config.stateFile, "utf8"));
        authToken = stateData.authToken || null;
        platformAgentId = stateData.agentId || null;
      }
    } catch {}
    if (!authToken || !platformAgentId) {
      // fallback: 通过 self-register 获取 token
      var ctx = await register(config);
      authToken = ctx.data.auth_token;
      platformAgentId = ctx.data.agent_id;
    }
    var baseUrl = baseUrlFromConnectUrl(config.connectUrl);
    var headers = {
      "content-type": "application/json",
      accept: "application/json",
      authorization: "Bearer " + authToken,
    };
    // 解析命令行参数
    var cliTitle = "";
    var cliSummary = "";
    var cliVisibility = "listed";
    for (var i = 3; i < process.argv.length; i++) {
      if (process.argv[i] === "--title" && i + 1 < process.argv.length) {
        cliTitle = process.argv[++i];
      } else if (process.argv[i] === "--summary" && i + 1 < process.argv.length) {
        cliSummary = process.argv[++i];
      } else if (process.argv[i] === "--visibility" && i + 1 < process.argv.length) {
        cliVisibility = process.argv[++i];
      }
    }
    if (!["listed", "private", "direct_link"].includes(cliVisibility)) {
      console.error(JSON.stringify({ error: "visibility must be one of: listed, private, direct_link" }, null, 2));
      process.exit(1);
    }
    // 从 SOUL.md 提取默认标题
    if (!cliTitle) {
      var localAgentId = (config.localAgentId || "").split(":").pop();
      for (var soulPath of [
        path.join(process.env.HOME || "~", ".openclaw", "workspace", localAgentId, "SOUL.md"),
        path.join(process.env.HOME || "~", ".openclaw", "workspace-" + localAgentId, "SOUL.md"),
        path.join(process.env.HOME || "~", ".openclaw", "workspace", "SOUL.md"),
      ]) {
        try {
          if (fs.existsSync(soulPath)) {
            var firstLine = fs.readFileSync(soulPath, "utf8").split("\n")[0].trim();
            // 提取 "# SOUL.md — Title" 或 "# SOUL.md - Title" 中的 Title 部分
            var titleMatch = firstLine.match(/^#\s*SOUL\.md\s*[—\-]\s*(.+)/);
            cliTitle = titleMatch ? titleMatch[1].trim() : firstLine.replace(/^#\s*/, "").trim();
            break;
          }
        } catch {}
      }
    }
    if (!cliTitle) {
      cliTitle = localAgentId || "OpenClaw Agent";
    }
    if (!cliSummary) {
      cliSummary = "Auto-published via openclaw aimoo CLI";
    }
    var timeoutMs = config.httpTimeoutMs || 15000;

    // 检查是否已有服务（避免重复创建）
    var existingService = null;
    try {
      var listResp = await requestJson(baseUrl + "/v1/services?limit=50", {
        headers: headers,
        timeoutMs: 8000,
      });
      var existingServices = (listResp.data || listResp || []);
      if (Array.isArray(existingServices)) {
        for (var svc of existingServices) {
          if (svc.handler_agent_id === platformAgentId) {
            existingService = svc;
            break;
          }
        }
      }
    } catch (listErr) {}

    if (existingService) {
      if (existingService.status === "ACTIVE") {
        console.log(JSON.stringify({ service_id: existingService.service_id, title: existingService.title, status: existingService.status, message: "Service already exists and is ACTIVE" }, null, 2));
        return;
      }
      // INACTIVE 状态，重新激活
      try {
        var reactivateResp = await requestJson(baseUrl + "/v1/services/" + existingService.service_id, {
          method: "PATCH",
          headers: headers,
          body: JSON.stringify({ status: "ACTIVE", title: cliTitle, summary: cliSummary, visibility: cliVisibility }),
          timeoutMs: timeoutMs,
        });
        console.log(JSON.stringify(reactivateResp.data || reactivateResp, null, 2));
        return;
      } catch (reactivateErr) {}
    }

    var response = await requestJson(baseUrl + "/v1/services", {
      method: "POST",
      headers: headers,
      body: JSON.stringify({
        handler_agent_id: platformAgentId,
        title: cliTitle,
        summary: cliSummary,
        visibility: cliVisibility,
      }),
      timeoutMs: timeoutMs,
    });
    var resultData = response.data || response;
    console.log(JSON.stringify(resultData, null, 2));
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
    if (command === "services") {
      var subCmd = process.argv[3];
      // services info <id>
      if (subCmd === "info") {
        var infoId = process.argv[4];
        if (!infoId) throw new Error("service_id is required for services info");
        return requestJson(ctx.baseUrl + "/v1/services/" + encodeURIComponent(infoId), {
          headers: headers, timeoutMs: timeoutMs,
        }).then(function(response) { return { service: response.data || response }; });
      }
      // services update <id> --title "..." --summary "..."
      if (subCmd === "update") {
        var updateId = process.argv[4];
        if (!updateId) throw new Error("service_id is required for services update");
        var updateBody = {};
        for (var si = 5; si < process.argv.length; si++) {
          if (process.argv[si] === "--title" && si + 1 < process.argv.length) updateBody.title = process.argv[++si];
          if (process.argv[si] === "--summary" && si + 1 < process.argv.length) updateBody.summary = process.argv[++si];
        }
        if (!updateBody.title && !updateBody.summary) throw new Error("at least --title or --summary is required for update");
        return requestJson(ctx.baseUrl + "/v1/services/" + encodeURIComponent(updateId), {
          method: "PATCH", headers: headers,
          body: JSON.stringify(updateBody), timeoutMs: timeoutMs,
        }).then(function(response) { return { service: response.data || response }; });
      }
      // services delete <id>
      if (subCmd === "delete") {
        var deleteId = process.argv[4];
        if (!deleteId) throw new Error("service_id is required for services delete");
        return requestJson(ctx.baseUrl + "/v1/services/" + encodeURIComponent(deleteId), {
          method: "DELETE", headers: headers, timeoutMs: timeoutMs,
        }).then(function(response) { return response.data || response; });
      }
      // services register [--title "..."] [--summary "..."] [--visibility listed|private|direct_link]
      if (subCmd === "register") {
        var regTitle = "";
        var regSummary = "";
        var regVisibility = "listed";
        for (var ri = 4; ri < process.argv.length; ri++) {
          if (process.argv[ri] === "--title" && ri + 1 < process.argv.length) regTitle = process.argv[++ri];
          else if (process.argv[ri] === "--summary" && ri + 1 < process.argv.length) regSummary = process.argv[++ri];
          else if (process.argv[ri] === "--visibility" && ri + 1 < process.argv.length) regVisibility = process.argv[++ri];
        }
        // 从 SOUL.md 提取默认标题和摘要
        var localAgentId = String(config.localAgentId || "").split(":").pop();
        if (!regTitle || !regSummary) {
          var soulPaths = [
            path.join(process.env.HOME || "~", ".openclaw", "workspace", localAgentId, "SOUL.md"),
            path.join(process.env.HOME || "~", ".openclaw", "workspace-" + localAgentId, "SOUL.md"),
          ];
          for (var sp of soulPaths) {
            try {
              if (fs.existsSync(sp)) {
                var soulContent = fs.readFileSync(sp, "utf8");
                var soulLines = soulContent.split("\n");
                if (!regTitle) {
                  var firstLine = soulLines[0].trim();
                  var titleMatch = firstLine.match(/^#\s*SOUL\.md\s*[—\-]\s*(.+)/);
                  regTitle = titleMatch ? titleMatch[1].trim() : firstLine.replace(/^#\s*/, "").trim();
                }
                if (!regSummary) {
                  var inSection = false;
                  var sumLines = [];
                  for (var sli = 1; sli < soulLines.length && sumLines.length < 3; sli++) {
                    var sl = soulLines[sli].trim();
                    if (/^##\s*(你是谁|基本身份|简介|概述|About)/i.test(sl)) { inSection = true; continue; }
                    if (/^##\s/.test(sl) && inSection) break;
                    if (inSection && sl && !sl.startsWith("#")) sumLines.push(sl);
                  }
                  regSummary = sumLines.join(" ").substring(0, 200);
                }
                break;
              }
            } catch {}
          }
        }
        if (!regTitle) regTitle = localAgentId || "OpenClaw Agent";
        if (!regSummary) regSummary = "Registered via openclaw aimoo CLI";

        // 检查是否已有服务
        var existingSvc = null;
        try {
          var listR = await requestJson(ctx.baseUrl + "/v1/services?limit=50", { headers: headers, timeoutMs: 8000 });
          var svcList = (listR.data || listR || []);
          if (Array.isArray(svcList)) {
            for (var s of svcList) {
              if (s.handler_agent_id === platformAgentId) { existingSvc = s; break; }
            }
          }
        } catch {}

        if (existingSvc) {
          if (existingSvc.status === "ACTIVE") {
            return { registered: "skipped", reason: "service already ACTIVE", service_id: existingSvc.service_id, title: existingSvc.title };
          }
          // 重新激活
          try {
            var reactivate = await requestJson(ctx.baseUrl + "/v1/services/" + existingSvc.service_id, {
              method: "PATCH", headers: headers,
              body: JSON.stringify({ status: "ACTIVE", title: regTitle, summary: regSummary, visibility: regVisibility }),
              timeoutMs: timeoutMs,
            });
            return { registered: "reactivated", service: reactivate.data || reactivate };
          } catch {}
        }

        return requestJson(ctx.baseUrl + "/v1/services", {
          method: "POST", headers: headers,
          body: JSON.stringify({
            handler_agent_id: platformAgentId,
            title: regTitle,
            summary: regSummary,
            visibility: regVisibility,
          }),
          timeoutMs: timeoutMs,
        }).then(function(response) { return { registered: "created", service: response.data || response }; });
      }
      // services list (with search/pagination)
      var queryParams = [];
      for (var si = 3; si < process.argv.length; si++) {
        if (process.argv[si] === "--keyword" && si + 1 < process.argv.length) queryParams.push("keyword=" + encodeURIComponent(process.argv[++si]));
        if (process.argv[si] === "--offset" && si + 1 < process.argv.length) queryParams.push("offset=" + process.argv[++si]);
        if (process.argv[si] === "--limit" && si + 1 < process.argv.length) queryParams.push("limit=" + process.argv[++si]);
      }
      var qs = queryParams.length ? "?" + queryParams.join("&") : "";
      return requestJson(ctx.baseUrl + "/v1/services" + qs, {
        headers: headers, timeoutMs: timeoutMs,
      }).then(function(response) {
        var services = (response.data || []).map(function(svc) {
          return { service_id: svc.service_id, title: svc.title, summary: svc.summary, status: svc.status, visibility: svc.visibility, handler_agent_id: svc.handler_agent_id, tenant_id: svc.tenant_id };
        });
        return { services: services, count: services.length };
      });
    }
    if (command === "chat") {
      var serviceId = process.argv[3];
      var chatText = process.argv[4] || "";
      var threadId = null;
      var forceNew = false;
      // 解析 --thread <id> 和 --new
      for (var ci = 5; ci < process.argv.length; ci++) {
        if (process.argv[ci] === "--thread" && ci + 1 < process.argv.length) {
          threadId = process.argv[++ci];
        } else if (process.argv[ci] === "--new") {
          forceNew = true;
        } else if (process.argv[ci - 1] !== "--thread") {
          chatText += (chatText ? " " : "") + process.argv[ci];
        }
      }
      if (!serviceId) throw new Error("service_id is required");
      if (!chatText) throw new Error("message is required");

      // thread 记忆文件
      var threadsFile = path.join(controlDir(config), "chat-threads.json");
      function loadThreads() {
        try { return JSON.parse(fs.readFileSync(threadsFile, "utf8")); } catch { return {}; }
      }
      function saveThread(svcId, tid, title) {
        try {
          var data = loadThreads();
          data[svcId] = { thread_id: tid, service_title: title || svcId, updated_at: new Date().toISOString() };
          fs.writeFileSync(threadsFile, JSON.stringify(data, null, 2) + "\n", "utf8");
        } catch {}
      }

      // 确定 thread_id：--thread > --new > 记忆 > 新建
      var existingThreadId = threadId;
      if (!existingThreadId && !forceNew) {
        var mem = loadThreads();
        if (mem[serviceId] && mem[serviceId].thread_id) {
          existingThreadId = mem[serviceId].thread_id;
        }
      }

      function pollAssistant(tid) {
        var maxPolls = 30;
        var pollInterval = 2000;
        var pollCount = 0;
        function poll() {
          return requestJson(ctx.baseUrl + "/v1/service-threads/" + encodeURIComponent(tid) + "/messages", {
            headers: headers, timeoutMs: timeoutMs,
          }).then(function(msgResp) {
            var messages = msgResp.data || [];
            var lastMsg = messages.length > 0 ? messages[messages.length - 1] : null;
            if (lastMsg && lastMsg.role === "assistant" && lastMsg.content_text && lastMsg.content_text.trim()) {
              return { thread_id: tid, messages: messages.map(function(m) { return { role: m.role, content: m.content_text }; }) };
            }
            pollCount++;
            if (pollCount >= maxPolls) {
              return { thread_id: tid, messages: messages.map(function(m) { return { role: m.role, content: m.content_text }; }), warning: "timeout after " + maxPolls + " polls" };
            }
            return new Promise(function(resolve) { setTimeout(function() { resolve(poll()); }, pollInterval); });
          });
        }
        return poll();
      }

      if (existingThreadId) {
        // 继续已有 thread
        return requestJson(ctx.baseUrl + "/v1/service-threads/" + encodeURIComponent(existingThreadId) + "/messages", {
          method: "POST", headers: headers,
          body: JSON.stringify({ text: chatText }),
          timeoutMs: timeoutMs,
        }).then(function() {
          saveThread(serviceId, existingThreadId, "");
          return pollAssistant(existingThreadId);
        });
      }
      // 创建新 thread
      return requestJson(ctx.baseUrl + "/v1/services/" + encodeURIComponent(serviceId) + "/threads", {
        method: "POST", headers: headers,
        body: JSON.stringify({ opening_message: chatText }),
        timeoutMs: timeoutMs,
      }).then(function(resp) {
        var thread = resp.data && resp.data.thread;
        var newThreadId = thread && thread.thread_id;
        if (!newThreadId) throw new Error("thread creation failed: no thread_id returned");
        saveThread(serviceId, newThreadId, thread.title || "");
        return pollAssistant(newThreadId);
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
