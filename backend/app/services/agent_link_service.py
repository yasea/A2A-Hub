"""
统一 Agent Link：下行分发、MQTT topic 规划、presence 维护。
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.core.security import create_access_token
from app.models.task import Task
from app.services.error_event_service import ErrorEventService
from app.services.mqtt_auth import tenant_mqtt_password, tenant_mqtt_username

logger = logging.getLogger(__name__)


@dataclass
class AgentLinkDispatchResult:
    dispatched: bool
    transport: str
    reason: str | None = None
    topic: str | None = None


class MqttPublisher:
    """以一次性 publish 方式向 MQTT broker 下发任务。"""

    def __init__(self):
        self._error: str | None = None

    def last_error(self) -> str | None:
        return self._error

    async def publish(self, topic: str, payload: dict[str, Any], username: str, password: str) -> bool:
        try:
            from paho.mqtt.publish import single
        except ImportError:
            self._error = "paho-mqtt not installed"
            return False

        parsed = urlparse(settings.MQTT_BROKER_URL)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 1883
        tls = parsed.scheme == "mqtts"
        try:
            single(
                topic,
                payload=json.dumps(payload, ensure_ascii=False),
                qos=1,
                hostname=host,
                port=port,
                auth={"username": username, "password": password},
                tls={} if tls else None,
                retain=False,
            )
            self._error = None
            return True
        except Exception as exc:  # pragma: no cover - 依赖外部 broker
            self._error = str(exc)
            return False


class AgentLinkService:
    def __init__(self, db: AsyncSession | None = None, publisher: MqttPublisher | None = None):
        self.db = db
        self.publisher = publisher or MqttPublisher()
        self._pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._presence: dict[tuple[str, str], dict[str, Any]] = {}

    def _pending_key(self, tenant_id: str, agent_id: str) -> str:
        return f"agent-link:pending:{tenant_id}:{agent_id}"

    def _presence_key(self, tenant_id: str, agent_id: str) -> str:
        return f"agent-link:presence:{tenant_id}:{agent_id}"

    async def _push_pending(self, tenant_id: str, agent_id: str, payload: dict[str, Any]) -> None:
        self._pending.setdefault((tenant_id, agent_id), []).append(payload)
        try:
            redis = get_redis()
            await redis.rpush(self._pending_key(tenant_id, agent_id), json.dumps(payload, ensure_ascii=False))
        except Exception:
            return

    async def _load_pending(self, tenant_id: str, agent_id: str) -> list[dict[str, Any]]:
        cached = list(self._pending.get((tenant_id, agent_id), []))
        try:
            redis = get_redis()
            raw = await redis.lrange(self._pending_key(tenant_id, agent_id), 0, -1)
            decoded = [json.loads(item) for item in raw]
            if decoded:
                self._pending[(tenant_id, agent_id)] = decoded
                return decoded
        except Exception:
            pass
        return cached

    async def _clear_pending(self, tenant_id: str, agent_id: str) -> None:
        self._pending.pop((tenant_id, agent_id), None)
        try:
            redis = get_redis()
            await redis.delete(self._pending_key(tenant_id, agent_id))
        except Exception:
            return

    def command_topic(self, tenant_id: str, agent_id: str) -> str:
        return f"{settings.MQTT_BASE_TOPIC}/{tenant_id}/agents/{agent_id}/commands"

    def client_id(self, tenant_id: str, agent_id: str) -> str:
        safe_agent = agent_id.replace(":", "_")
        return f"a2a_{tenant_id}_{safe_agent}"

    def transport_payload(self, tenant_id: str, agent_id: str, _auth_token: str) -> dict[str, Any]:
        mqtt_username = tenant_mqtt_username(tenant_id)
        mqtt_password = tenant_mqtt_password(tenant_id)
        return {
            "transport": settings.AGENT_LINK_TRANSPORT,
            "mqtt_broker_url": settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL,
            "mqtt_client_id": self.client_id(tenant_id, agent_id),
            "mqtt_command_topic": self.command_topic(tenant_id, agent_id),
            "presence_url": f"{settings.PUBLIC_BASE_URL}/v1/agent-link/presence",
            "qos": 1,
            "mqtt_username": mqtt_username,
            "mqtt_password": mqtt_password,
        }

    def build_agent_token(self, tenant_id: str, agent_id: str, subject: str | None) -> str:
        return create_access_token(
            subject=subject or agent_id,
            extra={
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "scope": "openclaw_gateway",
            },
        )

    async def dispatch_task(self, task: Task, auth_token: str) -> AgentLinkDispatchResult:
        if not task.target_agent_id:
            return AgentLinkDispatchResult(dispatched=False, transport=settings.AGENT_LINK_TRANSPORT, reason="missing_target_agent")

        topic = self.command_topic(task.tenant_id, task.target_agent_id)
        transport = self.transport_payload(task.tenant_id, task.target_agent_id, auth_token)
        payload = {
            "type": "task.dispatch",
            "task_id": task.task_id,
            "tenant_id": task.tenant_id,
            "context_id": task.context_id,
            "task_type": task.task_type,
            "input_text": task.input_text,
            "metadata": task.metadata_json,
            "trace_id": task.trace_id,
        }
        published = False
        if settings.AGENT_LINK_TRANSPORT == "mqtt":
            published = await self.publisher.publish(
                topic,
                payload,
                username=transport["mqtt_username"],
                password=transport["mqtt_password"],
            )
            logger.info(
                "agent_link.dispatch publish task_id=%s agent_id=%s topic=%s published=%s error=%s",
                task.task_id,
                task.target_agent_id,
                topic,
                published,
                self.publisher.last_error(),
            )
        if not published:
            await ErrorEventService.record_out_of_band(
                source_side="platform",
                stage="dispatch",
                category="mqtt",
                summary="MQTT 下发失败，任务已进入 pending 队列",
                tenant_id=task.tenant_id,
                agent_id=task.target_agent_id,
                detail=self.publisher.last_error(),
                payload={"task_id": task.task_id, "topic": topic},
            )
            await self._push_pending(task.tenant_id, task.target_agent_id, payload)
            return AgentLinkDispatchResult(
                dispatched=False,
                transport=settings.AGENT_LINK_TRANSPORT,
                reason=self.publisher.last_error() or "agent_not_connected",
                topic=topic,
            )
        return AgentLinkDispatchResult(dispatched=True, transport=settings.AGENT_LINK_TRANSPORT, topic=topic)

    async def heartbeat(self, tenant_id: str, agent_id: str, status: str, metadata: dict[str, Any] | None = None, auth_token: str | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.AGENT_LINK_PRESENCE_TTL_SECONDS)
        pending = await self._load_pending(tenant_id, agent_id)
        state = {
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "status": status,
            "metadata": metadata or {},
            "last_seen_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "pending_count": len(pending),
        }
        self._presence[(tenant_id, agent_id)] = state
        try:
            redis = get_redis()
            await redis.set(
                self._presence_key(tenant_id, agent_id),
                json.dumps(state, ensure_ascii=False),
                ex=settings.AGENT_LINK_PRESENCE_TTL_SECONDS,
            )
        except Exception:
            pass
        if auth_token and pending:
            flushed = 0
            for payload in pending:
                if settings.AGENT_LINK_TRANSPORT != "mqtt":
                    break
                ok = await self.publisher.publish(
                    self.command_topic(tenant_id, agent_id),
                    payload,
                    username=tenant_mqtt_username(tenant_id),
                    password=tenant_mqtt_password(tenant_id),
                )
                if not ok:
                    await ErrorEventService.record_out_of_band(
                        source_side="platform",
                        stage="presence_flush",
                        category="mqtt",
                        summary="pending 消息补发失败",
                        tenant_id=tenant_id,
                        agent_id=agent_id,
                        detail=self.publisher.last_error(),
                        payload={"pending_type": payload.get("type"), "topic": self.command_topic(tenant_id, agent_id)},
                    )
                    break
                flushed += 1
            if flushed == len(pending):
                await self._clear_pending(tenant_id, agent_id)
                state["pending_count"] = 0
        return state

    async def get_presence(self, tenant_id: str, agent_id: str) -> dict[str, Any] | None:
        state = self._presence.get((tenant_id, agent_id))
        if not state:
            try:
                redis = get_redis()
                raw = await redis.get(self._presence_key(tenant_id, agent_id))
                if raw:
                    state = json.loads(raw)
                    self._presence[(tenant_id, agent_id)] = state
            except Exception:
                state = None
        if not state:
            return None
        expires_at = datetime.fromisoformat(state["expires_at"])
        if expires_at < datetime.now(timezone.utc):
            self._presence.pop((tenant_id, agent_id), None)
            return None
        return state


agent_link_service = AgentLinkService()
