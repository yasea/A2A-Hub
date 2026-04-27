"use strict";

const fs = require("node:fs");
const { ensureRuntimeIdentityKey, fetchBootstrap } = require("./bootstrap");
const { AgentMessageApi } = require("./message-api");
const { MqttCommandClient } = require("./mqtt-client");
const { PresenceClient } = require("./presence");
const { FileStateStore, ensureDir } = require("./state-store");
const { writeAgentLinkLocalControl } = require("./local-control");
const { nowIso } = require("./protocol");

function writeInstallResultMirrors(config, state) {
  const targets = [];
  if (config.userProfileFile) {
    targets.push(`${require("node:path").dirname(config.userProfileFile)}/.agent-link/install-result.json`);
  }
  if (config.stateFile) {
    targets.push(`${require("node:path").dirname(config.stateFile)}/install-result.json`);
  }
  const payload = {
    status: "success",
    stage: "install_online",
    summary: "Agent Link 安装完成，插件已在线",
    detail: null,
    localAgentId: config.localAgentId || config.agentId,
    connectUrl: config.connectUrl || null,
    state: { ...state, public_number: state.publicNumber || state.public_number || null },
    userProfileFile: config.userProfileFile || null,
    updatedAt: nowIso(),
  };
  for (const target of targets) {
    try {
      ensureDir(target);
      fs.writeFileSync(target, JSON.stringify(payload, null, 2) + "\n", "utf8");
    } catch {
      // Best-effort install mirror. Runtime state remains authoritative.
    }
  }
}

class AgentLinkCoreRuntime {
  constructor(api, config, processTask) {
    this.api = api;
    this.logger = api?.logger || console;
    this.config = config;
    this.processTask = processTask;
    this.stateStore = new FileStateStore(config.stateFile);
    this.currentConnectUrl = "";
    this.currentBootstrap = null;
    this.messageApi = null;
    this.mqttClient = null;
    this.presenceClient = null;
    this.fileWatcher = null;
    this.retryTimer = null;
    this.retryAttempt = 0;
    this.started = false;
    this.connecting = false;
  }

  resolveLocalAgentId() {
    return this.config.localAgentId || this.config.agentId;
  }

  _ensureRuntimeIdentityKey() {
    ensureRuntimeIdentityKey(this.config);
  }

  async start() {
    if (!this.config.enabled || this.started) return;
    this.started = true;
    this.logger.info(`aimoo: runtime start localAgentId=${this.config.localAgentId || this.config.agentId}`);
    this._ensureRuntimeIdentityKey();
    ensureDir(this.config.stateFile);
    ensureDir(this.config.connectUrlFile);
    if (!fs.existsSync(this.config.connectUrlFile)) fs.writeFileSync(this.config.connectUrlFile, "", "utf8");
    this.stateStore.write({
      status: "idle",
      updatedAt: nowIso(),
      localAgentId: this.resolveLocalAgentId(),
    });
    await this.reload("startup");
    this.watchConnectUrlFile();
  }

  async stop() {
    if (this.fileWatcher) {
      fs.unwatchFile(this.config.connectUrlFile, this.fileWatcher);
      this.fileWatcher = null;
    }
    this.clearRetry();
    this.started = false;
    this.presenceClient?.stop();
    this.mqttClient?.stop();
    this.presenceClient = null;
    this.mqttClient = null;
    this.messageApi = null;
    this.currentBootstrap = null;
  }

  readConnectUrl() {
    if (this.config.connectUrl) return this.config.connectUrl.trim();
    return fs.readFileSync(this.config.connectUrlFile, "utf8").trim();
  }

