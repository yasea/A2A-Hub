"use strict";

const { createDbimMqttChannel } = require("./lib/channel");

module.exports = {
  channels: {
    dbim_mqtt: createDbimMqttChannel,
  },
};
