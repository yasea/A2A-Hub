"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createRequire } = require("node:module");
const { randomUUID, randomBytes } = require("node:crypto");

function fallbackStorePath(agentId) {
  return path.join(os.homedir(), ".openclaw", "agents", String(agentId || "main"), "sessions", "sessions.json");
}

function createHostRequire() {
  if (require.main?.filename) return createRequire(require.main.filename);
  return require;
}

function tryRequire(candidates) {
  for (const candidate of candidates) {
    try {
      return candidate();
    } catch {}
  }
  return null;
}

function loadHostDeps() {
  const hostRequire = createHostRequire();
  const distDir = require.main?.filename ? path.dirname(require.main.filename) : "";
  const nodeRoot = path.resolve(path.dirname(process.execPath), "..", "lib", "node_modules");
  const configRuntime = tryRequire([
    () => hostRequire("openclaw/dist/plugin-sdk/config-runtime.js"),
    () => require(path.join(distDir, "plugin-sdk", "config-runtime.js")),
    () => require(path.join(nodeRoot, "openclaw", "dist", "plugin-sdk", "config-runtime.js")),
  ]);
  if (!configRuntime?.updateSessionStore || !configRuntime?.resolveStorePath) {
    throw new Error("无法加载 OpenClaw session runtime");
  }
  const piRuntime = tryRequire([
    () => hostRequire("@mariozechner/pi-coding-agent"),
    () => require(path.join(nodeRoot, "@mariozechner", "pi-coding-agent")),
  ]) || {};
  return {
    updateSessionStore: configRuntime.updateSessionStore,
    resolveStorePath: configRuntime.resolveStorePath,
    CURRENT_SESSION_VERSION: Number.isInteger(piRuntime.CURRENT_SESSION_VERSION)
      ? piRuntime.CURRENT_SESSION_VERSION
      : 3,
  };
}

function safeShortId() {
  return randomBytes(4).toString("hex");
}

function normalizeAgentId(input) {
  return String(input || "main").trim() || "main";
}

function buildSessionKey(agentId, payload) {
  const parts = [
    "agent",
    normalizeAgentId(agentId).toLowerCase(),
    "aimoo-link",
    String(payload?.tenant_id || "").trim().toLowerCase(),
    String(payload?.context_id || payload?.task_id || "default").trim().toLowerCase(),
  ].filter(Boolean);
  return parts.join(":");
}

function buildOriginId(payload) {
  const tenantId = String(payload?.tenant_id || "tenant").trim() || "tenant";
  const contextId = String(payload?.context_id || payload?.task_id || "context").trim() || "context";
  return `aimoo-link:${tenantId}:${contextId}`;
}

function buildLabel(payload) {
  const contextId = String(payload?.context_id || payload?.task_id || "context").trim() || "context";
  return `A2A Hub ${contextId}`;
}

function ensureTranscriptHeader(sessionFile, sessionId, sessionVersion) {
  if (fs.existsSync(sessionFile)) return;
  fs.mkdirSync(path.dirname(sessionFile), { recursive: true });
  const header = {
    type: "session",
    version: sessionVersion,
    id: sessionId,
    timestamp: new Date().toISOString(),
    cwd: process.cwd(),
  };
  fs.writeFileSync(sessionFile, `${JSON.stringify(header)}\n`, { encoding: "utf8", mode: 0o600 });
}

function readLastMessageId(sessionFile) {
  if (!fs.existsSync(sessionFile)) return null;
  const lines = fs.readFileSync(sessionFile, "utf8").trim().split(/\r?\n/).filter(Boolean);
  for (let index = lines.length - 1; index >= 0; index -= 1) {
    try {
      const parsed = JSON.parse(lines[index]);
      if (parsed?.type === "message" && typeof parsed.id === "string" && parsed.id) return parsed.id;
    } catch {}
  }
  return null;
}

function hasIdempotencyKey(sessionFile, idempotencyKey) {
  if (!idempotencyKey || !fs.existsSync(sessionFile)) return false;
  const lines = fs.readFileSync(sessionFile, "utf8").split(/\r?\n/);
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const parsed = JSON.parse(line);
      if (parsed?.message?.idempotencyKey === idempotencyKey) return true;
    } catch {}
  }
  return false;
}