  watchConnectUrlFile() {
    if (this.config.connectUrl) return;
    let debounce = null;
    this.fileWatcher = () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        void this.reload("file-change");
      }, 500);
    };
    fs.watchFile(this.config.connectUrlFile, { interval: 1500 }, this.fileWatcher);
  }

  async reload(reason, options = {}) {
    const nextUrl = this.readConnectUrl();
    if (!nextUrl) {
      this.clearRetry();
      this.stateStore.write({
        status: "idle",
        reason: "waiting_for_connect_url",
        updatedAt: nowIso(),
        localAgentId: this.resolveLocalAgentId(),
      });
      return;
    }
    if (!options.force && nextUrl === this.currentConnectUrl && this.currentBootstrap) return;
    if (this.connecting) return;
    this.connecting = true;
    try {
      this.presenceClient?.stop();
      this.presenceClient = null;
      this.mqttClient?.stop();
      this.mqttClient = null;
      this.currentConnectUrl = nextUrl;
      this.currentBootstrap = await fetchBootstrap(nextUrl, this.config);
      this.logger.info(
        `aimoo: bootstrap resolved localAgentId=${this.config.localAgentId || this.config.agentId} agentId=${this.currentBootstrap.agentId} tenantId=${this.currentBootstrap.tenantId}`,
      );
      this.messageApi = new AgentMessageApi(this.currentBootstrap, this.config);
      this.mqttClient = new MqttCommandClient(
        this.currentBootstrap,
        this.config,
        this.stateStore,
        this.logger,
        (payload) => this.processTask(payload, this.messageApi, this.currentBootstrap),
      );
      this.stateStore.write({
        status: "bootstrapped",
        reason,
        updatedAt: nowIso(),
        localAgentId: this.resolveLocalAgentId(),
        bootstrap: {
          agentId: this.currentBootstrap.agentId,
          tenantId: this.currentBootstrap.tenantId,
          mqttBrokerUrl: this.currentBootstrap.mqttBrokerUrl,
          mqttCommandTopic: this.currentBootstrap.mqttCommandTopic,
        },
      });
      await this.mqttClient.connect();
      this.presenceClient = new PresenceClient(this.currentBootstrap, this.config, this.stateStore, {
        logger: this.logger,
        onAuthExpired: (error) => this.refreshAfterPresenceAuthFailure(error),
      });
      await this.presenceClient.send();
      this.presenceClient.start();
      writeInstallResultMirrors(this.config, {
        status: "online",
        updatedAt: nowIso(),
        localAgentId: this.resolveLocalAgentId(),
        topic: this.currentBootstrap.mqttCommandTopic,
        agentId: this.currentBootstrap.agentId,
        tenantId: this.currentBootstrap.tenantId,
        publicNumber: this.currentBootstrap.publicNumber || null,
      });
      writeAgentLinkLocalControl(this.config, this.currentBootstrap);
      this.logger.info(
        `aimoo: instance online localAgentId=${this.config.localAgentId || this.config.agentId} topic=${this.currentBootstrap.mqttCommandTopic}`,
      );
      this.retryAttempt = 0;
      this.clearRetry();
    } catch (error) {
      this.stateStore.write({
        status: "error",
        reason: String(error),
        updatedAt: nowIso(),
        localAgentId: this.resolveLocalAgentId(),
      });
      this.logger.error(`aimoo: reload failed: ${String(error)}`);
      this.scheduleRetry();
    } finally {
      this.connecting = false;
    }
  }

  async refreshAfterPresenceAuthFailure(error) {
    if (!this.started || this.connecting) return;
    this.logger.warn?.(`aimoo: refreshing bootstrap after presence auth failure: ${String(error)}`);
    this.presenceClient?.stop();
    this.presenceClient = null;
    this.mqttClient?.stop();
    this.mqttClient = null;
    this.currentBootstrap = null;
    await this.reload("presence_auth_expired", { force: true });
  }

  clearRetry() {
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  scheduleRetry() {
    if (!this.started || this.retryTimer) return;
    this.retryAttempt += 1;
    const retryIntervalSec =
      Number.isInteger(this.config.bootstrapRetryIntervalSec) && this.config.bootstrapRetryIntervalSec > 0
        ? this.config.bootstrapRetryIntervalSec
        : 30;
    const maxDelayMs = Math.max(retryIntervalSec, 5) * 1000;
    const delayMs = Math.min(maxDelayMs, 1000 * (2 ** Math.min(this.retryAttempt - 1, 5)));
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      void this.reload("retry", { force: true });
    }, delayMs);
    if (typeof this.retryTimer.unref === "function") this.retryTimer.unref();
    this.stateStore.write({
      status: "retry_wait",
      reason: `bootstrap_retry_${this.retryAttempt}`,
      retryAfterMs: delayMs,
      updatedAt: nowIso(),
      localAgentId: this.resolveLocalAgentId(),
    });
  }
}

module.exports = {
  AgentLinkCoreRuntime,
  writeInstallResultMirrors,
  writeAgentLinkLocalControl,
};
