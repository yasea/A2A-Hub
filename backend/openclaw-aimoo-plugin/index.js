"use strict";

const { resolvePluginInstances } = require("./lib/config");
const { AimooChannel } = require("./lib/channel");
const { createAimooCli } = require("./lib/cli");

const PLUGIN_ID = "aimoo-link";

module.exports = {
  id: PLUGIN_ID,
  name: "Aimoo Link",
  version: "0.4.0",
  description: "OpenClaw Aimoo Link channel plugin with embedded Agent Link Core.",
  register(api) {
    const configs = resolvePluginInstances(api).filter((item) => item.enabled !== false);
    const channels = configs.map((config) => new AimooChannel(api, config));
    api.registerCli(createAimooCli(api), { commands: ["aimoo"] });
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