function appendTranscriptMessage(sessionFile, message, idempotencyKey) {
  if (hasIdempotencyKey(sessionFile, idempotencyKey)) return null;
  const parentId = readLastMessageId(sessionFile) || undefined;
  const record = {
    type: "message",
    id: safeShortId(),
    ...(parentId ? { parentId } : {}),
    timestamp: new Date().toISOString(),
    message: {
      ...message,
      ...(idempotencyKey ? { idempotencyKey } : {}),
    },
  };
  fs.appendFileSync(sessionFile, `${JSON.stringify(record)}\n`, "utf8");
  return record.id;
}

class OpenClawSessionRecorder {
  constructor(api, config, deps = null) {
    this.api = api;
    this.config = config;
    this.logger = api?.logger || console;
    this.deps = deps;
  }

  isEnabled() {
    return this.config.recordOpenClawSession !== false;
  }

  resolveStorePath() {
    const deps = this.getDeps();
    try {
      return deps.resolveStorePath(undefined, { agentId: this.config.agentId });
    } catch {
      return fallbackStorePath(this.config.agentId);
    }
  }

  getDeps() {
    this.deps ||= loadHostDeps();
    return this.deps;
  }

  async ensureSession(payload) {
    const deps = this.getDeps();
    const sessionKey = buildSessionKey(this.config.agentId, payload);
    const storePath = this.resolveStorePath();
    const originId = buildOriginId(payload);
    const sessionEntry = await deps.updateSessionStore(
      storePath,
      (store) => {
        const existing = store[sessionKey];
        const sessionId = existing?.sessionId || randomUUID();
        const sessionFile = existing?.sessionFile || path.join(path.dirname(storePath), `${sessionId}.jsonl`);
        const label = existing?.origin?.label || buildLabel(payload);
        const next = {
          ...existing,
          sessionId,
          sessionFile,
          updatedAt: Date.now(),
          systemSent: false,
          abortedLastRun: false,
          chatType: "direct",
          origin: {
            provider: "aimoo-link",
            surface: "aimoo-link",
            chatType: "direct",
            label,
            from: originId,
            to: originId,
            accountId: normalizeAgentId(this.config.agentId),
          },
          deliveryContext: {
            channel: "aimoo-link",
            to: originId,
            accountId: normalizeAgentId(this.config.agentId),
          },
          lastChannel: "aimoo-link",
          lastTo: originId,
          lastAccountId: normalizeAgentId(this.config.agentId),
          displayName: label,
        };
        store[sessionKey] = next;
        return next;
      },
      { activeSessionKey: sessionKey },
    );
    ensureTranscriptHeader(sessionEntry.sessionFile, sessionEntry.sessionId, deps.CURRENT_SESSION_VERSION);
    return {
      sessionKey,
      storePath,
      sessionEntry,
    };
  }

  async recordInboundTask(payload) {
    if (!this.isEnabled()) return null;
    const inputText = String(payload?.input_text || "").trim();
    if (!inputText) return this.ensureSession(payload);
    const session = await this.ensureSession(payload);
    appendTranscriptMessage(
      session.sessionEntry.sessionFile,
      {
        role: "user",
        content: [{ type: "text", text: inputText }],
        timestamp: Date.now(),
      },
      `aimoo-link:user:${payload.task_id || safeShortId()}`,
    );
    return session;
  }

  async recordAssistantResult(payload, resultText, state) {
    if (!this.isEnabled()) return null;
    const text = String(resultText || "").trim();
    const session = await this.ensureSession(payload);
    appendTranscriptMessage(
      session.sessionEntry.sessionFile,
      {
        role: "assistant",
        content: [{ type: "text", text: text || (state === "FAILED" ? "处理失败" : "处理完成") }],
        api: "openai-responses",
        provider: "openclaw",
        model: "aimoo-link",
        usage: {
          input: 0,
          output: 0,
          cacheRead: 0,
          cacheWrite: 0,
          totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
        },
        stopReason: state === "FAILED" ? "error" : "stop",
        timestamp: Date.now(),
      },
      `aimoo-link:assistant:${payload.task_id || safeShortId()}:${state || "completed"}`,
    );
    return session;
  }
}

module.exports = {
  OpenClawSessionRecorder,
  buildSessionKey,
  buildOriginId,
};
