"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { resolvePluginInstances } = require("./config");

const CLI_BRIDGE_PATH = path.resolve(__dirname, "cli-bridge.js");

function normalizeAgentId(value) {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.split(":").pop();
}

function collectCliTargets(api) {
  return resolvePluginInstances(api)
    .filter((item) => item && item.enabled !== false)
    .map((item) => ({
      localAgentId: normalizeAgentId(item.localAgentId || item.agentId),
      platformAgentId: typeof item.agentId === "string" ? item.agentId.trim() : "",
      config: item,
    }));
}

function selectCliTarget(targets, agentOption) {
  if (!targets.length) {
    throw new Error("aimoo CLI unavailable: no enabled aimoo instance found");
  }
  const requested = normalizeAgentId(agentOption);
  if (!requested) {
    if (targets.length === 1) return targets[0];
    const available = targets.map((item) => item.localAgentId || item.platformAgentId).filter(Boolean).join(", ");
    throw new Error(`multiple aimoo agents configured; pass --agent <local-agent-id>. available: ${available}`);
  }
  const target = targets.find(
    (item) => requested === normalizeAgentId(item.localAgentId) || requested === normalizeAgentId(item.platformAgentId),
  );
  if (!target) {
    const available = targets.map((item) => item.localAgentId || item.platformAgentId).filter(Boolean).join(", ");
    throw new Error(`unknown aimoo agent "${requested}". available: ${available}`);
  }
  return target;
}

function ensureHelperExists(target) {
  const userProfileFile = typeof target?.config?.userProfileFile === "string" ? target.config.userProfileFile : "";
  if (!userProfileFile) throw new Error("aimoo Link userProfileFile is unavailable");
  if (!fs.existsSync(userProfileFile)) throw new Error(`aimoo Link USER.md not found: ${userProfileFile}`);
}

function buildRunnerEnv(target) {
  const userProfileFile = target.config.userProfileFile;
  const workspaceDir = path.dirname(userProfileFile);
  const openclawHome = path.resolve(workspaceDir, "..", "..");
  return { ...process.env, OPENCLAW_HOME: process.env.OPENCLAW_HOME || openclawHome };
}

function runCommand(target, argv) {
  ensureHelperExists(target);
  const configJson = JSON.stringify({
    connectUrl: target.config.connectUrl,
    agentId: target.platformAgentId || target.config.agentId,
    localAgentId: target.localAgentId,
    userProfileFile: target.config.userProfileFile,
    stateFile: target.config.stateFile,
    runtimeIdentityKey: target.config.runtimeIdentityKey,
    runtimeIdentityKeyFile: target.config.runtimeIdentityKeyFile,
    httpTimeoutMs: target.config.httpTimeoutMs || 15000,
  });
  execFileSync(process.execPath, [CLI_BRIDGE_PATH, ...argv], {
    stdio: "inherit",
    env: { ...buildRunnerEnv(target), AIMOO_LINK_CLI_CONFIG_JSON: configJson },
  });
}

function resolveAgentOption(command) {
  let current = command;
  while (current) {
    if (typeof current.opts === "function") {
      const opts = current.opts();
      if (opts && typeof opts.agent === "string" && opts.agent.trim()) return opts.agent.trim();
    }
    current = current.parent;
  }
  return "";
}

/** 从 options/parent chain 解析 agent 目标，失败时 exit */
function resolveTarget(api, options, command) {
  const agentOpt = (options && options.agent) || resolveAgentOption(command);
  const targets = collectCliTargets(api);
  const target = agentOpt
    ? targets.find(t => normalizeAgentId(t.localAgentId) === normalizeAgentId(agentOpt))
    : (targets.length === 1 ? targets[0] : null);
  if (!target) {
    if (targets.length > 1) {
      const available = targets.map(t => t.localAgentId).filter(Boolean).join(", ");
      console.error(`multiple aimoo agents configured; pass --agent <local-agent-id>. available: ${available}`);
    } else {
      console.error("no enabled aimoo instance found");
    }
    process.exit(1);
  }
  return target;
}

function executeCli(api, command, argv) {
  const target = selectCliTarget(collectCliTargets(api), resolveAgentOption(command));
  runCommand(target, argv);
}

