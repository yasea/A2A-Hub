"use strict";

const { requestJson } = require("./http-client");

class AgentMessageApi {
  constructor(bootstrap, config) {
    this.bootstrap = bootstrap;
    this.config = config;
  }

  async send(payload) {
    const resp = await requestJson(this.bootstrap.agentMessageUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${this.bootstrap.authToken}`,
      },
      body: JSON.stringify({ payload }),
      timeoutMs: this.config.httpTimeoutMs,
      tlsRejectUnauthorized: this.config.tlsRejectUnauthorized,
    });
    if (!resp.ok) {
      throw new Error(`agent message failed: ${resp.status} ${resp.text}`);
    }
    return resp.json;
  }
}

module.exports = {
  AgentMessageApi,
};
