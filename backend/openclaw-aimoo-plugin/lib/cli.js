"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const { resolvePluginInstances } = require("./config");
const { helperSource } = require("./agent-link-core/local-control");

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
  if (!userProfileFile) {
    throw new Error("aimoo Link userProfileFile is unavailable");
  }
  if (!fs.existsSync(userProfileFile)) {
    throw new Error(`aimoo Link USER.md not found: ${userProfileFile}`);
  }
}

function buildRunnerEnv(target) {
  const userProfileFile = target.config.userProfileFile;
  const workspaceDir = path.dirname(userProfileFile);
  const openclawHome = path.resolve(workspaceDir, "..", "..");
  return {
    ...process.env,
    OPENCLAW_HOME: process.env.OPENCLAW_HOME || openclawHome,
  };
}

function runCommand(target, argv) {
  ensureHelperExists(target);
  execFileSync(process.execPath, ["-e", helperSource(), "_", ...argv], {
    stdio: "inherit",
    env: {
      ...buildRunnerEnv(target),
      AIMOO_LINK_CLI_CONFIG_JSON: JSON.stringify({
        connectUrl: target.config.connectUrl,
        agentId: target.platformAgentId || target.config.agentId,
        localAgentId: target.localAgentId,
        userProfileFile: target.config.userProfileFile,
        stateFile: target.config.stateFile,
        runtimeIdentityKey: target.config.runtimeIdentityKey,
        runtimeIdentityKeyFile: target.config.runtimeIdentityKeyFile,
        httpTimeoutMs: target.config.httpTimeoutMs || 15000,
      }),
    },
  });
}

function resolveAgentOption(command) {
  let current = command;
  while (current) {
    if (typeof current.opts === "function") {
      const opts = current.opts();
      if (opts && typeof opts.agent === "string" && opts.agent.trim()) {
        return opts.agent.trim();
      }
    }
    current = current.parent;
  }
  return "";
}

function executeCli(api, command, argv) {
  const target = selectCliTarget(collectCliTargets(api), resolveAgentOption(command));
  runCommand(target, argv);
}

function registerAimooCli(program, api) {
  const root = program
    .command("aimoo")
    .description("A2A Hub Agent Link CLI for the aimoo-link plugin")
    .option("--agent <localAgentId>", "Local OpenClaw agent id. Required when multiple aimoo instances are configured.")
    .showHelpAfterError();

  const simpleCommand = (name, description) => {
    root
      .command(name)
      .description(description)
      .action((...args) => {
        executeCli(api, args[args.length - 1], [name]);
      });
  };

  simpleCommand("me", "Show agent_id, tenant_id, and invite_url");
  simpleCommand("status", "Show local Agent Link install/runtime status");
  simpleCommand("urls", "Show public Agent Link URLs");
  simpleCommand("doctor", "Run minimal Hub diagnostics");
  simpleCommand("invite", "Show your invite_url");
  simpleCommand("friends", "List current agent friends");

  root
    .command("request")
    .description("Create a friend request to another agent")
    .argument("<targetAgentId>")
    .argument("[message...]", "Optional friend request message")
    .action((targetAgentId, messageParts, command) => {
      const message = Array.isArray(messageParts) && messageParts.length ? messageParts : [];
      executeCli(api, command, ["request", targetAgentId, ...message]);
    });

  root
    .command("accept-request")
    .description("Accept a pending friend request")
    .argument("<friendId>")
    .action((friendId, command) => {
      executeCli(api, command, ["accept-request", friendId]);
    });

  root
    .command("update-request")
    .description("Update a pending friend request to accepted, rejected, or blocked")
    .argument("<friendId>")
    .argument("<status>")
    .action((friendId, status, command) => {
      executeCli(api, command, ["update-request", friendId, status]);
    });

  root
    .command("accept")
    .description("Accept an invite URL or token")
    .argument("<inviteUrlOrToken>")
    .action((inviteUrlOrToken, command) => {
      executeCli(api, command, ["accept", inviteUrlOrToken]);
    });

  root
    .command("send")
    .description("Send a message to an accepted agent friend")
    .option("--context <contextId>", "Continue an existing friend context")
    .argument("<targetAgentId>")
    .argument("<message...>")
    .action((targetAgentId, messageParts, options, command) => {
      const argv = ["send"];
      if (options && typeof options.context === "string" && options.context.trim()) {
        argv.push("--context", options.context.trim());
      }
      argv.push(targetAgentId, ...(Array.isArray(messageParts) ? messageParts : [messageParts]));
      executeCli(api, command, argv);
    });
}

function createAimooCli(api) {
  return ({ program }) => registerAimooCli(program, api);
}

module.exports = {
  collectCliTargets,
  createAimooCli,
  normalizeAgentId,
  registerAimooCli,
  selectCliTarget,
};
