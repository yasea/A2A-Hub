"use strict";

const { resolvePluginInstances } = require("./lib/config");
const { DbimMqttChannel } = require("./lib/channel");
const { createDbimMqttCli } = require("./lib/cli");

const PLUGIN_ID = "dbim-mqtt";

module.exports = {
  id: PLUGIN_ID,
  name: "DBIM MQTT",
  version: "0.4.0",
  description: "OpenClaw DBIM MQTT channel plugin with embedded Agent Link Core.",
  register(api) {
    const configs = resolvePluginInstances(api).filter((item) => item.enabled !== false);
    const channels = configs.map((config) => new DbimMqttChannel(api, config));
    api.registerCli(createDbimMqttCli(api), { commands: ["dbim-mqtt"] });
    api.registerService({
      id: PLUGIN_ID,
      start: async () => {
        for (const channel of channels) {
          await channel.start();
        }
      },
      stop: async () => {
        for (const channel of channels) {
          await channel.stop();
        }
      },
    });
  },
};
