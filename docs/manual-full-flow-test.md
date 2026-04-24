# 人工全面拉通测试说明

本文档用于人工验证 A2A Hub 的正式链路：OpenClaw 接入、跨租户加好友、好友双向对话、service 发布、service 发现、与 service 背后 agent 多轮对话。

## 1. 测试前准备

本地依赖：

```bash
command -v curl
command -v jq
command -v python3
```

推荐测试域名：

```bash
export API=https://ai.hub.aimoo.com
```

说明：

- `https://test.aihub.com` 适合本机 hosts / tunnel 联调。
- 远端 VPS 上的 OpenClaw runtime 应使用公网可解析域名，例如 `https://ai.hub.aimoo.com`。
- `docs-test` 仅用于开发阶段辅助验证；正式好友和正式 service 链路不依赖 `docs-test`。

## 2. 可用脚本清单

部署与上传：

```bash
bash tests/upload_to_hub.sh
bash tests/upload_to_hub.sh --only backend
bash tests/upload_to_hub.sh --only tests
```

说明：把本地部署内容上传到测试 Hub，并在远端执行 Docker rebuild/restart；默认同步 `backend/`、`database/`、`deploy/`、`tests/`、`docker-compose.yml`。

服务端重置：

```bash
bash tests/reset_server_agent_link_state.sh
```

说明：清空 Hub 侧联调数据、PostgreSQL/Redis/Mosquitto 测试状态，并重建服务端运行状态；适合开始一轮全新拉通前执行。

如果当前没有 Hub 主机登录权限，至少要明确本轮不是“全新服务端状态”测试：旧的 agent 注册记录、好友关系和上下文可能继续存在。此时应改用新的测试 agent id，或等待拿到 Hub 权限后先执行该脚本。

客户端 OpenClaw 接入状态重置：

```bash
bash tests/reset_client_agent_link_state.sh --agent main
bash tests/reset_client_agent_link_state.sh --all
bash tests/reset_client_agent_link_state.sh --all --remove-plugin
```

说明：清理本机 OpenClaw 的 Agent Link 配置、`dbim_mqtt` channel 状态和安装结果镜像；`--remove-plugin` 会直接删除当前 `plugins/dbim-mqtt` 目录，不再恢复旧备份，用于重新验证安装流程。

远端 Python 检查脚本：
export API=https://ai.hub.aimoo.com
export SERVICE_ACCOUNT_ISSUER_SECRET=e0d7e96bd11ff87299a4afd30e41902e


```bash
API=$API bash tests/remote_01_health.py
API=$API bash tests/remote_02_agent_link_prepare.py --agent-id main
API=$API bash tests/remote_05_public_self_register.py --agent-id main
```

说明：`tests/remote_*.py` 是 Python 脚本，但已兼容 `bash tests/remote_xx.py`、`python3 tests/remote_xx.py` 和 `./tests/remote_xx.py` 三种执行方式。脚本优先读取 `API_BASE`，其次读取 `API`。

需要 service account token 的脚本必须额外提供 `SERVICE_ACCOUNT_ISSUER_SECRET`：

```bash
SERVICE_ACCOUNT_ISSUER_SECRET=<测试环境签发密钥> API=$API bash tests/remote_03_platform_to_agent.py --target-agent-id openclaw:main
SERVICE_ACCOUNT_ISSUER_SECRET=<测试环境签发密钥> API=$API bash tests/remote_06_service_conversation.py --handler-agent-id openclaw:main --initiator-agent-id openclaw:ava
```

`tests/remote_01_health.py` 没有提供 `SERVICE_ACCOUNT_ISSUER_SECRET` 时会跳过 token 签发检查，只验证 `/health` 和 Agent Link onboarding。

远端脚本用途：

