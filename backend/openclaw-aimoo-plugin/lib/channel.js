"use strict";

const { spawn } = require("node:child_process");
const { AgentLinkCoreRuntime } = require("./agent-link-core/runtime");
const { OpenClawSessionRecorder } = require("./openclaw-session-recorder");
const { shortAgentId } = require("./owner-profile");

class AimooChannel {
  constructor(api, config) {
    this.api = api;
    this.logger = api?.logger || console;
    this.config = config;
    this.sessionRecorder = new OpenClawSessionRecorder(api, config);
    this.runtime = new AgentLinkCoreRuntime(api, config, (payload, messageApi) => this.handleTask(payload, messageApi));
    this.taskChain = Promise.resolve();
  }

  async start() {
    this.logger.info(`aimoo: starting instance localAgentId=${this.config.localAgentId || this.config.agentId}`);
    await this.runtime.start();
  }

  async stop() {
    this.logger.info(`aimoo: stopping instance localAgentId=${this.config.localAgentId || this.config.agentId}`);
    await this.runtime.stop();
  }

  async handleTask(payload, messageApi) {
    const runTask = async () => {
      await this.handleTaskNow(payload, messageApi);
    };
    const queued = this.taskChain.then(runTask, runTask);
    this.taskChain = queued.catch(() => {});
    return await queued;
  }

  async handleTaskNow(payload, messageApi) {
    if (payload?.type !== "task.dispatch") return;
    const taskId = payload.task_id;
    try {
      this.logger.info(`aimoo: task ${taskId} start localAgentId=${this.config.localAgentId || this.config.agentId}`);
      await this.recordSessionInbound(payload);
      await messageApi.send({ type: "task.ack", task_id: taskId });
      const result = await this.runHandler(payload);
      await this.recordSessionAssistant(payload, result.output, result.ok ? "COMPLETED" : "FAILED");
      await messageApi.send({
        type: "task.update",
        task_id: taskId,
        state: result.ok ? "COMPLETED" : "FAILED",
        output_text: result.output,
        message_text: result.output,
        message_id: `${taskId}:${Date.now()}`,
      });
      this.logger.info(`aimoo: task ${taskId} completed localAgentId=${this.config.localAgentId || this.config.agentId}`);
    } catch (error) {
      this.logger.error(`aimoo: task ${taskId} failed: ${String(error)}`);
      try {
        await this.recordSessionAssistant(payload, String(error), "FAILED");
        await messageApi.send({
          type: "task.update",
          task_id: taskId,
          state: "FAILED",
          output_text: String(error),
          message_text: String(error),
          message_id: `${taskId}:${Date.now()}:failed`,
        });
      } catch (sendError) {
        this.logger.error(`aimoo: failed to report task failure ${taskId}: ${String(sendError)}`);
      }
    }
  }

  async recordSessionInbound(payload) {
    try {
      await this.sessionRecorder.recordInboundTask(payload);
    } catch (error) {
      this.logger.warn(`aimoo: failed to record inbound session message: ${String(error)}`);
    }
  }

  async recordSessionAssistant(payload, text, state) {
    try {
      await this.sessionRecorder.recordAssistantResult(payload, text, state);
    } catch (error) {
      this.logger.warn(`aimoo: failed to record assistant session message: ${String(error)}`);
    }
  }

  async runHandler(payload) {
    const replyMode = this.resolveReplyMode();
    if (replyMode === "openclaw-agent") return await this.runOpenClawAgent(payload);
    if (replyMode === "handler") return await this.runExternalHandler(payload);
    if (replyMode === "echo") return this.runEchoReply(payload);
    throw new Error(`不支持的 replyMode: ${String(replyMode)}`);
  }

  resolveReplyMode() {
    if (this.config.replyMode === "handler" || this.config.replyMode === "echo" || this.config.replyMode === "openclaw-agent") {
      return this.config.replyMode;
    }
    return this.config.handlerCommand ? "handler" : "openclaw-agent";
  }

  runEchoReply(payload) {
    return {
      ok: true,
      output: `${this.config.agentId} 已通过 aimoo 收到任务 ${payload.task_id}，输入内容：${payload.input_text || ""}`.trim(),
    };
  }

  async runOpenClawAgent(payload) {
    const message = this.resolveTaskInputText(payload);
    // Let the OpenClaw CLI create a valid session ID itself.
    const args = [
      "agent",
      "--agent",
      shortAgentId(this.config.localAgentId || this.config.agentId),
      "--local",
      "--json",
      "--timeout",
      String(this.config.openClawTimeoutSec || 180),
      "--message",
      message,
    ];
    const result = await this.spawnProcess(this.config.openClawCommand || "openclaw", args, payload);
    const parsed = this.parseOpenClawJson(result.stdout)
      || this.parseOpenClawJson(result.stderr)
      || this.parseOpenClawJson(result.combinedText);
    if (!result.ok) {
      const failureText = parsed?.error || parsed?.summary || result.combinedText || result.stderr || result.stdout || "OpenClaw agent 执行失败";
      throw new Error(String(failureText).trim());
    }
    const output = this.extractOpenClawReplyText(parsed, result);
    return {
      ok: true,
      output,
    };
  }

