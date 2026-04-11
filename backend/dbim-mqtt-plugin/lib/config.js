"use strict";

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

function resolveSingleConfig(merged, defaultAgentId = "ava") {
  const explicitReplyMode =
    typeof merged.replyMode === "string" && merged.replyMode.trim()
      ? merged.replyMode.trim()
      : "";
  const agentId = typeof merged.agentId === "string" && merged.agentId.trim() ? merged.agentId.trim() : defaultAgentId;
  const shortId = String(agentId).includes(":") ? String(agentId).split(":").pop() : String(agentId);
  return {
    enabled: merged.enabled !== false,
    agentId,
    connectUrl: typeof merged.connectUrl === "string" && merged.connectUrl.trim() ? merged.connectUrl.trim() : "",
    userProfileFile: expandHome(
      typeof merged.userProfileFile === "string" && merged.userProfileFile.trim()
        ? merged.userProfileFile.trim()
        : path.join("~/.openclaw", `workspace-${shortId}`, "USER.md"),
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
    const single = resolveSingleConfig(instanceBase, "ava");
    single.localAgentId = String(single.agentId).split(":").pop();
    single.metadata = normalizeMetadata(single.metadata, single.agentId, single.localAgentId);
    return [single];
  }

  return rawInstances.map((item, index) => {
    const instanceCfg = item && typeof item === "object" ? item : {};
    const fallbackAgentId = typeof instanceCfg.localAgentId === "string" && instanceCfg.localAgentId.trim()
      ? instanceCfg.localAgentId.trim()
      : `agent${index + 1}`;
    const resolved = resolveSingleConfig(
      {
        ...instanceBase,
        ...instanceCfg,
        agentId: instanceCfg.agentId || instanceCfg.localAgentId || instanceBase.agentId || fallbackAgentId,
        metadata: {
          ...(instanceBase.metadata && typeof instanceBase.metadata === "object" ? instanceBase.metadata : {}),
          ...(instanceCfg.metadata && typeof instanceCfg.metadata === "object" ? instanceCfg.metadata : {}),
        },
      },
      fallbackAgentId,
    );
    resolved.localAgentId = String(instanceCfg.localAgentId || resolved.agentId).split(":").pop();
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
};