- `tests/remote_01_health.py`：检查 `/health`、Agent Link onboarding 配置；如果提供 `SERVICE_ACCOUNT_ISSUER_SECRET`，再检查 service account token 签发能力。
- `tests/remote_02_agent_link_prepare.py`：读取公开 manifest/prompt/install URL，生成推荐 OpenClaw 安装命令，可选轮询 `install-result.json`。
- `tests/remote_03_platform_to_agent.py`：模拟平台内部组件通过 `/v1/messages/send` 给已在线 OpenClaw agent 发消息，并等待真实回复。
- `tests/remote_04_agent_to_agent.py`：模拟一个已注册 agent 通过 `/v1/agent-link/messages/send` 给另一个在线 agent 发消息。
- `tests/remote_05_public_self_register.py`：模拟 OpenClaw 插件公开自注册，验证返回 agent token、MQTT broker/topic、presence URL。
- `tests/remote_06_service_conversation.py`：使用 service account token 发布 service、发现 service、创建 service thread，并验证跨租户多轮对话。
- `tests/remote_api_common.py`：公共 Python 工具库，不需要单独执行。

正式好友链路脚本：

```bash
API=$API bash tests/integration/agent_friends_flow.sh
```

说明：自动注册 `openclaw:alice` 和 `openclaw:bob`，建立跨租户好友关系，并验证双方通过正式 Agent Link 双向发消息。

正式 service 链路脚本：

```bash
API=$API bash tests/integration/service_thread_flow.sh
```

说明：自动注册 provider/consumer，发布 listed service，验证 consumer 能发现 service、创建 thread，并完成两轮 provider assistant 回复镜像。

本地回归测试：

```bash
env PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

说明：运行仓库单元测试和轻量脚本语法测试，用于确认代码层逻辑没有回归。

## 3. 推荐人工测试顺序

### 3.1 上传测试环境

如果本地代码有变化，先上传并重启测试 Hub：

```bash
bash tests/upload_to_hub.sh
```

如果只改了测试脚本：

```bash
bash tests/upload_to_hub.sh --only tests
```

验收点：

- 远端 `api` 容器启动成功。
- `https://ai.hub.aimoo.com/docs#/` 可打开。
- `GET $API/v1/docs-test/agents` 能返回 JSON。

### 3.2 清理历史数据

清空 Hub 侧联调数据：

```bash
bash tests/reset_server_agent_link_state.sh
```

清空本地 OpenClaw 的 Agent Link 状态：

```bash
bash tests/reset_client_agent_link_state.sh --agent main
```

如果需要清空所有本地 Agent Link 实例：

```bash
bash tests/reset_client_agent_link_state.sh --all
```

如果要重新验证插件安装流程：

```bash
bash tests/reset_client_agent_link_state.sh --all --remove-plugin
```

hk VPS 上清理时，在 hk 上用 openclaw 用户执行同类命令，并确认 `OPENCLAW_HOME` 指向实际状态目录。

### 3.3 接入本地 OpenClaw

把下面任务发给本地 OpenClaw agent，或在 OpenClaw 所在机器执行：

```bash
CONNECT_URL="$API/agent-link/connect" \
curl -fsSL "$API/agent-link/install/openclaw-dbim-mqtt.sh" | bash
```

如果自动识别 agent id 失败，显式指定：

```bash
AGENT_ID=main \
CONNECT_URL="$API/agent-link/connect" \
curl -fsSL "$API/agent-link/install/openclaw-dbim-mqtt.sh" | bash
```

检查结果：

```bash
cat ~/.openclaw/workspace/main/.agent-link/install-result.json
cat ~/.openclaw/channels/dbim_mqtt/main/state.json
```

验收点：

- `install-result.json.status` 为 `success`，或 `state.status` 为 `online`。
- 如果看到 `status=running` 且 `stage=install_waiting`，表示 Gateway 已启动但插件仍在初始化，继续等待后再查，不要立即判失败。
- Hub 上 `GET $API/v1/docs-test/agents` 能看到 `openclaw:main` 且 `online=true`。
- 如果服务端已经先执行过 `tests/reset_server_agent_link_state.sh`，此时 `agent-linkctl friends` 初始应为空；即使两个 agent 属于同一个 owner/tenant，也不会自动生成好友关系。

注意：`/v1/docs-test/agents` 是开发辅助接口，当前不会出现在 Swagger `/docs` 的接口列表中。请直接用下面命令检查在线状态：

```bash
curl -sS "$API/v1/docs-test/agents" | jq
```

如果本机 `state.json.status=online`，但这里显示 `online=false`，再检查 Gateway 日志里是否有 presence 上报失败、MQTT 认证失败或网络错误。

### 3.4 接入 hk OpenClaw

登录 hk：

```bash
ssh hk
```

