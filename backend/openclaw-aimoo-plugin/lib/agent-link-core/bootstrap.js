"use strict";

const fs = require("node:fs");
const { nowIso } = require("./protocol");
const { requestJson } = require("./http-client");
const { readOwnerProfile, readAgentSummary, localAgentId, platformAgentId } = require("../owner-profile");
const { ensureDir } = require("./state-store");

function ensureRuntimeIdentityKey(config) {
  if (typeof config !== "object" || !config) return;
  if (config.runtimeIdentityKey) return;
  const keyFile = config.runtimeIdentityKeyFile;
  if (!keyFile) return;
  try {
    const existing = fs.readFileSync(keyFile, "utf8").trim();
    if (existing) {
      config.runtimeIdentityKey = existing;
      return;
    }
  } catch {}
  const { randomUUID } = require("node:crypto");
  const newKey = randomUUID().replace(/-/g, "");
  try {
    ensureDir(keyFile);
    fs.writeFileSync(keyFile, newKey, "utf8");
    config.runtimeIdentityKey = newKey;
  } catch {
    // Non-fatal: backend will reject if identity_key is missing
  }
}

function normalizeBootstrap(connectUrl, baseUrl, data) {
  return {
    connectUrl,
    baseUrl,
    authToken: data.auth_token,
    agentId: data.agent_id,
    tenantId: data.tenant_id,
    mqttBrokerUrl: data.mqtt_broker_url,
    mqttClientId: data.mqtt_client_id,
    mqttCommandTopic: data.mqtt_command_topic,
    mqttUsername: data.mqtt_username,
    mqttPassword: data.mqtt_password,
    presenceUrl: data.presence_url,
    agentMessageUrl: `${baseUrl}/v1/agent-link/messages`,
    publicNumber: data.public_number || null,
    qos: data.qos || 1,
  };
}

async function selfRegister(connectUrl, baseUrl, config) {
  ensureRuntimeIdentityKey(config);
  const ownerProfile = readOwnerProfile(config);
  const agentSummary = readAgentSummary(config);
  const localId = localAgentId(config);
  const agentId = platformAgentId(config.agentId || localId);
  const resp = await requestJson(`${baseUrl}/v1/agent-link/self-register`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({
      agent_id: agentId,
      display_name: String(localId).toUpperCase(),
      capabilities: {
        analysis: true,
        generic: true,
      },
      agent_summary: agentSummary,
      config_json: {
        workspace: localId,
        local_agent_id: localId,
        plugin: "aimoo-link",
        agent_summary: agentSummary,
        runtime_identity_key: config.runtimeIdentityKey || "",
      },
      owner_profile: ownerProfile,
    }),
    timeoutMs: config.httpTimeoutMs,
    tlsRejectUnauthorized: config.tlsRejectUnauthorized,
  });
  if (!resp.ok) {
    throw new Error(`self-register failed: ${resp.status} ${resp.text}`);
  }
  const body = resp.json || {};
  return normalizeBootstrap(connectUrl, baseUrl, body.data || {});
}

async function fetchBootstrap(connectUrl, config = {}) {
  const parsed = new URL(connectUrl);
  const token = parsed.searchParams.get("token");
  const baseUrl = `${parsed.protocol}//${parsed.host}`;
  if (!token) return await selfRegister(connectUrl, baseUrl, config);
  const bootstrapUrl = `${baseUrl}/v1/openclaw/agents/bootstrap?token=${encodeURIComponent(token)}`;
  const resp = await requestJson(bootstrapUrl, {
    timeoutMs: config.httpTimeoutMs,
    tlsRejectUnauthorized: config.tlsRejectUnauthorized,
  });
  if (!resp.ok) {
    if ((resp.status === 401 || resp.status === 403) && config.agentId) {
      return await selfRegister(`${baseUrl}/agent-link/connect`, baseUrl, config);
    }
    throw new Error(`bootstrap failed: ${resp.status} ${resp.text}`);
  }
  const body = resp.json || {};
  const data = body.data || {};
  return normalizeBootstrap(connectUrl, baseUrl, data);
}

module.exports = {
  ensureRuntimeIdentityKey,
  fetchBootstrap,
};
