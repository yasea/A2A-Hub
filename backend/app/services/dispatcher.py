"""
Dispatcher：多通道任务投递。

支持三种投递通道：
  - mqtt：通过 Agent Link MQTT 长连接推送（默认，原有逻辑）
  - webhook：通过 HTTP POST 推送到 Agent 的 webhook_url
  - telegram_bot：通过 Telegram Bot API 发消息触发 Agent 执行

Agent 的 dispatch_channel 存储在 config_json["dispatch_channel"] 中，
dispatch_config 存储在 config_json["dispatch_config"] 中。
无需数据库 schema 变更，复用现有 JSONB 字段。
"""

import logging
from typing import Any

import httpx

from app.models.agent import Agent

logger = logging.getLogger(__name__)

# 投递超时（秒）
WEBHOOK_TIMEOUT = 30
TELEGRAM_API_TIMEOUT = 30


class DispatcherError(Exception):
    """投递失败"""
    pass


class WebhookDispatcher:
    """HTTP POST 投递到 Agent 的 webhook_url"""

    def __init__(self, config: dict[str, Any]):
        self.webhook_url = config.get("webhook_url")
        if not self.webhook_url:
            raise DispatcherError("webhook_url 未配置")

        self.headers = config.get("headers", {})
        self.secret = config.get("secret")

    async def dispatch(self, payload: dict[str, Any]) -> bool:
        """投递任务到 webhook endpoint，返回是否成功"""
        body = {**payload}
        if self.secret:
            import hashlib
            import hmac
            sig = hmac.new(
                self.secret.encode(),
                json_dumps(body).encode(),
                hashlib.sha256,
            ).hexdigest()
            self.headers["X-Hub-Signature-256"] = f"sha256={sig}"

        try:
            async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as client:
                resp = await client.post(
                    self.webhook_url,
                    json=body,
                    headers=self.headers,
                )
                resp.raise_for_status()
                logger.info(
                    "webhook.dispatch ok url=%s task_id=%s status=%d",
                    self.webhook_url,
                    payload.get("task_id"),
                    resp.status_code,
                )
                return True
        except httpx.HTTPStatusError as exc:
            logger.error(
                "webhook.dispatch http_error url=%s task_id=%s status=%d body=%s",
                self.webhook_url,
                payload.get("task_id"),
                exc.response.status_code,
                exc.response.text[:200],
            )
            return False
        except Exception as exc:
            logger.error(
                "webhook.dispatch error url=%s task_id=%s err=%s",
                self.webhook_url,
                payload.get("task_id"),
                exc,
            )
            return False


class TelegramBotDispatcher:
    """通过 Telegram Bot API 发消息触发 Agent"""

    TELEGRAM_API = "https://api.telegram.org"

    def __init__(self, config: dict[str, Any]):
        self.bot_token = config.get("bot_token")
        self.chat_id = config.get("chat_id")
        if not self.bot_token or not self.chat_id:
            raise DispatcherError("bot_token 或 chat_id 未配置")

        self.parse_mode = config.get("parse_mode", "Markdown")

    async def dispatch(self, payload: dict[str, Any]) -> bool:
        """通过 Telegram sendMessage 投递任务，返回是否成功"""
        task_id = payload.get("task_id", "")
        input_text = payload.get("input_text", "")
        task_type = payload.get("task_type", "generic")
        context_id = payload.get("context_id", "")
        tenant_id = payload.get("tenant_id", "")

        # 构造发给 Telegram Bot 的消息
        message = (
            f"🔔 *A2A Hub 任务通知*\n\n"
            f"📋 任务ID: `{task_id}`\n"
            f"📂 类型: {task_type}\n"
            f"💬 内容:\n{input_text}\n\n"
            f"请处理此任务。完成后请通过 A2A Hub API 回报结果。"
        )

        url = f"{self.TELEGRAM_API}/bot{self.bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=TELEGRAM_API_TIMEOUT) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": self.chat_id,
                        "text": message,
                        "parse_mode": self.parse_mode,
                    },
                )
                resp.raise_for_status()
                result = resp.json()
                if result.get("ok"):
                    logger.info(
                        "telegram_bot.dispatch ok chat_id=%s task_id=%s msg_id=%s",
                        self.chat_id,
                        task_id,
                        result.get("message_id"),
                    )
                    return True
                else:
                    logger.error(
                        "telegram_bot.dispatch not_ok chat_id=%s task_id=%s desc=%s",
                        self.chat_id,
                        task_id,
                        result.get("description"),
                    )
                    return False
        except Exception as exc:
            logger.error(
                "telegram_bot.dispatch error chat_id=%s task_id=%s err=%s",
                self.chat_id,
                task_id,
                exc,
            )
            return False


def get_agent_dispatch_channel(agent: Agent) -> str:
    """从 Agent 的 config_json 中读取 dispatch_channel，默认 mqtt"""
    config = agent.config_json or {}
    return config.get("dispatch_channel", "mqtt")


def get_agent_dispatch_config(agent: Agent) -> dict[str, Any]:
    """从 Agent 的 config_json 中读取 dispatch_config"""
    config = agent.config_json or {}
    return config.get("dispatch_config", {})


async def dispatch_via_channel(
    agent: Agent,
    payload: dict[str, Any],
) -> tuple[bool, str]:
    """
    根据 Agent 的 dispatch_channel 选择投递通道。

    返回 (success: bool, channel: str)
    """
    channel = get_agent_dispatch_channel(agent)

    if channel == "webhook":
        config = get_agent_dispatch_config(agent)
        try:
            dispatcher = WebhookDispatcher(config)
            ok = await dispatcher.dispatch(payload)
            return ok, "webhook"
        except DispatcherError as exc:
            logger.error("webhook dispatcher init failed: %s", exc)
            return False, "webhook"

    elif channel == "telegram_bot":
        config = get_agent_dispatch_config(agent)
        try:
            dispatcher = TelegramBotDispatcher(config)
            ok = await dispatcher.dispatch(payload)
            return ok, "telegram_bot"
        except DispatcherError as exc:
            logger.error("telegram_bot dispatcher init failed: %s", exc)
            return False, "telegram_bot"

    else:
        # 默认 mqtt —— 由调用方（agent_link_service）处理
        return False, "mqtt"


def json_dumps(obj: Any) -> str:
    """简单 JSON 序列化，避免循环导入"""
    import json
    return json.dumps(obj, ensure_ascii=False)
