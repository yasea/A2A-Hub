"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

function shortAgentId(agentId) {
  const value = String(agentId || "agent").trim() || "agent";
  return value.includes(":") ? value.split(":").pop() : value;
}

function platformAgentId(agentId) {
  const value = String(agentId || "agent").trim() || "agent";
  return value.includes(":") ? value : `openclaw:${value}`;
}

function candidateUserMdPaths(config = {}) {
  const shortId = shortAgentId(config.agentId);
  const configured = typeof config.userProfileFile === "string" && config.userProfileFile.trim()
    ? [config.userProfileFile.trim()]
    : [];
  return [
    ...configured,
    path.join(os.homedir(), ".openclaw", `workspace-${shortId}`, "USER.md"),
    path.join(os.homedir(), ".openclaw", "workspace-main", "USER.md"),
    path.join(os.homedir(), ".openclaw", "USER.md"),
  ];
}

function readOwnerProfile(config = {}) {
  for (const filePath of candidateUserMdPaths(config)) {
    const expanded = filePath.startsWith("~/") ? path.join(os.homedir(), filePath.slice(2)) : filePath;
    if (!fs.existsSync(expanded)) continue;
    const rawText = fs.readFileSync(expanded, "utf8").trim();
    if (!rawText) continue;
    return {
      source: "openclaw-user-md",
      user_md_path: expanded,
      raw_text: rawText.slice(0, 8192),
      local_agent_id: shortAgentId(config.agentId),
      hostname: os.hostname(),
    };
  }
  return {
    source: "openclaw-runtime",
    local_agent_id: shortAgentId(config.agentId),
    hostname: os.hostname(),
  };
}

module.exports = {
  platformAgentId,
  readOwnerProfile,
  shortAgentId,
};
