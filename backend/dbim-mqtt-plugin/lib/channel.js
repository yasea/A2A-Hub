"use strict";

function createDbimMqttChannel(runtimeConfig = {}) {
  return {
    id: "dbim_mqtt",
    runtimeConfig,
    async start() {
      return { ok: true };
    },
    async stop() {
      return { ok: true };
    },
  };
}

module.exports = {
  createDbimMqttChannel,
};