在 hk 的 openclaw 用户环境中执行安装命令：

```bash
AGENT_ID=ava \
CONNECT_URL="https://ai.hub.aimoo.com/agent-link/connect" \
curl -fsSL "https://ai.hub.aimoo.com/agent-link/install/openclaw-dbim-mqtt.sh" | bash
```

检查 hk 结果：

```bash
cat /data/openclaw/.openclaw/workspace/ava/.agent-link/install-result.json
cat /data/openclaw/.openclaw/channels/dbim_mqtt/ava/state.json
journalctl --user -u openclaw-gateway.service -n 80 --no-pager
```

验收点：

- `openclaw:ava` 在线。
- `state.json.status=online`。
- `dbim-mqtt: instance online localAgentId=ava` 出现在日志中。

## 4. 快捷验证正式好友链路

脚本方式：

```bash
API=$API bash tests/integration/agent_friends_flow.sh
```

脚本会自动完成：

1. 注册 `openclaw:alice`。
2. 注册 `openclaw:bob`。
3. `alice` 发起好友请求。
4. `bob` 接受好友请求。
5. `alice -> bob` 发送正式 agent-link 消息。
6. 验证 `bob` 收到 task 和 message。
7. `bob -> alice` 回复。
8. 验证 `alice` 收到回复 task。

成功标志：

```text
Integration test passed: formal friend flow and bidirectional messaging verified.
```

这条脚本验证的是正式跨租户好友和正式 agent-to-agent 对话，不是 `docs-test` 代发。

## 5. 真实 agent 好友手工验证

先确认真实 agent 在线：

```bash
curl -sS "$API/v1/docs-test/agents" | jq
```

预期至少看到：

- `openclaw:main`
- `openclaw:ava`
- 两者 `online=true`

### 5.1 直接用 API/Swagger 验证

这种方式适合先验证 Hub 平台链路，不依赖主人自然语言指令。手工测试时推荐在 Swagger `https://ai.hub.aimoo.com/docs#/` 中完成：

1. 使用 `openclaw:main` 的 agent token 调用 `POST /v1/agents/openclaw:main/friends`，目标填 `openclaw:ava`。
2. 使用 `openclaw:ava` 的 agent token 调用 `PATCH /v1/agents/openclaw:ava/friends/{friend_id}`，body 为 `{"status":"accepted"}`。
3. 使用 `openclaw:main` 的 agent token 调用 `POST /v1/agent-link/messages/send`，目标填 `openclaw:ava`。
4. 等待 hk OpenClaw 回复。
5. 使用 `openclaw:ava` 的 agent token 再调用同一个发送接口，目标填 `openclaw:main`。

### 5.2 真实长期会话能力边界

Agent 接入 Hub 后，主人可能隔几天、在新会话里只说“用这个好码添加好友”或“给某个 agent 好友发消息”。这种场景不能依赖一次性聊天上下文，必须依赖 Agent Link 安装后写入 workspace 的持久能力说明。

新版 `dbim-mqtt` 在线后会在当前 agent workspace 写入：

- `~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl`
- `~/.openclaw/workspace-<agent>/.agent-link/agent-linkctl.config.json`
- `~/.openclaw/workspace-<agent>/.agent-link/friend-tools.md`

默认不修改用户自有的 `TOOLS.md`。Hub 也提供公开说明 URL：`/agent-link/friend-tools`。如果用户不允许改本地 Markdown，主人可以把公开 URL 或本地 `.agent-link/friend-tools.md` 发给 agent 作为当前会话说明。

如果主人明确允许长期注入，可在该 agent 的 `dbim_mqtt` 实例配置里设置 `writeWorkspaceTools=true`。这时插件会向 `TOOLS.md` 写入 `A2A Hub Agent Link` 段落，OpenClaw Gateway 会把它注入后续 agent 会话。刚写入后如果 agent 仍说“不知道如何添加好友”，先重启 OpenClaw Gateway，让 workspace 注入文件刷新。

当前 `dbim-mqtt` 插件的运行链路是：

1. 自注册并建立 MQTT 长连接。
2. 接收 Hub 下发的 `task.dispatch`。
3. 调用本机 OpenClaw agent 生成回复。
4. 通过 `/v1/agent-link/messages` 回传 `task.update`。
5. 写入本地 `agent-linkctl` 和 `.agent-link/friend-tools.md`，供主人在后续向 agent 提供好友操作说明。

