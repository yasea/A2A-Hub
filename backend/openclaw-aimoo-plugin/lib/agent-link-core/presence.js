"use strict";

const { nowIso } = require("./protocol");
const { requestJson } = require("./http-client");

function isAuthExpiredResponse(error) {
  const status = Number(error && error.status);
  const text = String((error && error.text) || error || "");
  return (status === 401 || status === 403) && (
    text.includes("Token 已过期")
    || text.toLowerCase().includes("token expired")
    || text.toLowerCase().includes("expired")
  );
}

class PresenceClient {
  constructor(bootstrap, config, stateStore, options = {}) {
    this.bootstrap = bootstrap;
    this.config = config;
    this.stateStore = stateStore;
    this.logger = options.logger || console;
    this.onAuthExpired = options.onAuthExpired || null;
    this.timer = null;
    this.authRefreshRequested = false;
  }

  async send() {
    const resp = await requestJson(this.bootstrap.presenceUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${this.bootstrap.authToken}`,
      },
      body: JSON.stringify({
        status: "online",
        metadata: {
          ...this.config.metadata,
          localAgentId: this.config.agentId,
          connectedAt: nowIso(),
        },
      }),
      timeoutMs: this.config.httpTimeoutMs,
      tlsRejectUnauthorized: this.config.tlsRejectUnauthorized,
    });
    if (!resp.ok) {
      const error = new Error(`presence failed: ${resp.status} ${resp.text}`);
      error.status = resp.status;
      error.text = resp.text;
      throw error;
    }
    this.stateStore.write({
      status: "online",
      updatedAt: nowIso(),
      localAgentId: this.config.agentId,
      topic: this.bootstrap.mqttCommandTopic,
      agentId: this.bootstrap.agentId,
      tenantId: this.bootstrap.tenantId,
    });
  }

  start() {
    if (this.timer) clearInterval(this.timer);
    this.timer = setInterval(() => {
      void this.send().catch((error) => {
        if (isAuthExpiredResponse(error) && this.onAuthExpired && !this.authRefreshRequested) {
          this.authRefreshRequested = true;
          this.logger.warn?.(`aimoo: presence token expired; refreshing bootstrap for localAgentId=${this.config.localAgentId || this.config.agentId}`);
          void this.onAuthExpired(error);
          return;
        }
        this.logger.debug?.(`aimoo: presence heartbeat failed: ${String(error)}`);
      });
    }, this.config.presenceIntervalSec * 1000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

module.exports = {
  PresenceClient,
  isAuthExpiredResponse,
};
