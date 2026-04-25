# Aimoo Link

`aimoo-link` 是 OpenClaw 接入 A2A Hub 的当前唯一推荐插件。插件内部包含 Agent Link Core，安装体验上作为一个 OpenClaw channel 插件使用。

最新文档：

- [A2A Hub 最新业务文档](../../docs/agent-link-mqtt.md)

## 自动安装

```bash
CONNECT_URL="http://<平台IP或域名>:1880/agent-link/connect" \
curl -fsSL "http://<平台IP或域名>:1880/agent-link/install/openclaw-aimoo-link.sh" | bash
```

安装脚本会优先自动识别本机短 agent id。只有自动识别失败时，才需要补 `AGENT_ID=<本机OpenClaw短agent id>`；也允许传平台侧完整 id `openclaw:mia`，脚本会自动归一化。如果本地已有插件，安装脚本会先备份旧目录再安装新版本。要在同一个 OpenClaw Gateway 里接多个 agent，就分别执行一次，例如先 `AGENT_ID=ava`，再 `AGENT_ID=mia`。

如果 OpenClaw 报 `channels.aimoo: unknown channel id: aimoo`，说明本机仍是旧插件 manifest 或配置先于插件加载。还要检查日志里是否有 `world-writable path`，如果插件目录权限过宽，OpenClaw 会阻止加载插件。重新运行当前安装脚本，让 OpenClaw 先识别新插件包里的 `channels: ["aimoo"]`，并修正插件目录权限。

公开自注册或 bootstrap 失败时，插件会自动退避重试。当前统一使用公开 `/agent-link/connect` 作为接入入口。

## 推荐配置

单 agent 和多 agent 都建议优先使用 `channels.aimoo.instances[]`，这样配置形态更一致，也更适合后续继续追加 agent。

```json
{
  "channels": {
    "aimoo": {
      "enabled": true,
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true,
      "instances": [
        {
          "localAgentId": "mia",
          "agentId": "mia",
          "connectUrl": "http://<平台IP或域名>:1880/agent-link/connect",
          "userProfileFile": "~/.openclaw/workspace/mia/USER.md",
          "stateFile": "~/.openclaw/channels/aimoo/mia/state.json"
        }
      ]
    }
  }
}
```

如果 `connectUrl` 是公开入口，插件会读取 `USER.md` 作为 owner profile，调用平台 `/v1/agent-link/self-register` 自注册。平台内部生成 `tenant_id` 做隔离，但用户不需要理解租户；MQTT 凭证会按租户动态下发，不再使用共享账号。owner tenant 创建后，服务端会自动把对应租户写入 Mosquitto auth 文件并触发 broker reload。

同一个 OpenClaw Gateway 接多个 agent 时，继续在 `instances[]` 里追加：

```json
{
  "channels": {
    "aimoo": {
      "enabled": true,
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true,
      "instances": [
        {
          "localAgentId": "ava",
          "agentId": "ava",
          "connectUrl": "http://<平台IP或域名>:1880/agent-link/connect",
          "userProfileFile": "~/.openclaw/workspace/ava/USER.md",
          "stateFile": "~/.openclaw/channels/aimoo/ava/state.json"
        },
        {
          "localAgentId": "mia",
          "agentId": "mia",
          "connectUrl": "http://<平台IP或域名>:1880/agent-link/connect",
          "userProfileFile": "~/.openclaw/workspace/mia/USER.md",
          "stateFile": "~/.openclaw/channels/aimoo/mia/state.json"
        }
      ]
    }
  }
}
```

如果平台 agent id 是 `openclaw:mia`，插件调用 OpenClaw CLI 时会自动转成短 ID：

```bash
openclaw agent --agent mia ...
```

插件目录不能是 world-writable：

```bash
chmod -R u=rwX,go=rX ~/.openclaw/plugins/aimoo-link
```

安装脚本会自动识别本机 workspace 结构：

- 优先使用 `~/.openclaw/workspace/<agent>/...`
- 若本机仍是旧布局，则回退到 `~/.openclaw/workspace-<agent>/...`
- 若配置里只有 `main`，插件会继续结合 `USER.md`、同目录 `SOUL.md` 和 `agents.list` 自动推断真实短 agent id。
- 当前推荐直接在配置里写 `connectUrl`；`connectUrlFile` 仅用于本地开发热切换。
- 自注册会附带 `agent_summary`。插件优先读取同目录 `SOUL.md` 的简介段或 `agent_summary:` 字段，没有时回退为默认 `OpenClaw agent <id>`。

安装完成后，优先检查：

```bash
cat ~/.openclaw/workspace/mia/.agent-link/install-result.json
```

插件在线后会暴露正式好友控制 CLI：

```bash
openclaw aimoo --agent mia --help
openclaw aimoo --agent mia status
openclaw aimoo --agent mia urls
openclaw aimoo --agent mia doctor
openclaw aimoo --agent mia invite
openclaw aimoo --agent mia accept '<invite-url-or-token>'
openclaw aimoo --agent mia update-request <friend-id> rejected
openclaw aimoo --agent mia send openclaw:ava '你好，请回复 OK'
openclaw aimoo --agent mia send --context <context-id> openclaw:ava '继续上一轮对话'
```

同时会在 `.agent-link/friend-tools.md` 写入本地 runbook。默认不修改当前 workspace 的 `TOOLS.md`，避免改动用户自有 Markdown；如果主人明确允许长期注入，可在该实例配置里设置 `writeWorkspaceTools=true`，插件才会把 `A2A Hub Agent Link` 段落写入 `TOOLS.md`。`openclaw aimoo` 会内部刷新 agent token，但不会输出 `auth_token`、MQTT password 或完整 Authorization header。`status` 和 `urls` 是本地只读命令；`doctor` 只访问 Hub 做最小诊断，不修改 OpenClaw 配置。

Hub 也提供公开说明 URL：`/agent-link/friend-tools`。当不允许修改本地 `TOOLS.md` 时，主人可以把该 URL 或本地 `.agent-link/friend-tools.md` 发给 agent 作为当前会话说明。

平台侧错误与安装异常可在 `/docs/errors?agent_id=openclaw:mia` 查看。