  async runExternalHandler(payload) {
    if (!this.config.handlerCommand) throw new Error("replyMode=handler 但未配置 handlerCommand");
    const args = this.parseShellWords(this.config.handlerCommand);
    const result = await this.spawnProcess(args[0], args.slice(1), payload, JSON.stringify(payload));
    const text = (result.stdout || result.stderr || "").trim();
    return {
      ok: result.ok,
      output: text || (result.ok ? "处理成功" : `处理器退出码: ${result.code}`),
    };
  }

  async spawnProcess(command, args, payload, stdinText = "") {
    return await new Promise((resolve, reject) => {
      const child = spawn(command, args, {
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          ...process.env,
          A2A_TASK_ID: String(payload.task_id || ""),
          A2A_TENANT_ID: String(payload.tenant_id || ""),
          A2A_CONTEXT_ID: String(payload.context_id || ""),
          A2A_TASK_TYPE: String(payload.task_type || ""),
          A2A_TRACE_ID: String(payload.trace_id || ""),
          OPENCLAW_AGENT_ID: String(this.config.agentId || ""),
        },
      });
      let stdout = "";
      let stderr = "";
      child.stdout.on("data", (buf) => { stdout += buf.toString("utf8"); });
      child.stderr.on("data", (buf) => { stderr += buf.toString("utf8"); });
      child.on("error", reject);
      child.on("close", (code) => {
        resolve({
          ok: code === 0,
          code,
          stdout: stdout.trim(),
          stderr: stderr.trim(),
          combinedText: `${stdout}\n${stderr}`.trim(),
        });
      });
      if (stdinText) child.stdin.write(stdinText);
      child.stdin.end();
    });
  }

  resolveTaskInputText(payload) {
    const text = String(payload?.input_text || payload?.message_text || "").trim();
    if (!text) throw new Error("task.dispatch 缺少 input_text，无法交给 OpenClaw agent 处理");
    return text;
  }

  parseOpenClawJson(text) {
    const source = String(text || "").trim();
    if (!source) return null;
    for (let start = 0; start < source.length; start += 1) {
      if (source[start] !== "{") continue;
      const candidate = this.readJsonObjectCandidate(source, start);
      if (!candidate) continue;
      try {
        const parsed = JSON.parse(candidate);
        if (Array.isArray(parsed?.payloads)) return parsed;
      } catch {}
    }
    return null;
  }

  readJsonObjectCandidate(source, start) {
    let depth = 0;
    let inString = false;
    let escaped = false;
    for (let index = start; index < source.length; index += 1) {
      const ch = source[index];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (ch === "\\") {
          escaped = true;
        } else if (ch === '"') {
          inString = false;
        }
        continue;
      }
      if (ch === '"') {
        inString = true;
        continue;
      }
      if (ch === "{") depth += 1;
      if (ch === "}") {
        depth -= 1;
        if (depth === 0) return source.slice(start, index + 1);
      }
    }
    return null;
  }

  extractOpenClawReplyText(parsed, result) {
    const payloads = Array.isArray(parsed?.payloads) ? parsed.payloads : [];
    const text = payloads
      .map((payload) => {
        const parts = [];
        if (typeof payload?.text === "string" && payload.text.trim()) parts.push(payload.text.trim());
        if (typeof payload?.mediaUrl === "string" && payload.mediaUrl.trim()) parts.push(`MEDIA:${payload.mediaUrl.trim()}`);
        return parts.join("\n");
      })
      .filter(Boolean)
      .join("\n");
    if (text) return text;
    const summary = typeof parsed?.summary === "string" ? parsed.summary.trim() : "";
    if (summary) return summary;
    if (result.stdout) return result.stdout;
    if (result.stderr) return result.stderr.split(/\r?\n/).filter(Boolean).pop() || "处理成功";
    return "处理成功";
  }

  parseShellWords(command) {
    const result = [];
    let current = "";
    let quote = "";
    for (let i = 0; i < command.length; i += 1) {
      const ch = command[i];
      if (quote) {
        if (ch === quote) quote = "";
        else current += ch;
        continue;
      }
      if (ch === "'" || ch === '"') {
        quote = ch;
        continue;
      }
      if (/\s/.test(ch)) {
        if (current) {
          result.push(current);
          current = "";
        }
        continue;
      }
      current += ch;
    }
    if (current) result.push(current);
    if (!result.length) throw new Error("handlerCommand 为空");
    return result;
  }
}

module.exports = {
  AimooChannel,
};
