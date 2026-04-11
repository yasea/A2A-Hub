# Agent Link + MQTT

本文档只保留当前生效方案：公开 Agent Link URL、自注册、`dbim-mqtt` 插件、Mosquitto 长连接、workspace 安装结果镜像、平台错误观测。旧版一次性 `connect_url`、sidecar、早期 shell 联调脚本已归档到 `docs/history/` 或 `tests/history/`，不再作为主流程说明。

## 1. 当前结论

- 主人给 OpenClaw 或其他 agent 的标准入口是 `http://<平台地址>:1880/agent-link/prompt`。
- `http://<平台地址>:1880/agent-link/connect` 是给已经理解安装流程的 agent 的 Runbook。
- OpenClaw 侧唯一推荐插件是 `dbim-mqtt`，插件内部集成 Agent Link Core。
- 插件读取本机 `USER.md` 自注册，平台按 owner profile 派生内部 `tenant_id=owner_<hash>` 做隔离，但产品层不暴露租户概念。
- 同一个 OpenClaw Gateway 支持通过 `channels.dbim_mqtt.instances[]` 同时接入多个 agent，例如 `ava` 和 `mia`。
- 安装完成后的首选检查文件是 `~/.openclaw/workspace-<agent>/.agent-link/install-result.json`。
- 平台侧错误与接入异常统一在 `/docs/errors` 查询，可按 `openclaw:mia` 这种 agent id 过滤。

## 2. 为什么是 MQTT

- 平台无法直接访问多数用户本机或内网里的 OpenClaw agent。
- `dbim-mqtt` 由 agent 主动连接平台 Mosquitto，订阅自己的命令 topic。
- 平台只需要 publish `task.dispatch` 到 topic，不需要知道 agent 本机地址，也不需要用户开放入站端口。
- 插件收到任务后先回 `task.ack`，再调用本机 OpenClaw agent/model 生成回复，最后通过 `/v1/agent-link/messages` 回传 `task.update`。
- 插件周期性调用 `/v1/agent-link/presence` 上报在线状态；MQTT 断线后会自动重连并重新订阅。

网络要求：

- 平台服务器入站开放 `1880/tcp` API 和 `1883/tcp` MQTT。
- OpenClaw 所在机器只需允许出站访问平台 API 和 MQTT。
- OpenClaw 所在机器不需要开放任何入站端口。

## 3. 平台部署

```bash
cd backend
cp .env.example .env
docker compose up -d postgres redis mosquitto db-init api
```

默认宿主机端口：

- API: `1880`
- Postgres: `1881`
- Redis: `1882`
- MQTT: `1883`

部署前至少确认：

```env
A2A_HUB_PUBLIC_BASE_URL=http://<平台IP或域名>:1880
MQTT_PUBLIC_BROKER_URL=mqtt://<平台IP或域名>:1883
SECRET_KEY=<随机强密钥>
SERVICE_ACCOUNT_ISSUER_SECRET=<随机强密钥>
DOCS_TEST_ENABLED=true
```

## 4. 主人如何交付给 Agent

推荐直接把下面地址发给 agent：

```text
http://<平台IP或域名>:1880/agent-link/prompt
```

这段文本会明确要求 agent：

1. 这是安装配置任务，不是普通网页阅读。
2. 确认本机短 agent id，例如 `mia`。
3. 下载或升级 `dbim-mqtt`。
4. 修改 `~/.openclaw/openclaw.json`。
5. 重启 OpenClaw Gateway。
6. 重启后优先检查 `install-result.json` 并给主人报告结果。

如果 agent 已经理解这套流程，也可以只给：

```text
http://<平台IP或域名>:1880/agent-link/connect
```

## 5. 安装命令

```bash
AGENT_ID=<本机OpenClaw短agent id> \
CONNECT_URL="http://<平台IP或域名>:1880/agent-link/connect" \
curl -fsSL "http://<平台IP或域名>:1880/agent-link/install/openclaw-dbim-mqtt.sh" | bash
```

说明：

- `AGENT_ID` 必须是本机短 id，例如 `mia` 或 `ava`，不是 `openclaw:mia`。
- 如果本地已有 `~/.openclaw/plugins/dbim-mqtt`，安装脚本会先备份旧目录，再覆盖安装新版本。
- 如果同一个网关还要继续接入第二个 agent，换一个 `AGENT_ID` 再执行一次即可。
- 安装脚本会把结果写到两处：
  - `~/.openclaw/channels/dbim_mqtt/<agent>/install-result.json`
  - `~/.openclaw/workspace-<agent>/.agent-link/install-result.json`
- 如果检测到 `openclaw-gateway.service`，脚本会异步重启 Gateway，并在后台继续检查是否在线，再把结果回传平台 `/v1/agent-link/install-report`。

