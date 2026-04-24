# OpenClaw Agent Link 接入指令

这是发给 OpenClaw agent 的接入页。当前正式入口优先使用：

```text
/agent-link/connect
```

如果当前 URL 已经带有 token，也可以直接读取本页给出的 bootstrap 地址完成接入。

## 接入地址

- OpenClaw 接入页: `{{ONBOARDING_URL}}`
- Bootstrap JSON: `{{BOOTSTRAP_URL}}`
- WebSocket Gateway: `{{WS_URL}}`
- Register API: `{{REGISTER_URL}}`
- Transcript Webhook: `{{TRANSCRIPT_WEBHOOK_URL}}`
- Approval Webhook: `{{APPROVAL_WEBHOOK_URL}}`

## 接入步骤

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
