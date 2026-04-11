"""
统一 Agent Link client SDK。

当前版本：
- 下行通过 MQTT 订阅 task.dispatch
- 上行继续复用 Hub HTTP 接口回传 task.update / transcript / approval
"""
import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import httpx


@dataclass
class AgentLinkBootstrap:
    agent_id: str
    tenant_id: str
    auth_token: str
    mqtt_broker_url: str
    mqtt_client_id: str
    mqtt_command_topic: str
    mqtt_username: str
    mqtt_password: str
    presence_url: str
    agent_message_url: str
    error_report_url: str
    qos: int = 1


class AgentLinkClient:
    def __init__(self, connect_url: str, on_task: Callable[[dict[str, Any]], Awaitable[None]]):
        self.connect_url = connect_url
        self.on_task = on_task
        self.bootstrap: AgentLinkBootstrap | None = None

    async def load_bootstrap(self) -> AgentLinkBootstrap:
        parsed = urlparse(self.connect_url)
        token = parse_qs(parsed.query).get("token", [None])[0]
        if not token:
            raise ValueError("connect_url 缺少 token")
        base = f"{parsed.scheme}://{parsed.netloc}"
        bootstrap_url = f"{base}/v1/openclaw/agents/bootstrap?token={token}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(bootstrap_url)
            response.raise_for_status()
            payload = response.json()["data"]
        self.bootstrap = AgentLinkBootstrap(
            agent_id=payload["agent_id"],
            tenant_id=payload["tenant_id"],
            auth_token=payload["auth_token"],
            mqtt_broker_url=payload["mqtt_broker_url"],
            mqtt_client_id=payload["mqtt_client_id"],
            mqtt_command_topic=payload["mqtt_command_topic"],
            mqtt_username=payload["mqtt_username"],
            mqtt_password=payload["mqtt_password"],
            presence_url=payload["presence_url"],
            agent_message_url=f"{base}/v1/agent-link/messages",
            error_report_url=f"{base}/v1/agent-link/errors",
            qos=payload.get("qos", 1),
        )
        return self.bootstrap

    async def send_presence(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.bootstrap:
            await self.load_bootstrap()
        assert self.bootstrap is not None
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.bootstrap.presence_url,
                headers={"Authorization": f"Bearer {self.bootstrap.auth_token}"},
                json={"status": "online", "metadata": metadata or {}},
            )
            response.raise_for_status()
            return response.json()["data"]

    async def send_agent_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.bootstrap:
            await self.load_bootstrap()
        assert self.bootstrap is not None
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                self.bootstrap.agent_message_url,
                headers={"Authorization": f"Bearer {self.bootstrap.auth_token}"},
                json={"payload": payload},
            )
            response.raise_for_status()
            return response.json()["data"]

    async def report_error(
        self,
        *,
        stage: str,
        summary: str,
        category: str = "runtime",
        detail: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self.bootstrap:
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    self.bootstrap.error_report_url,
                    headers={"Authorization": f"Bearer {self.bootstrap.auth_token}"},
                    json={
                        "stage": stage,
                        "summary": summary,
                        "category": category,
                        "detail": detail,
                        "metadata": metadata or {},
                    },
                )
        except Exception:
            return

    async def run_presence_loop(self, interval_seconds: int = 30) -> None:
        while True:
            try:
                await self.send_presence()
            except Exception as exc:
                await self.report_error(stage="presence", summary="presence 上报失败", detail=str(exc), category="runtime")
            await asyncio.sleep(interval_seconds)

    def run_mqtt(self) -> None:
        if not self.bootstrap:
            raise RuntimeError("bootstrap 未加载")
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:  # pragma: no cover - 依赖外部环境
            raise RuntimeError("缺少 paho-mqtt 依赖") from exc

        bs = self.bootstrap
        parsed = urlparse(bs.mqtt_broker_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 1883
        transport = "tcp"

        client = mqtt.Client(client_id=bs.mqtt_client_id, transport=transport)
        client.username_pw_set(bs.mqtt_username, bs.mqtt_password)

        def _on_connect(cli, userdata, flags, rc, properties=None):
            cli.subscribe(bs.mqtt_command_topic, qos=bs.qos)

        def _on_message(cli, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                asyncio.run(self.on_task(payload))
            except Exception as exc:
                asyncio.run(
                    self.report_error(
                        stage="mqtt_message",
                        summary="MQTT 消息处理失败",
                        detail=str(exc),
                        category="runtime",
                    )
                )

        client.on_connect = _on_connect
        client.on_message = _on_message
        client.connect(host, port, keepalive=60)
        client.loop_forever()


class OpenClawAgentAdapter:
    def __init__(self, client: AgentLinkClient, base_url: str):
        self.client = client
        self.base_url = base_url.rstrip("/")

    async def update_task(self, task_id: str, state: str, output_text: str | None = None, message_text: str | None = None):
        return await self.client.send_agent_message(
            {
                "type": "task.update",
                "task_id": task_id,
                "state": state,
                "output_text": output_text,
                "message_text": message_text,
            }
        )

    async def emit_transcript(self, session_key: str, event_id: str, text: str, task_type: str = "generic"):
        return await self.client.send_agent_message(
            {
                "type": "transcript",
                "session_key": session_key,
                "event_id": event_id,
                "text": text,
                "task_type": task_type,
            }
        )

    async def request_approval(self, task_id: str, external_key: str, reason: str, approver_user_id: str | None = None):
        return await self.client.send_agent_message(
            {
                "type": "approval",
                "task_id": task_id,
                "external_key": external_key,
                "reason": reason,
                "approver_user_id": approver_user_id,
            }
        )
