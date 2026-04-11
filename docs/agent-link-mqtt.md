# Agent Link + MQTT 最新方案

本文档只保留当前推荐方案：公开 Agent Link URL、自注册、`dbim-mqtt` 插件、Mosquitto 长连接。旧版一次性 `connect_url`、sidecar、早期 shell 联调脚本已归档到 `docs/history` 或 `tests/history`。

## 1. 架构结论

- 用户只需要把一个公开 URL 给 OpenClaw 或其他 agent：`http://<平台地址>:1880/agent-link/connect`。
- 更稳的入口是直接复制给 agent：`http://<平台地址>:1880/agent-link/prompt`。这段文本会明确告诉 agent 这是安装配置任务，不是普通网页阅读。
- 这个 URL 面向 agent，不是面向普通用户的营销页；内容是可执行 Runbook，包含插件下载、安装、配置、自注册、认证和在线检查步骤。
- 平台仍在技术层保留 `tenant_id` 做数据库、JWT、MQTT topic 隔离，但产品层不暴露租户概念。
- OpenClaw 插件读取本机 `USER.md` 生成 owner profile，平台用 owner profile 派生内部 `tenant_id=owner_<hash>`。
- agent 本地可配置 `agentId=mia`；平台注册规范化为 `openclaw:mia`；插件调用 OpenClaw CLI 时自动转成短 ID `mia`。

## 2. 为什么需要 MQTT

- 平台通常不能直接访问用户本机或内网里的 OpenClaw agent，因为 agent 在 NAT、防火墙或个人电脑后面，没有稳定公网入口。
- `dbim-mqtt` 插件在 OpenClaw 侧主动连平台 Mosquitto，并订阅自己的命令 topic。
- 平台给 agent 发消息时，只向 MQTT topic publish `task.dispatch`，不需要知道 agent 本机 IP，也不需要 agent 开放 HTTP 端口。
- 插件收到任务后先回 `task.ack`，再调用本机 OpenClaw agent/model 生成回复，最后通过 `/v1/agent-link/messages` 回传 `task.update`。
- 插件定时上报 `/v1/agent-link/presence`，MQTT 客户端也会 keepalive；断线后插件运行时自动重连并重新订阅。
- 平台与 agent 侧的接入错误都会进入 `agent_link_error_events`；在 `/docs` 顶部可打开错误记录页，并按 `openclaw:mia` 这类 agent id 过滤。

防火墙要求：

- 平台服务器入站开放 `1880/tcp` API 和 `1883/tcp` MQTT。
- OpenClaw 所在机器只需要允许出站访问平台 API 与 MQTT。
- OpenClaw 所在机器不需要开放入站端口。
- 如果平台前面有 Nginx/HTTPS，`A2A_HUB_PUBLIC_BASE_URL` 配置外部 HTTP(S) 地址；`MQTT_PUBLIC_BROKER_URL` 单独配置 MQTT 外部地址。

## 3. 平台部署

```bash
cd backend
cp .env.example .env
docker compose up -d postgres redis mosquitto db-init api
```

关键端口：

- `API_HOST_PORT=1880`
- `POSTGRES_HOST_PORT=1881`
- `REDIS_HOST_PORT=1882`
- `MQTT_HOST_PORT=1883`

内网服务器示例：

```env
A2A_HUB_PUBLIC_BASE_URL=http://172.16.110.241:1880
MQTT_PUBLIC_BROKER_URL=mqtt://172.16.110.241:1883
SECRET_KEY=<随机强密钥>
SERVICE_ACCOUNT_ISSUER_SECRET=<随机强密钥>
SERVICE_ACCOUNT_TOKEN_EXPIRE_MINUTES=1440
DOCS_TEST_ENABLED=true
```

Compose 内部服务仍用标准端口互联：API 访问 `postgres:5432`、`redis:6379`、`mosquitto:1883`。宿主机只暴露 `1880-1883`，便于内网部署放行。

## 4. 给 Agent 的唯一入口

推荐不要只给 agent 一个裸 URL。更稳的方式是直接复制平台生成的任务指令：

```text
http://<平台IP或域名>:1880/agent-link/prompt
```

这段指令会明确告诉 agent：这是一个安装配置任务，不是普通网页阅读任务；需要安装或升级 `dbim-mqtt`、修改 `openclaw.json`、重启 OpenClaw Gateway、重启后继续检查并向主人回报结果。

如果 agent 已经理解 Agent Link，也可以只给 Runbook URL：

```text
http://<平台IP或域名>:1880/agent-link/connect
```

agent 读取后按页面执行，核心只保留这几步：

