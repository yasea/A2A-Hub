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

function localAgentId(config = {}) {
  return shortAgentId(config.localAgentId || config.agentId);
}

function candidateUserMdPaths(config = {}) {
  const shortId = localAgentId(config);
  const configured = typeof config.userProfileFile === "string" && config.userProfileFile.trim()
    ? [config.userProfileFile.trim()]
    : [];
  return [
    ...configured,
    path.join(os.homedir(), ".openclaw", "workspace", shortId, "USER.md"),
    path.join(os.homedir(), ".openclaw", `workspace-${shortId}`, "USER.md"),
    path.join(os.homedir(), ".openclaw", "workspace", "main", "USER.md"),
    path.join(os.homedir(), ".openclaw", "workspace-main", "USER.md"),
    path.join(os.homedir(), ".openclaw", "workspace", "USER.md"),
    path.join(os.homedir(), ".openclaw", "USER.md"),
  ];
}

function expandHome(filePath) {
  return filePath.startsWith("~/") ? path.join(os.homedir(), filePath.slice(2)) : filePath;
}

function uniquePaths(paths) {
  return [...new Set(paths.map((item) => String(item || "").trim()).filter(Boolean))];
}

function candidateSoulMdPaths(config = {}) {
  const shortId = localAgentId(config);
  const configuredUserPaths = candidateUserMdPaths(config).map(expandHome);
  const siblingSoulPaths = configuredUserPaths.map((filePath) => path.join(path.dirname(filePath), "SOUL.md"));
  return uniquePaths([
    ...siblingSoulPaths,
    path.join(os.homedir(), ".openclaw", "workspace", shortId, "SOUL.md"),
    path.join(os.homedir(), ".openclaw", `workspace-${shortId}`, "SOUL.md"),
    path.join(os.homedir(), ".openclaw", "workspace", "main", "SOUL.md"),
    path.join(os.homedir(), ".openclaw", "workspace-main", "SOUL.md"),
    path.join(os.homedir(), ".openclaw", "workspace", "SOUL.md"),
    path.join(os.homedir(), ".openclaw", "SOUL.md"),
  ]);
}

function readFirstExistingText(filePaths) {
  for (const filePath of filePaths) {
    const expanded = expandHome(filePath);
    if (!fs.existsSync(expanded)) continue;
    const rawText = fs.readFileSync(expanded, "utf8").trim();
    if (!rawText) continue;
    return { filePath: expanded, rawText };
  }
  return null;
}

function extractLabeledSummary(rawText) {
  const normalized = String(rawText || "").replace(/\r/g, "");
  const lines = normalized.split("\n");
  const singleLinePatterns = [
    /^\s*(?:agent_summary|agent intro|agent_intro|self_intro|summary|bio|description|简介|自我简介|自我介绍)\s*[:：-]\s*(.+?)\s*$/i,
  ];
  for (const line of lines) {
    for (const pattern of singleLinePatterns) {
      const match = line.match(pattern);
      if (match && match[1]) return match[1].trim();
    }
  }
  const headingPattern = /^\s*#{1,6}\s*(agent summary|agent intro|self intro|简介|自我简介|自我介绍)\s*$/i;
  for (let index = 0; index < lines.length; index += 1) {
    if (!headingPattern.test(lines[index])) continue;
    const chunk = [];
    for (let cursor = index + 1; cursor < lines.length; cursor += 1) {
      const line = lines[cursor].trim();
      if (!line) {
        if (chunk.length) break;
        continue;
      }
      if (line.startsWith("#")) break;
      chunk.push(line);
    }
    if (chunk.length) return chunk.join(" ");
  }
  return "";
}

function extractLooseSummary(rawText) {
  const normalized = String(rawText || "").replace(/\r/g, "");
  const paragraphs = normalized
    .split(/\n\s*\n+/)
    .map((item) => item.replace(/\s+/g, " ").trim())
    .filter(Boolean);
  for (const paragraph of paragraphs) {
    if (/^(agent[_ ]?id|local agent id|name|owner|username)\s*[:：-]/i.test(paragraph)) continue;
    if (paragraph.length >= 8) return paragraph;
  }
  return "";
}

function normalizeSummary(rawText) {
  return String(rawText || "").replace(/\s+/g, " ").trim().slice(0, 160);
}

function readOwnerProfile(config = {}) {
  const found = readFirstExistingText(candidateUserMdPaths(config));
  if (found) {
    return {
      source: "openclaw-user-md",
      user_md_path: found.filePath,
      raw_text: found.rawText.slice(0, 8192),
      local_agent_id: localAgentId(config),
      hostname: os.hostname(),
    };
  }
  return {
    source: "openclaw-runtime",
    local_agent_id: localAgentId(config),
    hostname: os.hostname(),
  };
}

function readAgentSummary(config = {}) {
  for (const filePath of candidateSoulMdPaths(config)) {
    const found = readFirstExistingText([filePath]);
    if (!found) continue;
    const summary = normalizeSummary(extractLabeledSummary(found.rawText) || extractLooseSummary(found.rawText));
    if (summary) return summary;
  }
  const foundUser = readFirstExistingText(candidateUserMdPaths(config));
  if (foundUser) {
    const summary = normalizeSummary(extractLabeledSummary(foundUser.rawText));
    if (summary) return summary;
  }
  return `OpenClaw agent ${localAgentId(config)}`;
}

module.exports = {
  localAgentId,
  platformAgentId,
  readAgentSummary,
  readOwnerProfile,
  shortAgentId,
};