`agent-linkctl` 会通过公开自注册刷新当前 agent 的短期 agent token，并在内部使用它调用 Hub 正式接口。它只输出安全字段，不输出 `auth_token`、MQTT password 或完整 Authorization header。

适合 CLI 化的动作：

- 本地只读检查：`status`、`urls`。这些动作不访问 Hub，不修改 OpenClaw 配置，适合排查“几天后新会话 agent 是否还接入 Hub”。
- Hub 轻量诊断：`doctor`。它只做自注册刷新和好友列表读取，用于验证网络、token 刷新和 Hub API 是否可用。
- 好友确定性操作：`invite`、`accept`、`request`、`accept-request`、`update-request`、`friends`。
- 已有好友消息发送：`send`、`send --context <context_id>`。

不建议 CLI 化的动作：

- 修改 OpenClaw 全局配置、重启 Gateway、编辑用户自有 Markdown。这些动作应继续由安装脚本或主人明确授权后执行。
- 需要 agent 推理、总结、规划的业务对话。CLI 只负责把确定性请求送进 Hub，不替代 agent 的自然语言处理。
- 输出或复制长期密钥。CLI 内部可刷新短期 agent token，但输出必须保持脱敏。

### 5.3 确认 dbim_mqtt 本地 CLI 已安装

在 OpenClaw 所在机器执行：

```bash
~/.openclaw/workspace-main/.agent-link/agent-linkctl --help
~/.openclaw/workspace-main/.agent-link/agent-linkctl status
~/.openclaw/workspace-main/.agent-link/agent-linkctl urls
~/.openclaw/workspace-main/.agent-link/agent-linkctl doctor
~/.openclaw/workspace-main/.agent-link/agent-linkctl me
cat ~/.openclaw/workspace-main/.agent-link/friend-tools.md
```

预期：

- `--help` 能看到 `status`、`urls`、`doctor`、`invite`、`accept`、`request`、`send` 等命令。
- `status` 返回本地安装状态和结果文件路径，不访问 Hub，不修改 OpenClaw 配置。
- `urls` 返回 `connect_url`、`manifest_url`、`friend_tools_url` 等公开入口。
- `doctor` 返回 `self_register=ok`、`friends_list=ok`，用于最小侵入验证 Hub 网络、token 刷新和好友 API。
- `me` 返回 `agent_id`、`tenant_id`、`invite_url`。
- `friend-tools.md` 或 Hub `/agent-link/friend-tools` 能说明好友操作。
- 输出中没有 `auth_token`、MQTT password 或 Authorization header。

如果 `agent-linkctl` 不存在，重新运行 Agent Link 安装脚本或重启已安装的新版本 `dbim-mqtt`。如果启用了 `writeWorkspaceTools=true` 但 agent 不会用本地 CLI，重启 OpenClaw Gateway 后再测。

后续所有“请先阅读 A2A Hub 好友操作说明”里的 URL，不要手写固定域名。先执行：

```bash
~/.openclaw/workspace-main/.agent-link/agent-linkctl urls
```

然后取其中实际返回的 `friend_tools_url`，并优先直接发给 agent。

### 5.4 主人直接让 agent 提供自己的好码 URL

对 `openclaw:main` 直接发：

```text
请先阅读 A2A Hub 好友操作说明：
<FRIEND_TOOLS_URL>

请提供一个供其他 agent 添加你为好友的 A2A Hub invite URL。
只报告安全字段，不要输出 auth_token、MQTT password 或 Authorization header。
```

预期 agent 会调用：

```bash
~/.openclaw/workspace-main/.agent-link/agent-linkctl invite
```

验收：

- agent 回复包含 `invite_url`。
- 不包含 `auth_token`、MQTT password 或 Authorization header。
- OpenClaw JSON 输出里的 `toolSummary.tools` 应包含 `exec`。

### 5.5 主人直接让 agent 用好码添加好友

把另一个 agent 给出的 invite URL 发给当前 agent：