/** 调用 bridge 全局命令（不传 agent config，bridge 自动走全局模式） */
function runGlobalBridge(name, extraArgs) {
  try {
    execFileSync(process.execPath, [CLI_BRIDGE_PATH, name, ...(extraArgs || [])], {
      stdio: "inherit",
      env: { ...process.env, OPENCLAW_HOME: process.env.OPENCLAW_HOME || path.join(process.env.HOME || "", ".openclaw") },
    });
  } catch (err) {
    process.exit(err.status || 1);
  }
}

function registerAimooCli(program, api) {
  const root = program
    .command("aimoo")
    .description("A2A Hub Agent Link CLI for the aimoo-link plugin")
    .option("--agent <localAgentId>", "Local OpenClaw agent id")
    .showHelpAfterError();

  // ─── 需要 agent 的简单命令 ────────────────────────────────
  const simpleCommand = (name, description) => {
    root.command(name).description(description).action((...args) => {
      executeCli(api, args[args.length - 1], [name]);
    });
  };
  simpleCommand("me", "Show agent_id, tenant_id, and invite_url");
  simpleCommand("urls", "Show public Agent Link URLs");
  simpleCommand("invite", "Show your invite_url");
  simpleCommand("friends", "List current agent friends");
  simpleCommand("remove", "Remove this agent from A2A Hub and clean up local config");
  simpleCommand("publish-service", "Publish this agent as a service on A2A Hub");

  // ─── 智能命令：无 --agent 时全局展示，有 --agent 时单个 ───
  // bridge 侧通过有无 AIMOO_LINK_CLI_CONFIG_JSON 自动判断模式
  const smartCommand = (name, description) => {
    root.command(name).description(description).option("--agent <localAgentId>", "Local OpenClaw agent id")
      .action((options, command) => {
        if (options.agent || resolveAgentOption(command)) {
          executeCli(api, command, [name]);
        } else {
          runGlobalBridge(name);
        }
      });
  };
  smartCommand("status", "Show agent status (all agents if no --agent, specific agent otherwise)");
  smartCommand("doctor", "Run Hub diagnostics (all agents if no --agent, specific agent otherwise)");

  // ─── 全局命令 ─────────────────────────────────────────────
  root.command("list").description("List all agents and their status (installed/pending/online)")
    .option("--pending", "Show only pending (not installed) agents")
    .option("--online", "Show only online agents")
    .action((options) => {
      const argv = [];
      if (options.pending) argv.push("--pending");
      if (options.online) argv.push("--online");
      runGlobalBridge("list", argv);
    });

  root.command("repair").description("Check and repair agent configurations")
    .option("--agent <localAgentId>", "Repair specific agent only")
    .option("--fix", "Auto-fix repairable issues")
    .action((options, command) => {
      const argv = ["repair"];
      if (options.fix) argv.push("--fix");
      if (options.agent || resolveAgentOption(command)) {
        executeCli(api, command, argv);
      } else {
        runGlobalBridge(argv[0], argv.slice(1));
      }
    });

  root.command("update").description("Update aimoo-link plugin to latest version")
    .option("--url <url>", "Plugin download URL (default: from Hub)")
    .action((options) => {
      const argv = ["update"];
      if (options.url) argv.push("--url", options.url);
      runGlobalBridge(argv[0], argv.slice(1));
    });

  // ─── 服务命令（需要 --agent 认证）─────────────────────────
  root.command("services").description("List, search, or inspect services on A2A Hub")
    .option("--agent <localAgentId>", "Local OpenClaw agent id")
    .option("--keyword <keyword>", "Search services by keyword")
    .option("--offset <n>", "Pagination offset", "0")
    .option("--limit <n>", "Results per page (max 100)", "20")
    .argument("[action]", "'info' or 'update' (omit to list)")
    .argument("[serviceId]", "Service ID (required for info/update)")
    .option("--title <title>", "New title (for update)")
    .option("--summary <summary>", "New summary (for update)")
    .action((action, serviceId, options, command) => {
      const target = resolveTarget(api, options, command);
      const argv = ["services"];
      if (action === "info" && serviceId) {
        argv.push("info", serviceId);
      } else if (action === "update" && serviceId) {
        argv.push("update", serviceId);
        if (options.title) argv.push("--title", options.title);
        if (options.summary) argv.push("--summary", options.summary);
      } else if (action && !serviceId) {
        // action is actually a serviceId for backward compat: services <id>
        argv.push("info", action);
      }
      if (options.keyword) argv.push("--keyword", options.keyword);
      if (options.offset && options.offset !== "0") argv.push("--offset", options.offset);
      if (options.limit && options.limit !== "20") argv.push("--limit", options.limit);
      runCommand(target, argv);
    });

  root.command("chat").description("Chat with a service agent (remembers last thread automatically)")
    .option("--agent <localAgentId>", "Local OpenClaw agent id (required)")
    .option("--thread <threadId>", "Continue a specific thread")
    .option("--new", "Force start a new thread")
    .argument("<serviceId>", "Service ID to chat with")
    .argument("<message...>", "Message to send")
    .action((serviceId, messageParts, options, command) => {
      const message = Array.isArray(messageParts) ? messageParts.join(" ") : messageParts;
      const target = resolveTarget(api, options, command);
      const argv = ["chat", serviceId, message];
      if (options.thread) argv.push("--thread", options.thread);
      if (options.new) argv.push("--new");
      runCommand(target, argv);
    });

  // ─── 复杂命令 ─────────────────────────────────────────────
  root.command("setup").description("Configure agent in openclaw.json and restart Gateway")
    .argument("[agentId]", "Agent ID to configure (optional, uses --agent if not provided)")
    .option("--connect-url <url>", "Hub connect URL")
    .option("--restart", "Restart Gateway after configuration")
    .option("--wait", "Wait for agent to come online")
    .option("--auto-publish-service", "Auto-publish service if SOUL.md contains service keywords")
    .action((agentId, options, command) => {
      const argv = ["setup"];
      const resolvedAgentId = agentId || resolveAgentOption(command);
      if (resolvedAgentId) argv.push(resolvedAgentId);
      if (options.connectUrl) argv.push("--connect-url", options.connectUrl);
      if (options.restart) argv.push("--restart");
      if (options.wait) argv.push("--wait");
      if (options.autoPublishService) argv.push("--auto-publish-service");
      const configJson = JSON.stringify({
        connectUrl: options.connectUrl || "", localAgentId: resolvedAgentId || "",
        agentId: "", userProfileFile: "", stateFile: "", httpTimeoutMs: 15000,
      });
      try {
        execFileSync(process.execPath, [CLI_BRIDGE_PATH, ...argv], {
          stdio: "inherit",
          env: { ...process.env, OPENCLAW_HOME: process.env.OPENCLAW_HOME || path.join(process.env.HOME || "", ".openclaw"), AIMOO_LINK_CLI_CONFIG_JSON: configJson },
        });
      } catch (err) { process.exit(err.status || 1); }
    });

  root.command("request").description("Create a friend request to another agent")
    .argument("<targetAgentId>").argument("[message...]", "Optional friend request message")
    .action((targetAgentId, messageParts, command) => {
      const message = Array.isArray(messageParts) && messageParts.length ? messageParts : [];
      executeCli(api, command, ["request", targetAgentId, ...message]);
    });

  root.command("accept-request").description("Accept a pending friend request")
    .argument("<friendId>").action((friendId, command) => executeCli(api, command, ["accept-request", friendId]));

  root.command("update-request").description("Update a pending friend request to accepted, rejected, or blocked")
    .argument("<friendId>").argument("<status>")
    .action((friendId, status, command) => executeCli(api, command, ["update-request", friendId, status]));

  root.command("accept").description("Accept an invite URL or token")
    .argument("<inviteUrlOrToken>").action((inviteUrlOrToken, command) => executeCli(api, command, ["accept", inviteUrlOrToken]));

  root.command("send").description("Send a message to an accepted agent friend")
    .option("--context <contextId>", "Continue an existing friend context")
    .argument("<targetAgentId>").argument("<message...>")
    .action((targetAgentId, messageParts, options, command) => {
      const argv = ["send"];
      if (options && typeof options.context === "string" && options.context.trim()) argv.push("--context", options.context.trim());
      argv.push(targetAgentId, ...(Array.isArray(messageParts) ? messageParts : [messageParts]));
      executeCli(api, command, argv);
    });
}

function createAimooCli(api) {
  return ({ program }) => registerAimooCli(program, api);
}

module.exports = { collectCliTargets, createAimooCli, normalizeAgentId, registerAimooCli, selectCliTarget };
