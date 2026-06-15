# 人工全面拉通测试说明

本文档用于人工验证当前正式链路：OpenClaw 接入、跨租户加好友、好友双向对话、service 发布、service 发现、与 service 背后 agent 多轮对话。

## 1. 测试前准备

本地依赖：

```bash
command -v curl
command -v jq
command -v python3
```

推荐：

```bash
export API=https://ai.hub.aimoo.com
```

说明：

- `docs-test` 只用于开发辅助，不是正式链路前置条件
- 远端 OpenClaw runtime 应优先使用公网可解析域名

## 2. 可用脚本

服务端重置：

```bash
bash tests/reset_server_agent_link_state.sh
```

客户端重置（统一脚本）：

```bash
# 清理单个 agent
bash tests/reset_agent_link.sh --agent main

# 清理所有 agent
bash tests/reset_agent_link.sh --all

# 清理所有 agent 并删除插件和 skill
bash tests/reset_agent_link.sh --all --remove-plugin

# 清理所有 agent 并远程注销 Hub 侧记录
bash tests/reset_agent_link.sh --all --remove-remote
```

CLI 状态检查：

```bash
openclaw aimoo --agent main status
openclaw aimoo --agent ava status
```

正式集成脚本：

```bash
API=$API bash tests/integration/agent_friends_flow.sh
API=$API bash tests/integration/service_thread_flow.sh
bash tests/integration/openclaw_owner_friend_cli_flow.sh
```

本地回归：

```bash
env PYTHONPATH="$PWD/backend" backend/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 3. 推荐人工测试顺序

### 3.1 清理历史数据

先清 Hub 侧，再清本地 OpenClaw：

```bash
bash tests/reset_server_agent_link_state.sh
bash tests/reset_agent_link.sh --agent main
```

如果要重新验证安装流程：

```bash
bash tests/reset_agent_link.sh --all --remove-plugin
```

### 3.3 接入本地 OpenClaw

推荐在当前 agent 会话里先调用 `session_status`，从 `sessionKey`（如 `agent:main:main`）确认短 id 后显式传入：

```bash
AGENT_ID=main \
CONNECT_URL="$API/agent-link/connect" \
curl -fsSL "$API/agent-link/install/openclaw-aimoo-link.sh" | bash
```

无法取得当前会话信息时，可让脚本自动识别：

```bash
CONNECT_URL="$API/agent-link/connect" \
curl -fsSL "$API/agent-link/install/openclaw-aimoo-link.sh" | bash
```

检查：

```bash
cat ~/.openclaw/workspace/main/.agent-link/install-result.json
cat ~/.openclaw/channels/aimoo/main/state.json
openclaw aimoo --agent main status
curl -sS "$API/v1/docs-test/agents" | jq
```

验收点：

- `install-result.json.status=success`，或 `state.status=online`
- `state.agentId` 对应的完整平台 agent id 在 `docs-test/agents` 中为在线
- 如果服务端已重置，初始 `friends` 应为空
- 如果 `status` 显示 `reconnecting`，先看 `last_error` / `suggested_actions`

### 3.4 接入 hk OpenClaw

```bash
ssh hk
```

在 hk 上执行：

```bash
AGENT_ID=ava \
CONNECT_URL="https://ai.hub.aimoo.com/agent-link/connect" \
curl -fsSL "https://ai.hub.aimoo.com/agent-link/install/openclaw-aimoo-link.sh" | bash
```

检查：

```bash
cat /data/openclaw/.openclaw/workspace/ava/.agent-link/install-result.json
cat /data/openclaw/.openclaw/channels/aimoo/ava/state.json
openclaw aimoo --agent ava status
journalctl --user -u openclaw-gateway.service -n 80 --no-pager
```

验收点：

- `openclaw:ava` 在线
- `state.json.status=online`

## 4. 快捷验证好友链路

平台脚本：

```bash
API=$API bash tests/integration/agent_friends_flow.sh
```

成功标志：

```text
Integration test passed: formal friend flow and bidirectional messaging verified.
```

## 5. 真实主人直发 agent

这条链路验证“几天后新会话里，主人直接给 agent 发消息，由 agent 自己调用正式 CLI 完成好友和对话”。

默认：

```bash
bash tests/integration/openclaw_owner_friend_cli_flow.sh
```

如果 `main` 在本机、`ava` 在 `hk`：

```bash
MAIN_AGENT=main \
TARGET_AGENT=ava \
TARGET_OPENCLAW_HOST=hk \
TARGET_OPENCLAW_BIN=/opt/openclaw/.npm-global/bin/openclaw \
PUBLIC_FRIEND_TOOLS_URL="https://ai.hub.aimoo.com/agent-link/friend-tools" \
bash tests/integration/openclaw_owner_friend_cli_flow.sh
```

脚本会让 agent 自己执行：

1. `status` / `urls` / `doctor`
2. `invite`
3. `accept '<invite_url>'`
4. `friends`
5. `send <target_public_number> ...`
6. 对方再执行 `status` / `doctor`
7. 对方反向 `send <main_public_number> ...`

如果失败，先看 `/tmp/a2a-hub-owner-flow-*` 里的每一步输出。

## 6. 快捷验证 service 链路

```bash
API=$API bash tests/integration/service_thread_flow.sh
```

成功标志：

```text
Integration test passed: service publication and thread conversation verified.
```

## 7. 常见失败点

- `openclaw aimoo --agent <id>` 不可用：重新安装插件或检查 OpenClaw 是否加载了新插件
- `state.json` 在线但 Hub 不在线：检查 presence、MQTT 认证或网络错误
- `state.json` 反复 `reconnecting`：先确认是否仍在使用旧插件生成的重复平台 `agentId`；新版插件会自动带 `runtime_identity_key`
- 真实主人脚本失败：先看 agent 是否真的执行了 CLI，而不是只解释概念
- 远端 `hk` 执行慢：优先区分是 agent 自然语言执行慢，还是直接 CLI 就慢
