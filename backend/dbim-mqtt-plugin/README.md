# DBIM MQTT

`dbim-mqtt` 是 OpenClaw 接入 A2A Hub 的当前唯一推荐插件。插件内部包含 Agent Link Core，安装体验上作为一个 OpenClaw channel 插件使用。

最新文档：

- [Agent Link + MQTT 最新方案](../../docs/agent-link-mqtt.md)

## 自动安装

```bash
AGENT_ID=<本机OpenClaw短agent id> \
CONNECT_URL="http://<平台IP或域名>:1880/agent-link/connect" \
curl -fsSL "http://<平台IP或域名>:1880/agent-link/install/openclaw-dbim-mqtt.sh" | bash
```

`AGENT_ID` 是本机短 id，例如 `mia`，不是平台侧完整 id `openclaw:mia`。如果本地已有插件，安装脚本会先备份旧目录再安装新版本。要在同一个 OpenClaw Gateway 里接多个 agent，就分别执行一次，例如先 `AGENT_ID=ava`，再 `AGENT_ID=mia`。

如果 OpenClaw 报 `channels.dbim_mqtt: unknown channel id: dbim_mqtt`，说明本机仍是旧插件 manifest 或配置先于插件加载。还要检查日志里是否有 `world-writable path`，如果插件目录权限过宽，OpenClaw 会阻止加载插件。重新运行当前安装脚本，让 OpenClaw 先识别新插件包里的 `channels: ["dbim_mqtt"]`，并修正插件目录权限。

公开自注册或 bootstrap 失败时，插件会自动退避重试。旧版 token 化入口仅保留兼容；当前推荐始终使用公开 `/agent-link/connect`。

## 推荐配置

单 agent 和多 agent 都建议优先使用 `channels.dbim_mqtt.instances[]`，这样配置形态更一致，也更适合后续继续追加 agent。

```json
{
  "channels": {
    "dbim_mqtt": {
      "enabled": true,
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true,
      "instances": [
        {
          "localAgentId": "mia",
          "agentId": "mia",
          "connectUrl": "http://<平台IP或域名>:1880/agent-link/connect",
          "userProfileFile": "~/.openclaw/workspace-mia/USER.md",
          "stateFile": "~/.openclaw/channels/dbim_mqtt/mia/state.json"
        }
      ]
    }
  }
}
```

如果 `connectUrl` 是公开入口，插件会读取 `USER.md` 作为 owner profile，调用平台 `/v1/agent-link/self-register` 自注册。平台内部生成 `tenant_id` 做隔离，但用户不需要理解租户。

同一个 OpenClaw Gateway 接多个 agent 时，继续在 `instances[]` 里追加：

```json
{
  "channels": {
    "dbim_mqtt": {
      "enabled": true,
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true,
      "instances": [
        {
          "localAgentId": "ava",
          "agentId": "ava",
          "connectUrl": "http://<平台IP或域名>:1880/agent-link/connect",
          "userProfileFile": "~/.openclaw/workspace-ava/USER.md",
          "stateFile": "~/.openclaw/channels/dbim_mqtt/ava/state.json"
        },
        {
          "localAgentId": "mia",
          "agentId": "mia",
          "connectUrl": "http://<平台IP或域名>:1880/agent-link/connect",
          "userProfileFile": "~/.openclaw/workspace-mia/USER.md",
          "stateFile": "~/.openclaw/channels/dbim_mqtt/mia/state.json"
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
chmod -R u=rwX,go=rX ~/.openclaw/plugins/dbim-mqtt
```

安装完成后，优先检查：

```bash
cat ~/.openclaw/workspace-mia/.agent-link/install-result.json
```

平台侧错误与安装异常可在 `/docs/errors?agent_id=openclaw:mia` 查看。
