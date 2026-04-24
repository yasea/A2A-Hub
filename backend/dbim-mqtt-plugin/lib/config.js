"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function expandHome(input) {
  if (typeof input !== "string") return input;
  if (input === "~") return os.homedir();
  if (input.startsWith("~/")) return path.join(os.homedir(), input.slice(2));
  return input;
}

function fromChannelsConfig(api) {
  const root = api?.config && typeof api.config === "object" ? api.config : {};
  if (
    Array.isArray(root.instances)
    || typeof root.agentId === "string"
    || typeof root.connectUrl === "string"
    || typeof root.connectUrlFile === "string"
    || typeof root.userProfileFile === "string"
    || typeof root.stateFile === "string"
    || typeof root.replyMode === "string"
    || typeof root.enabled === "boolean"
    || typeof root.writeWorkspaceTools === "boolean"
  ) {
    return root;
  }
  const channels = root.channels && typeof root.channels === "object" ? root.channels : {};
  const dbim = channels.dbim_mqtt && typeof channels.dbim_mqtt === "object" ? channels.dbim_mqtt : {};
  return dbim;
}

function normalizeMetadata(input, agentId, localAgentId = agentId) {
  const metadata = input && typeof input === "object" && !Array.isArray(input) ? input : {};
  return {
    plugin: "dbim-mqtt",
    mode: "channel",
    localAgentId,
    agentId,
    ...metadata,
  };
}

function workspaceDirCandidates(shortId) {
  if (shortId === "main") {
    return [
      path.join("~/.openclaw", "workspace"),
      path.join("~/.openclaw", "workspace-main"),
    ];
  }
  return [
    path.join("~/.openclaw", "workspace", shortId),
    path.join("~/.openclaw", `workspace-${shortId}`),
  ];
}

function resolveWorkspaceDir(shortId) {
  const candidates = workspaceDirCandidates(shortId);
  for (const candidate of candidates) {
    const expanded = expandHome(candidate);
    if (typeof expanded === "string" && fs.existsSync(expanded)) return candidate;
  }
  return candidates[0];
}

function normalizeAgentId(value) {
  if (typeof value !== "string") return "";
  const trimmed = value.trim();
  if (!trimmed) return "";
  return trimmed.split(":").pop();
}

function workspaceAgentHint(file) {
  const expanded = expandHome(file);
  if (typeof expanded !== "string" || !expanded) return "";
  const normalized = expanded.replace(/\\/g, "/");
  const match = normalized.match(/\/workspace\/([^/]+)\/(?:USER|SOUL)\.md$/i);
  if (match) return normalizeAgentId(match[1]);
  if (/\/workspace\/(?:USER|SOUL)\.md$/i.test(normalized)) return "main";
  const legacyMatch = normalized.match(/\/workspace-([^/]+)\/(?:USER|SOUL)\.md$/i);
  if (legacyMatch) return normalizeAgentId(legacyMatch[1]);
  if (/\/workspace-main\/(?:USER|SOUL)\.md$/i.test(normalized)) return "main";
  return "";
}

