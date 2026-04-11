"use strict";

const mqtt = require("mqtt");
const { nowIso } = require("./protocol");

class MqttCommandClient {
  constructor(bootstrap, config, stateStore, logger, onTask) {
    this.bootstrap = bootstrap;
    this.config = config;
    this.stateStore = stateStore;
    this.logger = logger || console;
    this.onTask = onTask;
    this.client = null;
  }

  async connect() {
    if (this.client) {
      this.client.end(true);
      this.client = null;
    }
    const cfg = this.bootstrap;
    const client = mqtt.connect(cfg.mqttBrokerUrl, {
      clientId: cfg.mqttClientId,
      username: cfg.mqttUsername,
      password: cfg.mqttPassword,
      reconnectPeriod: 3000,
      keepalive: 60,
      clean: false,
    });
    this.client = client;

    await new Promise((resolve, reject) => {
      let ready = false;
      const failStartup = (error) => {
        if (ready) return;
        client.end(true);
        this.client = null;
        reject(error);
      };

      client.on("connect", () => {
        this.logger.info("dbim-mqtt: mqtt connected");
        client.subscribe(cfg.mqttCommandTopic, { qos: cfg.qos }, (err) => {
          if (err) {
            failStartup(new Error(`mqtt subscribe failed: ${String(err)}`));
            return;
          }
          ready = true;
          resolve();
        });
      });

      client.on("message", (topic, payloadBuffer) => {
        let payload = null;
        try {
          payload = JSON.parse(payloadBuffer.toString("utf8"));
        } catch {
          this.logger.warn(`dbim-mqtt: invalid mqtt payload on ${topic}`);
          return;
        }
        Promise.resolve(this.onTask(payload)).catch((err) => {
          this.logger.error(`dbim-mqtt: task handler failed: ${String(err)}`);
          this.stateStore.write({
            status: "task_error",
            updatedAt: nowIso(),
            localAgentId: this.config.agentId,
            reason: String(err),
          });
        });
      });

      client.on("error", (err) => {
        this.logger.error(`dbim-mqtt: mqtt error: ${String(err)}`);
        if (!ready) {
          failStartup(err instanceof Error ? err : new Error(String(err)));
          return;
        }
        this.stateStore.write({
          status: "mqtt_error",
          updatedAt: nowIso(),
          localAgentId: this.config.agentId,
          reason: String(err),
        });
      });

      client.on("reconnect", () => {
        this.stateStore.write({
          status: "reconnecting",
          updatedAt: nowIso(),
          localAgentId: this.config.agentId,
          topic: cfg.mqttCommandTopic,
          agentId: cfg.agentId,
          tenantId: cfg.tenantId,
        });
      });
    });
  }

  stop() {
    if (this.client) {
      this.client.end(true);
      this.client = null;
    }
  }
}

module.exports = {
  MqttCommandClient,
};
