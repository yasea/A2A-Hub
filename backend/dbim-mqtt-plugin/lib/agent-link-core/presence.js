"use strict";

const { nowIso } = require("./protocol");
const { requestJson } = require("./http-client");

class PresenceClient {
  constructor(bootstrap, config, stateStore) {
    this.bootstrap = bootstrap;
    this.config = config;
    this.stateStore = stateStore;
    this.timer = null;
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
      throw new Error(`presence failed: ${resp.status} ${resp.text}`);
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
      void this.send().catch(() => {});
    }, this.config.presenceIntervalSec * 1000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}

module.exports = {
  PresenceClient,
};
