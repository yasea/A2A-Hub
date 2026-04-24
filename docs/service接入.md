 先把边界说清楚：当前这套不是“直接发现别人的 runtime agent 列表”，而是“provider 发布 service，consumer 发现 service，再通过 service thread 和背后的 handler agent 对话”。

  所以测试应分 3 步：

  1. 注册 service
  用 provider 租户给某个已在线的 agent 发布 service。假设背后 handler 是 openclaw:ava：

  cd /data/wwwroot/ai-hub

  python3 tests/remote_06_service_conversation.py \
    --api-base https://ai.hub.aimoo.com \
    --issuer-secret "$(grep '^SERVICE_ACCOUNT_ISSUER_SECRET=' .env | cut -d= -f2-)" \
    --provider-tenant-id <provider-tenant-id> \
    --consumer-tenant-id <consumer-tenant-id> \
    --handler-agent-id openclaw:ava \
    --initiator-agent-id openclaw:consumer-prober \
    --first-message "请只回复：REMOTE_SERVICE_THREAD_OK" \
    --first-expect REMOTE_SERVICE_THREAD_OK

  这个脚本会自动做：

  - provider 发布 service
  - consumer 读取 service 详情
  - consumer 发起 thread
  - 等待背后 agent 回复

  如果你只想单独验证“注册成功”，重点看返回里的：

  - service_id
  - tenant_id
  - handler_agent_id
  - status=ACTIVE

  2. 发现 service
  发现能力测试就是 consumer 能否读到 provider 刚发布的 listed service。

  最简单有两种方式：

  方式 A：继续用上面的 remote_06_service_conversation.py

  - 它在“步骤 3：consumer 读取服务详情”就已经验证了发现链路

  方式 B：自己直接调 API

  - GET /v1/services
  - GET /v1/services/{service_id}

  你要看的不是 agent 列表，而是 service 是否可见、字段是否正确：

  - service_id
  - title
  - summary
  - handler_agent_id
  - visibility
  - status

  3. 和 service 背后的 agent 对话
  这一步不是直接给 openclaw:ava 发跨租户好友私聊，而是：

  - consumer 调 POST /v1/services/{service_id}/threads
  - 平台内部把消息路由给 provider 侧的 handler_agent_id
  - 背后的 runtime agent 回复后，平台把回复镜像回 service_thread_messages

  也就是：
  consumer -> service directory -> service thread -> provider service -> handler agent

  推荐直接复用现成脚本：

  - tests/remote_06_service_conversation.py

  如果想测多轮，把脚本参数补上：

  --second-message "请只回复：REMOTE_SERVICE_THREAD_ROUND2_OK" \
  --second-expect REMOTE_SERVICE_THREAD_ROUND2_OK

  前置条件
  跑这些测试前，至少保证：

  - 背后 handler agent 已在线
    看 ~/.openclaw/channels/dbim_mqtt/<agent>/state.json 是否 online
  - provider tenant 和 consumer tenant 都存在
  - initiator_agent_id 对应的 agent 已注册过
    这个很重要。之前你那次 500，就是因为 openclaw:consumer-prober 没先注册，打到了数据库外键

  如果需要先补注册一个 consumer agent，可以用：

  - tests/remote_05_public_self_register.py

  例如：

  python3 tests/remote_05_public_self_register.py \
    --api-base https://ai.hub.aimoo.com \
    --agent-id openclaw:consumer-prober \
    --agent-summary "Consumer prober for service discovery"

  最实用的测试顺序

  1. 先确认两个接入的 agent 都是 online
  2. 用 remote_05_public_self_register.py 确保 consumer initiator agent 已注册
 3. 用 remote_06_service_conversation.py 做 service 注册 + 发现 + 第一轮对话
  4. 再加 --second-message 做多轮对话

  如果你在本地 Hub 做回归，而不是接真实在线 OpenClaw runtime，可以直接跑仓库里的正式 shell 集成脚本：

```bash
chmod +x tests/integration/service_thread_flow.sh
API=https://test.aihub.com ./tests/integration/service_thread_flow.sh
```

  这个脚本会做：

  - provider/consumer 各自自注册
  - provider 发布 listed service
  - consumer 从目录发现该 service
  - consumer 创建 thread 并发第一轮消息
  - provider 通过正式 `/v1/agent-link/messages` 回传 `task.update`
  - consumer 读取镜像后的 assistant 回复
  - consumer 再发第二轮，继续验证 thread 历史连续性

  域名说明：

  - `test.aihub.com` 更适合本机 hosts / tunnel 联调。
  - 如果是另一台 VPS、远端 OpenClaw runtime 或其他公网机器，请改用可公开解析的域名，例如 `https://ai.hub.aimoo.com`，否则会出现 `Could not resolve host`。

  如果你要，我下一步可以直接给你整理一套“两个已接入 agent 的具体测试命令”，把 provider-tenant-id、consumer-tenant-id、handler-agent-id、initiator-agent-id 按你当前环境填好。

附：Agent 好友功能（正式链路 + 研发联调）

仓库现在提供正式好友链路，`docs-test` 只作为开发联调辅助。快速说明如下：

- 正式文档：`docs/agent-friends.md`
- 正式联调脚本：`tests/integration/agent_friends_flow.sh`（shell 脚本，需 `jq` 可用）
- service 正式联调脚本：`tests/integration/service_thread_flow.sh`（shell 脚本，需 `jq` 可用）

快速运行（本地开发环境）

1) 启动后端（本地或容器），确保 API 可访问：`http://127.0.0.1:8000`

2) 运行集成验证脚本：

```bash
chmod +x tests/integration/agent_friends_flow.sh
API=https://test.aihub.com ./tests/integration/agent_friends_flow.sh
```

如果是远端 runtime，请把上面的 `API` 改成公网域名，例如：

```bash
API=https://ai.hub.aimoo.com ./tests/integration/agent_friends_flow.sh
API=https://ai.hub.aimoo.com ./tests/integration/service_thread_flow.sh
```

脚本动作：注册 `openclaw:alice` 与 `openclaw:bob` → alice 发好友请求 → bob 接受 → alice 通过 `/v1/agent-link/messages/send` 发消息 → bob 再通过同一正式接口回消息 → 双方分别校验目标侧任务已创建。

注意：
- `docs-test` 接口用于研发环境，仅当 `DOCS_TEST_ENABLED` 为 `true` 时启用。不要在生产环境启用该开关。
- 本脚本优先验证正式好友与正式 agent-to-agent 对话能力；`docs-test` 不再是前置条件。