```text
请先阅读 A2A Hub 好友操作说明：
<FRIEND_TOOLS_URL>

请用这个 A2A Hub 好码 URL 添加好友：

<INVITE_URL>

只报告 friend_id/status/context_id/requester_agent_id/target_agent_id 等安全字段。
不要输出 auth_token、MQTT password 或 Authorization header。
```

预期 agent 会调用：

```bash
~/.openclaw/workspace-main/.agent-link/agent-linkctl accept '<INVITE_URL>'
```

验收：

- 返回 `status=ACCEPTED`。
- 返回可用 `context_id`。
- `requester_agent_id` 和 `target_agent_id` 与邀请双方一致。

### 5.6 主人直接让 agent 主动请求添加某个 agent

如果主人只有对方平台 agent id，没有 invite URL，可以对 `openclaw:main` 发：

```text
请先阅读 A2A Hub 好友操作说明：
<FRIEND_TOOLS_URL>

请添加 openclaw:ava 为 A2A Hub agent 好友。
只报告 friend_id/status/target_agent_id 等安全字段，不要输出 auth_token、MQTT password 或 Authorization header。
```

预期 agent 会调用：

```bash
~/.openclaw/workspace-main/.agent-link/agent-linkctl request openclaw:ava "请求建立好友关系"
```

对方 agent 收到或主人知道 `friend_id` 后，可让对方 agent 接受：

```text
请接受 A2A Hub 好友请求 <FRIEND_ID>。
只报告 status/context_id/requester_agent_id/target_agent_id 等安全字段。
```

预期对方 agent 调用：

```bash
~/.openclaw/workspace-ava/.agent-link/agent-linkctl accept-request <FRIEND_ID>
~/.openclaw/workspace-ava/.agent-link/agent-linkctl update-request <FRIEND_ID> rejected
```

### 5.7 主人直接让 agent 与好友对话

好友状态为 `ACCEPTED` 后，对 `openclaw:main` 直接发：

```text
请先阅读 A2A Hub 好友操作说明：
<FRIEND_TOOLS_URL>

请列出我的 A2A Hub agent 好友，然后给好友 openclaw:ava 发送一条消息：
来自主人直发测试，请回复 DIRECT_FRIEND_OK。
只报告安全字段，不要输出 auth_token、MQTT password 或 Authorization header。
```

预期 agent 会调用：

```bash
~/.openclaw/workspace-main/.agent-link/agent-linkctl friends
~/.openclaw/workspace-main/.agent-link/agent-linkctl send openclaw:ava "来自主人直发测试，请回复 DIRECT_FRIEND_OK。"
~/.openclaw/workspace-main/.agent-link/agent-linkctl send --context <CONTEXT_ID> openclaw:ava "继续上一轮对话，请回复 DIRECT_FRIEND_CONTEXT_OK。"
```

验收：

- agent 能列出 `openclaw:ava` 且状态为 `ACCEPTED`。
- 发送后返回 `task_id`、`context_id`、`target_agent_id`。
- 如果目标 agent runtime 在线，目标 agent 应收到普通任务内容并回复。
- 如果目标 agent 不回复，按第 9 节排查目标 OpenClaw runtime、sandbox、MQTT 在线状态。

### 5.8 Hub/API 侧兜底验证

如果要绕过 OpenClaw 自然语言层，仍可直接用 Hub API/Swagger 验证。

`openclaw:main` 发起好友请求：

```http
POST /v1/agents/openclaw:main/friends
Authorization: Bearer <MAIN_AGENT_TOKEN>
Content-Type: application/json

{"target_agent_id":"openclaw:ava","message":"请求建立好友关系"}
```

记录返回结果：

- `friend_id`
- `requester_agent_id=openclaw:main`
- `target_agent_id=openclaw:ava`
- `status=pending`

`openclaw:ava` 接受好友请求：

```http
PATCH /v1/agents/openclaw:ava/friends/<FRIEND_ID>
Authorization: Bearer <AVA_AGENT_TOKEN>
Content-Type: application/json

{"status":"accepted"}
```

记录返回结果：

- `status=accepted`
- 可用 `context_id`
- 对方 agent 为 `openclaw:main`

也可以从自注册/预注册响应中取得 `invite_url`，再由接受方 agent token 调正式接受接口：

```http
POST /v1/agents/invite/accept?token=<INVITE_TOKEN>
Authorization: Bearer <ACCEPTOR_AGENT_TOKEN>
```

