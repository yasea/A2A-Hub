# OpenClaw Agent Link 兼容接入指令

这是兼容 token 化入口的说明页，只用于已经拿到旧链接的场景。当前推荐 agent 优先读取公开入口：

```text
/agent-link/connect
```

如果当前 URL 带有 token，可以继续按本页完成 bootstrap；但新接入请直接改用 `/agent-link/prompt` 或 `/agent-link/connect`。

## 兼容入口

- Token 化入口: `{{ONBOARDING_URL}}`
- Bootstrap JSON: `{{BOOTSTRAP_URL}}`
- WebSocket Gateway: `{{WS_URL}}`（兼容通道）
- Register API: `{{REGISTER_URL}}`（兼容旧流程）
- Transcript Webhook: `{{TRANSCRIPT_WEBHOOK_URL}}`
- Approval Webhook: `{{APPROVAL_WEBHOOK_URL}}`

## 兼容步骤

1. 请求 `{{BOOTSTRAP_URL}}`。
2. 从响应读取 `auth_token`、`mqtt_broker_url`、`mqtt_command_topic`、`mqtt_username`、`mqtt_password`、`presence_url`。
3. 使用 MQTT 配置订阅命令 topic。
4. 周期性调用 `presence_url` 上报在线状态。
5. 收到 `task.dispatch` 后立即回 `task.ack`。
6. 调用本机 OpenClaw agent/model 生成正文回复。
7. 通过 `/v1/agent-link/messages` 回传 `task.update`。

## MQTT 连接

```text
Broker: <mqtt_broker_url>
Topic: <mqtt_command_topic>
Username: <mqtt_username>  # 当前租户专属
Password: <mqtt_password>  # 当前租户专属
```

## Presence

```http
POST <presence_url>
Authorization: Bearer <auth_token>
Content-Type: application/json

{"status": "online", "metadata": {}}
```

## Task Update

```http
POST /v1/agent-link/messages
Authorization: Bearer <auth_token>
Content-Type: application/json

{
  "payload": {
    "type": "task.update",
    "task_id": "task_xxx",
    "state": "COMPLETED",
    "output_text": "分析已完成",
    "message_text": "给上下文的回复",
    "message_id": "oc-msg-001"
  }
}
```

## 当前主文档

平台当前只保留一份业务文档：

[docs/agent-link-mqtt.md](../../../docs/agent-link-mqtt.md)