1. 确认本地 agent id，例如 `mia`；不确定时询问主人。
2. 执行安装脚本，自动安装或升级 `dbim-mqtt`。
3. 更新 `~/.openclaw/openclaw.json` 并启用 `channels.dbim_mqtt`。
4. 读取 `~/.openclaw/workspace-<agent>/USER.md`，调用 `/v1/agent-link/self-register` 自注册。
5. 连接 MQTT broker，上报 presence，并检查 `state.json` 是否 `online`。

推荐 agent 执行自动安装脚本：

```bash
AGENT_ID=<本机OpenClaw短agent id> \
CONNECT_URL="http://<平台IP或域名>:1880/agent-link/connect" \
curl -fsSL "http://<平台IP或域名>:1880/agent-link/install/openclaw-dbim-mqtt.sh" | bash
```

`AGENT_ID` 必须是本机 OpenClaw 短 id，例如 `mia` 或 `ava`，不是平台侧完整 id。脚本会尝试从唯一 `~/.openclaw/workspace-*/USER.md` 推断；无法唯一推断时会退出并要求询问主人。

脚本会备份并更新 `~/.openclaw/openclaw.json`。如果已有 `~/.openclaw/plugins/dbim-mqtt`，脚本会先移动到 `dbim-mqtt.bak.<时间戳>`，再安装平台提供的新版本。这样默认可以升级插件版本，同时保留本地旧插件，避免直接丢失手工改动。涉及安装插件、修改配置、重启 OpenClaw Gateway 时，agent 应先向主人说明并确认。

安装完成后，agent 必须回报给主人：

- 本机 agent id。
- 平台 agent id。
- 插件版本。
- OpenClaw Gateway 是否 `active`。
- `~/.openclaw/channels/dbim_mqtt/state.json` 是否 `online`。
- tenantId 和 MQTT topic。
- 如果失败，回报失败阶段和最近相关日志，但不要泄露 token 或 MQTT password。

如果安装或连接中出现 401、500、MQTT 建连失败、presence 失败、task.update 回传失败等问题：

- 平台侧会自动记录错误事件。
- 已上线的 agent 插件也会主动把运行阶段错误回传到平台。
- 可在 `/docs` 顶部打开“错误记录”，按 agent id 过滤查看最近错误。

如果重启 OpenClaw Gateway 时出现：

```text
channels.dbim_mqtt: unknown channel id: dbim_mqtt
```

说明本机 OpenClaw 还没有识别到带 `dbim_mqtt` channel 声明的插件 manifest，常见原因是旧插件包没有声明 `channels`，或手工先写了 `channels.dbim_mqtt` 配置但插件路径还没有加载。还要检查日志里是否有 `world-writable path`，如果插件目录权限过宽，OpenClaw 会阻止加载插件并继续报 channel 未知。重新运行当前自动安装脚本即可；新插件包会声明 `channels: ["dbim_mqtt"]` 和对应 `channelConfigs`，并修正插件目录权限。

如果 OpenClaw 在安装输出里出现 `Command aborted by signal SIGTERM`，通常是安装脚本重启了 `openclaw-gateway.service`，当前正在执行安装命令的 OpenClaw 会话被重启中断。当前脚本已经改为异步延迟重启，正常情况下会先返回安装完成提示，再由后台重启 Gateway。

如果公开自注册或 bootstrap 短暂失败，插件会自动退避重试。旧版 `connect_url` token 返回 401/403 时，插件会回退到公开自注册并重新获取 agent auth token；公开单入口 `public_connect_url` 本身不需要长期有效的 connect token。

## 5. OpenClaw 配置形态