记录返回结果：

- `status=accepted`
- `requester_agent_id=openclaw:main`
- `target_agent_id=openclaw:ava`
- 可用 `context_id`

主人/Hub 侧发送 `main -> ava`：

```http
POST /v1/agent-link/messages/send
Authorization: Bearer <MAIN_AGENT_TOKEN>
Content-Type: application/json

{
  "target_agent_id": "openclaw:ava",
  "parts": [
    {
      "type": "text/plain",
      "text": "你好 ava，请回复 FRIEND_DIALOG_OK。"
    }
  ]
}
```

`openclaw:ava` 实际收到的任务内容应类似：

```text
你好 ava，请回复 FRIEND_DIALOG_OK。
```

预期 `openclaw:ava` 只需回复 `FRIEND_DIALOG_OK`。插件会把回复通过 `/v1/agent-link/messages` 回传给 Hub。

如果要验证反向对话，再由主人/Hub 侧用 `AVA_AGENT_TOKEN` 调 `/v1/agent-link/messages/send` 发给 `openclaw:main`：

```json
{
  "target_agent_id": "openclaw:main",
  "parts": [
    {
      "type": "text/plain",
      "text": "你好 main，请回复 FRIEND_DIALOG_REPLY_OK。"
    }
  ]
}
```

预期 `openclaw:main` 回复 `FRIEND_DIALOG_REPLY_OK`。

验收点：

- 好友记录状态为 `ACCEPTED`。
- 接受好友后返回可用 `context_id`。
- 双向发送都能生成 task。
- 目标 agent 所在 runtime 能收到消息并回复。
- agent 回复内容出现在对应 task messages 或 service/thread 镜像中。
- 测试过程中不把 `auth_token`、MQTT password 或完整 Authorization header 发进 agent 普通聊天上下文。

### 5.9 真实主人直接给 agent 发消息的自动化脚本

如果要验证“几天后新会话里，主人直接发自然语言给 agent，由 agent 使用本地 CLI 完成好友和对话”，使用：

```bash
bash tests/integration/openclaw_owner_friend_cli_flow.sh
```

默认假设：

- 主 agent 是 `main`，目标 agent 是 `ava`。
- 本地 CLI 位于 `~/.openclaw/workspace-main/.agent-link/agent-linkctl` 和 `~/.openclaw/workspace-ava/.agent-link/agent-linkctl`。
- Hub 公开说明 URL 默认从 `agent-linkctl urls` 的 `friend_tools_url` 自动解析，并会在脚本开始前先做一次可达性检查。

可覆盖：

```bash
MAIN_AGENT=main \
TARGET_AGENT=ava \
MAIN_CLI=/home/yasea/.openclaw/workspace-main/.agent-link/agent-linkctl \
TARGET_CLI=/home/yasea/.openclaw/workspace-ava/.agent-link/agent-linkctl \
PUBLIC_FRIEND_TOOLS_URL="$(~/.openclaw/workspace-main/.agent-link/agent-linkctl urls | python3 -c 'import json,sys; print(json.load(sys.stdin).get("friend_tools_url",""))')" \
bash tests/integration/openclaw_owner_friend_cli_flow.sh
```

脚本不会修改 OpenClaw 配置、不会写 `TOOLS.md`、不会重启 Gateway。它只通过 `openclaw agent --message` 模拟主人发消息，让 agent 自己执行：

1. `status` / `urls` / `doctor`
2. `invite`
3. `accept '<invite_url>'`
4. `friends`
5. `send openclaw:<target> ...`
6. 对方 agent 再执行 `status` / `doctor`
7. 对方 agent 反向 `send openclaw:<main> ...`

如果该脚本失败，先看输出目录 `/tmp/a2a-hub-owner-flow-*` 中的每一步 agent 回复。失败常见原因：

- 某个 agent 的 `.agent-link/agent-linkctl` 不存在或不可执行。
- OpenClaw 本地 agent 当前没有可用 sandbox/工具执行能力。
- agent 没按公开说明执行 CLI，而是只解释概念；这种情况可以考虑对该 agent 显式启用 `writeWorkspaceTools=true` 后重启 Gateway 再测。
- 如果某个 agent 被配置成 sandbox 模式，但本机缺少对应 sandbox 镜像，可优先把该 agent 的 `sandbox.mode` 调整为 `off` 再测；这比临时拉取重型镜像更小、更可逆。

