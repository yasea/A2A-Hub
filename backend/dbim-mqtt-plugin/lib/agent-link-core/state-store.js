"use strict";

const fs = require("node:fs");
const path = require("node:path");

function ensureDir(filePath) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

class FileStateStore {
  constructor(stateFile) {
    this.stateFile = stateFile;
  }

  write(state) {
    ensureDir(this.stateFile);
    fs.writeFileSync(this.stateFile, JSON.stringify(state, null, 2), "utf8");
  }
}

module.exports = {
  FileStateStore,
  ensureDir,
};