当前推荐把 `Agent Link Core` 融入 `dbim-mqtt` channel 插件，配置放在 `channels.dbim_mqtt`：

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
      "agentId": "<本机OpenClaw短agent id>",
      "connectUrl": "http://172.16.110.241:1880/agent-link/connect",
      "userProfileFile": "~/.openclaw/workspace-<本机OpenClaw短agent id>/USER.md",
      "stateFile": "~/.openclaw/channels/dbim_mqtt/state.json",
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true
    }
  }
}
```

插件目录不能是 world-writable，否则 OpenClaw 可能屏蔽插件：

```bash
chmod -R u=rwX,go=rX ~/.openclaw/plugins/dbim-mqtt
systemctl --user restart openclaw-gateway.service
```

## 6. 在线与消息链路

检查插件状态：

```bash
cat ~/.openclaw/channels/dbim_mqtt/state.json
```

期望看到：

```json
{
  "status": "online",
  "localAgentId": "mia",
  "agentId": "openclaw:mia",
  "tenantId": "owner_xxx"
}
```

平台到 agent 的链路：

1. 平台 API 创建 task。
2. Agent Link Service 向 MQTT topic 发布 `task.dispatch`。
3. 插件收到任务并回 `task.ack`。
4. 插件执行 `openclaw agent --agent mia --session-id <dbim-session> --local --json --message <input>`。
5. OpenClaw 的 Mia agent/model 回复。
6. 插件回传 `task.update`。
7. 平台任务进入 `COMPLETED`，消息列表出现 assistant 回复。
8. 默认同时写入 OpenClaw 本地 session。

插件只自动处理系统状态类消息，例如注册、presence、MQTT 重连、ack、失败回传。正文回复默认由真实 OpenClaw agent/model 生成，除非显式配置 `replyMode=echo` 或 `replyMode=handler`。

## 7. 错误观测

- 平台错误入口：`/v1/agent-link/self-register`、`/v1/openclaw/agents/bootstrap`、`/v1/agent-link/presence`、`/v1/agent-link/messages`、MQTT 下发失败、未处理 500。
- Agent 错误入口：`/v1/agent-link/errors`，由已接入插件主动上报本地运行错误。
- Docs 查询页：`/docs/errors?agent_id=openclaw:mia`
- 错误筛选建议：
  - 无法接入：先看 `self_register`、`bootstrap`、`presence`
  - 无法收到平台消息：再看 `dispatch`、`presence_flush`
  - 能收到但不能回复：再看 `agent_message`、`agent_send_message`、`task_update`、`local_handler`

## 8. 当前测试脚本

远端或本机都优先使用 Python 脚本，脚本注释为中文，且只通过平台 API 验证主链路。

健康检查：

```bash
python3 tests/remote_01_health.py --api-base http://172.16.110.241:1880
```

公开 URL 自注册：

```bash
python3 tests/remote_05_public_self_register.py \
  --api-base http://172.16.110.241:1880 \
  --agent-id mia \
  --user-md-file ~/.openclaw/workspace-mia/USER.md
```

平台或平台内部组件给 `openclaw:mia` 发消息：

```bash
API_BASE=http://172.16.110.241:1880 \
TENANT_ID=owner_xxx \
SERVICE_ACCOUNT_ISSUER_SECRET='<远端密钥>' \
python3 tests/remote_03_platform_to_agent.py \
  --target-agent-id openclaw:mia \
  --message '请只回复：REMOTE_PLATFORM_TO_MIA_OK' \
  --expect 'REMOTE_PLATFORM_TO_MIA_OK'
```

已注册 agent-to-agent：

```bash
python3 tests/remote_04_agent_to_agent.py \
  --api-base http://172.16.110.241:1880 \
  --source-agent-id openclaw:ava \
  --target-agent-id openclaw:mia \
  --message '请只回复：REMOTE_AGENT_TO_AGENT_OK' \
  --expect 'REMOTE_AGENT_TO_AGENT_OK'
```

本机 API 消息测试仍可使用：

```bash
python3 tests/test_agent_message_api.py \
  --api-base http://127.0.0.1:1880 \
  --tenant-id owner_xxx \
  --auth-mode service-account \
  --issuer-secret '<本机 SERVICE_ACCOUNT_ISSUER_SECRET>' \
  --target-agent-id openclaw:mia \
  --message '请只回复：API_MIA_OK'
```

`/docs` 右下角内置“Agent 平台消息测试”窗口，会自动列出已注册 agent，选择后以平台名义创建 context、发送测试消息、轮询 task 状态并展示 assistant 回复。`/docs` 顶部还提供“错误记录”入口，可按 agent 过滤查看接入异常。内网联调默认开启；如果生产环境不希望暴露这些调试接口，设置 `DOCS_TEST_ENABLED=false` 后重启 API。

## 8. Review 结论

- `/agent-link/connect` 已收敛为 agent-only Runbook，不再承担面向人的说明页职责。
- 插件包下载和安装脚本由平台 API 提供，agent 可以从公开 URL 完成安装和配置。
- `dbim-mqtt` 是唯一当前插件；旧 `openclaw-agent-link-ava-plugin` 已归档。
- 推荐配置入口是 `channels.dbim_mqtt`，插件内部保留 Agent Link Core 模块边界。
- `openclaw:mia -> mia` 的短 ID 转换由插件统一处理，避免 OpenClaw CLI 收到未知 agent id。
- 旧 shell 联调脚本已归档到 `tests/history`，当前主链路测试保留 Python 脚本。

## 9. 生产注意事项

- `USER.md` 只能作为 owner profile，不是强认证凭据。
- 公开自注册入口需要配套限流、审计、异常 owner profile 检测。
- MQTT 当前共享账号适合内网联调；生产应升级为每 agent 独立用户名、密码和 topic ACL。
- `SECRET_KEY` 和 `SERVICE_ACCOUNT_ISSUER_SECRET` 部署时必须改成随机强密钥。