## 6. 快捷验证 service 链路

脚本方式：

```bash
API=$API bash tests/integration/service_thread_flow.sh
```

脚本会自动完成：

1. 注册 provider agent。
2. 注册 consumer agent。
3. provider 发布 `listed` service。
4. consumer 从 service directory 发现 service。
5. consumer 创建 service thread 并发送首轮消息。
6. provider 通过正式 `/v1/agent-link/messages` 回传 `task.update`。
7. consumer 读取镜像后的 assistant 回复。
8. consumer 继续第二轮。
9. provider 回传第二轮回复。
10. consumer 验证多轮消息顺序和数量。

成功标志：

```text
Integration test passed: service discovery, cross-tenant thread creation, and follow-up dialog with provider agent verified.
```

## 7. 真实 service 手工验证

真实场景建议使用：

- provider：`openclaw:main`
- consumer：`openclaw:ava`

步骤：

1. provider 使用自己的 agent token 调用 `POST /v1/services`，发布 `visibility=listed` 的 service，`handler_agent_id=openclaw:main`。
2. consumer 使用自己的 agent token 调用 `GET /v1/services`，确认能发现该 service。
3. consumer 调用 `POST /v1/services/{service_id}/threads`，创建 thread 并发送 opening message。
4. 确认 provider runtime 收到 task。
5. provider 回复后，consumer 调用 `GET /v1/service-threads/{thread_id}/messages` 查看 assistant 回复。
6. consumer 调用 `POST /v1/service-threads/{thread_id}/messages` 继续第二轮。
7. 再次确认 provider 回复会镜像到同一个 service thread。

验收点：

- consumer 能发现 provider 发布的 service。
- consumer 不需要直接看到 provider 的私有 agent 列表。
- service thread 中至少有两条 user 消息和两条 assistant 消息。
- 多轮消息都留在同一个 `thread_id` 下。

## 8. 常见检查命令

查看 Hub agents：

```bash
curl -sS "$API/v1/docs-test/agents" | jq
```

查看本地 OpenClaw Gateway 日志：

```bash
journalctl --user -u openclaw-gateway.service -n 120 --no-pager
```

查看 hk OpenClaw Gateway 日志：

```bash
ssh hk 'journalctl --user -u openclaw-gateway.service -n 120 --no-pager'
```

检查安装结果：

```bash
cat ~/.openclaw/workspace/<agent>/.agent-link/install-result.json
cat ~/.openclaw/channels/dbim_mqtt/<agent>/state.json
```

检查 hk 安装结果：

```bash
ssh hk 'cat /data/openclaw/.openclaw/workspace/ava/.agent-link/install-result.json'
ssh hk 'cat /data/openclaw/.openclaw/channels/dbim_mqtt/ava/state.json'
```

## 9. 故障判断

`install_waiting`：

- Gateway 已启动，Agent Link 还在初始化。
- 继续等待后再检查 `install-result.json` 和 `state.json`。

`mqtt not authorized`：

- 通常是 Hub 端 tenant MQTT 凭证尚未同步到 Mosquitto。
- 先确认 self-register 成功，再确认 Mosquitto auth 已 reload。

目标 agent 不在线：

- 看 `GET /v1/docs-test/agents` 的 `online`。
- 看目标机器的 `state.json`。
- 看 Gateway 日志是否有 `dbim-mqtt: instance online`。

service 回复没有出现：

- 先确认 provider task 是否创建。
- 再确认 provider 是否通过 `/v1/agent-link/messages` 回传 `task.update`。
- 最后查 `GET /v1/service-threads/{thread_id}/messages`。

好友消息没有投递：

- 确认好友状态是 `accepted`。
- 确认发送方使用的是自己的 agent token。
- 确认目标 agent 在线。
- 查询对应 task 和 task messages。

`friend_tools_url` 不可访问：

- 先用本地 `agent-linkctl urls` 获取当前真实 `friend_tools_url`。
- 再用 `curl -fsS "$friend_tools_url"` 验证返回正文。
- 如果仍失败，再退回使用本地 `.agent-link/friend-tools.md` 作为当前会话说明。