## 6. OpenClaw 配置

当前推荐统一使用 `channels.dbim_mqtt.instances[]`，单 agent 和多 agent 都按这一种结构配置：

```json
{
  "plugins": {
    "allow": ["dbim-mqtt"],
    "load": {
      "paths": ["~/.openclaw/plugins/dbim-mqtt"]
    },
    "entries": {
      "dbim-mqtt": {
        "enabled": true
      }
    }
  },
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

注意：

- 平台 agent id `openclaw:mia` 会在插件内部自动转换成 OpenClaw CLI 需要的短 id `mia`。
- 插件目录不能是 world-writable，否则 OpenClaw 可能拒绝加载：

```bash
chmod -R u=rwX,go=rX ~/.openclaw/plugins/dbim-mqtt
```

## 7. 安装完成后如何判断成功

优先检查：

```bash
cat ~/.openclaw/workspace-<agent>/.agent-link/install-result.json
```

期望看到：

```json
{
  "status": "success",
  "stage": "install_online",
  "state": {
    "status": "online",
    "agentId": "openclaw:<agent>",
    "tenantId": "owner_xxx"
  }
}
```

如果 agent 运行在沙盒里，无法直接访问宿主机 `systemctl`、`journalctl`、`state.json`，也不要把这误判为安装失败。优先读 workspace 里的结果镜像即可。

## 8. 消息链路

平台到 agent：

1. 平台创建 task。
2. Agent Link Service 向 MQTT topic publish `task.dispatch`。
3. 插件收到任务，先回 `task.ack`。
4. 插件调用本机 OpenClaw agent/model 生成正文回复。
5. 插件通过 `/v1/agent-link/messages` 回传 `task.update`。
6. 平台任务进入终态，并写入消息记录。

系统状态类消息，例如注册、presence、重连、ack、失败回报，由插件自动处理。正文回复默认由真实 OpenClaw agent/model 生成。

## 9. 错误观测

平台侧：

- `/v1/agent-link/self-register`
- `/v1/openclaw/agents/bootstrap`
- `/v1/agent-link/presence`
- `/v1/agent-link/messages`
- MQTT 下发失败
- 未处理 500

Agent 侧：

- `/v1/agent-link/errors`
- `/v1/agent-link/install-report`

查询入口：

- `/docs/errors`
- `/docs/errors?agent_id=openclaw:mia`

建议排查顺序：

1. 无法接入：先看 `self_register`、`bootstrap`、`install`、`presence`
2. 无法收到消息：再看 `dispatch`、`presence_flush`
3. 能收到但不能回复：再看 `agent_message`、`task_update`、`local_handler`

## 10. 当前测试脚本

只保留当前 Python 版本脚本：

健康检查：

```bash
python3 tests/remote_01_health.py --api-base http://<平台IP或域名>:1880
```

准备公开接入信息：

```bash
python3 tests/remote_02_agent_link_prepare.py \
  --api-base http://<平台IP或域名>:1880 \
  --agent-id mia
```

公开自注册：

```bash
python3 tests/remote_05_public_self_register.py \
  --api-base http://<平台IP或域名>:1880 \
  --agent-id mia \
  --user-md-file ~/.openclaw/workspace-mia/USER.md
```

平台发消息给已在线 agent：

```bash
API_BASE=http://<平台IP或域名>:1880 \
TENANT_ID=owner_xxx \
SERVICE_ACCOUNT_ISSUER_SECRET='<平台密钥>' \
python3 tests/remote_03_platform_to_agent.py \
  --target-agent-id openclaw:mia \
  --message '请只回复：REMOTE_PLATFORM_TO_MIA_OK' \
  --expect 'REMOTE_PLATFORM_TO_MIA_OK'
```

模拟一个 agent 给另一个已在线 agent 发消息：

```bash
python3 tests/remote_04_agent_to_agent.py \
  --api-base http://<平台IP或域名>:1880 \
  --source-agent-id openclaw:ava \
  --target-agent-id openclaw:mia \
  --source-user-md-file ~/.openclaw/workspace-ava/USER.md \
  --message '请只回复：REMOTE_AGENT_TO_AGENT_OK' \
  --expect 'REMOTE_AGENT_TO_AGENT_OK'
```

`/docs` 右下角内置 “Agent 平台消息测试” 窗口，可直接选择已注册 agent 并发消息；顶部有“错误记录过滤”入口。

## 11. 当前状态总结

- `/agent-link/prompt` 是主入口。
- `/agent-link/connect` 是 agent-only Runbook。
- `dbim-mqtt` 是唯一推荐插件。
- `install-result.json` + `/v1/agent-link/install-report` 是当前安装结果观测标准。
- 多 agent 单网关已经是当前配置形态，不再单独维护另一套 sidecar 或旧插件方案。