function extractAgentHintFromText(text) {
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

function readFileAgentHint(file) {
  const expanded = expandHome(file);
  if (typeof expanded !== "string" || !expanded || !fs.existsSync(expanded)) return "";
  try {
    return extractAgentHintFromText(fs.readFileSync(expanded, "utf8"));
  } catch {
    return "";
  }
}

function detectAgentHintsFromFiles(userProfileFile) {
  const hints = [];
  const pushHint = (value) => {
    const normalized = normalizeAgentId(value);
    if (normalized && !hints.includes(normalized)) hints.push(normalized);
  };
  pushHint(workspaceAgentHint(userProfileFile));
  pushHint(readFileAgentHint(userProfileFile));
  const expanded = expandHome(userProfileFile);
  if (typeof expanded === "string" && expanded) {
    pushHint(readFileAgentHint(path.join(path.dirname(expanded), "SOUL.md")));
  }
  return hints;
}

function configuredAgentIds(api) {
  const root = api?.config && typeof api.config === "object" ? api.config : {};
  const agents = root.agents && typeof root.agents === "object" ? root.agents : {};
  const rawList = Array.isArray(agents.list) ? agents.list : [];
  const values = [];
  for (const item of rawList) {
    const candidate = normalizeAgentId(typeof item === "string" ? item : item && item.id);
    if (candidate && !values.includes(candidate)) values.push(candidate);
  }
  return values;
}

function inferAgentId(api, merged, fallbackAgentId) {
  const explicitAgentId = normalizeAgentId(merged.agentId || merged.localAgentId || fallbackAgentId);
  const fileHints = detectAgentHintsFromFiles(merged.userProfileFile);
  const configAgents = configuredAgentIds(api);
  const nonMainConfigAgents = configAgents.filter((item) => item !== "main");

  if (explicitAgentId && explicitAgentId !== "main") return explicitAgentId;
  for (const hint of fileHints) {
    if (hint !== "main") return hint;
  }
  if (explicitAgentId === "main" && nonMainConfigAgents.length === 1) return nonMainConfigAgents[0];
  if (!explicitAgentId && nonMainConfigAgents.length === 1) return nonMainConfigAgents[0];
  if (!explicitAgentId && configAgents.length === 1) return configAgents[0];
  return explicitAgentId || normalizeAgentId(fallbackAgentId) || "ava";
}

function resolveSingleConfig(merged, defaultAgentId = "ava") {
  const explicitReplyMode =
    typeof merged.replyMode === "string" && merged.replyMode.trim()
      ? merged.replyMode.trim()
      : "";
  const agentId = typeof merged.agentId === "string" && merged.agentId.trim() ? merged.agentId.trim() : defaultAgentId;
  const shortId = normalizeAgentId(agentId) || normalizeAgentId(defaultAgentId) || "ava";
  const workspaceDir = resolveWorkspaceDir(shortId);
  return {
    enabled: merged.enabled !== false,
    agentId,
    connectUrl: typeof merged.connectUrl === "string" && merged.connectUrl.trim() ? merged.connectUrl.trim() : "",
    userProfileFile: expandHome(
      typeof merged.userProfileFile === "string" && merged.userProfileFile.trim()
        ? merged.userProfileFile.trim()
        : path.join(workspaceDir, "USER.md"),
    ),
    connectUrlFile: expandHome(
      typeof merged.connectUrlFile === "string" && merged.connectUrlFile.trim()
        ? merged.connectUrlFile.trim()
        : path.join("~/.openclaw", "channels", "dbim_mqtt", shortId, "connect-url.txt"),
    ),
    stateFile: expandHome(
      typeof merged.stateFile === "string" && merged.stateFile.trim()
        ? merged.stateFile.trim()
        : path.join("~/.openclaw", "channels", "dbim_mqtt", shortId, "state.json"),
    ),
    presenceIntervalSec:
      Number.isInteger(merged.presenceIntervalSec) && merged.presenceIntervalSec > 0
        ? merged.presenceIntervalSec
        : 30,
    replyMode:
      explicitReplyMode === "handler" || explicitReplyMode === "echo" || explicitReplyMode === "openclaw-agent"
        ? explicitReplyMode
        : typeof merged.handlerCommand === "string" && merged.handlerCommand.trim()
          ? "handler"
          : "openclaw-agent",
    openClawCommand:
      typeof merged.openClawCommand === "string" && merged.openClawCommand.trim()
        ? merged.openClawCommand.trim()
        : "openclaw",
    openClawTimeoutSec:
      Number.isInteger(merged.openClawTimeoutSec) && merged.openClawTimeoutSec > 0
        ? merged.openClawTimeoutSec
        : 180,
    handlerCommand:
      typeof merged.handlerCommand === "string" && merged.handlerCommand.trim()
        ? merged.handlerCommand.trim()
        : "",
    metadata:
      merged.metadata && typeof merged.metadata === "object" && !Array.isArray(merged.metadata)
        ? merged.metadata
        : { plugin: "dbim-mqtt", mode: "channel" },
    httpTimeoutMs:
      Number.isInteger(merged.httpTimeoutMs) && merged.httpTimeoutMs > 0
        ? merged.httpTimeoutMs
        : 15000,
    bootstrapRetryIntervalSec:
      Number.isInteger(merged.bootstrapRetryIntervalSec) && merged.bootstrapRetryIntervalSec > 0
        ? merged.bootstrapRetryIntervalSec
        : 30,
    tlsRejectUnauthorized:
      typeof merged.tlsRejectUnauthorized === "boolean"
        ? merged.tlsRejectUnauthorized
        : true,
    recordOpenClawSession:
      typeof merged.recordOpenClawSession === "boolean"
        ? merged.recordOpenClawSession
        : true,
    writeWorkspaceTools:
      typeof merged.writeWorkspaceTools === "boolean"
        ? merged.writeWorkspaceTools
        : false,
    instanceId: shortId,
  };
}

function resolvePluginInstances(api) {
  const channelCfg = fromChannelsConfig(api);
  const pluginCfg = api?.pluginConfig && typeof api.pluginConfig === "object" ? api.pluginConfig : {};
  const merged = { ...pluginCfg, ...channelCfg };
  const instanceBase = { ...merged };
  delete instanceBase.instances;

  const rawInstances = Array.isArray(merged.instances) ? merged.instances : [];
  if (!rawInstances.length) {
    const inferredAgentId = inferAgentId(api, instanceBase, "ava");
    const single = resolveSingleConfig(
      {
        ...instanceBase,
        agentId: inferredAgentId,
        localAgentId: inferredAgentId,
      },
      inferredAgentId || "ava",
    );
    single.localAgentId = normalizeAgentId(single.agentId);
    single.metadata = normalizeMetadata(single.metadata, single.agentId, single.localAgentId);
    return [single];
  }

  return rawInstances.map((item, index) => {
    const instanceCfg = item && typeof item === "object" ? item : {};
    const fallbackAgentId = typeof instanceCfg.localAgentId === "string" && instanceCfg.localAgentId.trim()
      ? instanceCfg.localAgentId.trim()
      : `agent${index + 1}`;
    const inferredAgentId = inferAgentId(
      api,
      {
        ...instanceBase,
        ...instanceCfg,
      },
      fallbackAgentId,
    );
    const resolved = resolveSingleConfig(
      {
        ...instanceBase,
        ...instanceCfg,
        agentId: instanceCfg.agentId || instanceCfg.localAgentId || instanceBase.agentId || inferredAgentId,
        localAgentId: instanceCfg.localAgentId || inferredAgentId,
        metadata: {
          ...(instanceBase.metadata && typeof instanceBase.metadata === "object" ? instanceBase.metadata : {}),
          ...(instanceCfg.metadata && typeof instanceCfg.metadata === "object" ? instanceCfg.metadata : {}),
        },
      },
      inferredAgentId || fallbackAgentId,
    );
    resolved.localAgentId = normalizeAgentId(instanceCfg.localAgentId || inferredAgentId || resolved.agentId);
    resolved.metadata = normalizeMetadata(resolved.metadata, resolved.agentId, resolved.localAgentId);
    return resolved;
  });
}

function resolvePluginConfig(api) {
  return resolvePluginInstances(api)[0];
}

module.exports = {
  expandHome,
  resolvePluginConfig,
  resolvePluginInstances,
  resolveWorkspaceDir,
};
