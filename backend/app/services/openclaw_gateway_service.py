"""
OpenClaw Gateway 适配：Agent 长连接、任务下发与事件回传。
"""
import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from app.core.security import decode_access_token
from app.models.task import Task
from app.services.openclaw_service import OpenClawService
from app.services.task_service import InvalidTaskTransitionError, TaskService


OPENCLAW_AGENT_MESSAGE_TYPES = [
    "hello",
    "ping",
    "task.ack",
    "transcript",
    "approval",
    "task.update",
]


@dataclass
class OpenClawConnection:
    connection_id: str
    tenant_id: str
    agent_id: str
    websocket: WebSocket | None
    metadata: dict[str, Any]


class OpenClawGatewayBroker:
    def __init__(self):
        self._connections: dict[tuple[str, str], OpenClawConnection] = {}
        self._pending: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        websocket: WebSocket,
        token: str,
    ) -> OpenClawConnection:
        payload = decode_access_token(token)
        if payload.get("scope") != "openclaw_gateway":
            raise ValueError("OpenClaw gateway token scope 非法")
        tenant_id = payload.get("tenant_id")
        agent_id = payload.get("agent_id")
        if not tenant_id or not agent_id:
            raise ValueError("OpenClaw gateway token 缺少 tenant_id 或 agent_id")

        connection = OpenClawConnection(
            connection_id=f"ocws_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_id,
            agent_id=agent_id,
            websocket=websocket,
            metadata={"sub": payload.get("sub")},
        )
        async with self._lock:
            self._connections[(tenant_id, agent_id)] = connection
        return connection

    async def unregister(self, tenant_id: str, agent_id: str) -> None:
        async with self._lock:
            self._connections.pop((tenant_id, agent_id), None)

    def get_connection(self, tenant_id: str, agent_id: str) -> OpenClawConnection | None:
        return self._connections.get((tenant_id, agent_id))

    async def send_json(self, tenant_id: str, agent_id: str, payload: dict[str, Any]) -> OpenClawConnection | None:
        connection = self.get_connection(tenant_id, agent_id)
        if not connection:
            return None
        if connection.websocket is None:
            return connection
        await connection.websocket.send_json(payload)
        return connection

    async def queue_payload(self, tenant_id: str, agent_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._pending.setdefault((tenant_id, agent_id), []).append(payload)

    async def flush_pending(self, tenant_id: str, agent_id: str) -> int:
        connection = self.get_connection(tenant_id, agent_id)
        if not connection:
            return 0
        async with self._lock:
            payloads = self._pending.pop((tenant_id, agent_id), [])
        for payload in payloads:
            if connection.websocket is not None:
                await connection.websocket.send_json(payload)
        return len(payloads)

    async def dispatch_task(self, task: Task) -> OpenClawConnection | None:
        if not task.target_agent_id:
            return None
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
        connection = await self.send_json(task.tenant_id, task.target_agent_id, payload)
        if connection is None:
            await self.queue_payload(task.tenant_id, task.target_agent_id, payload)
        return connection

    async def handle_agent_message(
        self,
        db,
        connection: OpenClawConnection,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        msg_type = payload.get("type")
        if msg_type == "hello":
            return {
                "type": "hello.ack",
                "connection_id": connection.connection_id,
                "agent_id": connection.agent_id,
                "tenant_id": connection.tenant_id,
                "message_types": OPENCLAW_AGENT_MESSAGE_TYPES,
            }

        if msg_type == "ping":
            return {"type": "pong"}

        if msg_type == "task.ack":
            task_service = TaskService(db)
            task = await task_service.get(payload["task_id"], connection.tenant_id)
            if not task:
                return {"type": "error", "code": "TASK_NOT_FOUND", "message": "task not found"}
            if task.state == "ROUTING":
                task = await task_service.update_state(
                    task_id=payload["task_id"],
                    tenant_id=connection.tenant_id,
                    new_state="WORKING",
                    reason=payload.get("reason") or "agent_acknowledged",
                    actor_type="agent",
                    actor_id=connection.agent_id,
                )
            return {"type": "task.ack.ack", "task_id": task.task_id, "state": task.state}

        if msg_type == "transcript":
            result = await OpenClawService(db).ingest_transcript(
                tenant_id=connection.tenant_id,
                session_key=payload["session_key"],
                event_id=payload["event_id"],
                text=payload["text"],
                sender_type=payload.get("sender_type", "agent"),
                sender_id=connection.agent_id,
                task_type=payload.get("task_type", "generic"),
                metadata=payload.get("metadata") or {},
            )
            return {"type": "transcript.ack", **result}

        if msg_type == "approval":
            approval = await OpenClawService(db).ingest_approval_request(
                tenant_id=connection.tenant_id,
                task_id=payload["task_id"],
                reason=payload["reason"],
                external_key=payload["external_key"],
                requested_by=connection.agent_id,
                approver_user_id=payload.get("approver_user_id"),
                metadata=payload.get("metadata") or {},
            )
            return {
                "type": "approval.ack",
                "approval_id": approval.approval_id,
                "task_id": approval.task_id,
                "status": approval.status,
            }

        if msg_type == "task.update":
            task_service = TaskService(db)
            try:
                current_task = await task_service.get(payload["task_id"], connection.tenant_id)
                if not current_task:
                    return {"type": "error", "code": "TASK_NOT_FOUND", "message": "task not found"}
                if current_task.state == "ROUTING":
                    await task_service.update_state(
                        task_id=payload["task_id"],
                        tenant_id=connection.tenant_id,
                        new_state="WORKING",
                        reason="implicit_agent_ack",
                        actor_type="agent",
                        actor_id=connection.agent_id,
                    )
                task = await task_service.update_state(
                    task_id=payload["task_id"],
                    tenant_id=connection.tenant_id,
                    new_state=payload["state"],
                    reason=payload.get("reason"),
                    actor_type="agent",
                    actor_id=connection.agent_id,
                    output_text=payload.get("output_text"),
                )
            except InvalidTaskTransitionError as exc:
                return {"type": "error", "code": "INVALID_TASK_TRANSITION", "message": str(exc)}

            if payload.get("message_text"):
                await task_service.append_message(
                    task_id=task.task_id,
                    context_id=task.context_id,
                    role="assistant",
                    content_text=payload["message_text"],
                    source_agent_id=connection.agent_id,
                    source_message_id=payload.get("message_id"),
                    metadata=payload.get("metadata") or {},
                )
            return {
                "type": "task.update.ack",
                "task_id": task.task_id,
                "state": task.state,
            }

        return {"type": "error", "code": "UNSUPPORTED_MESSAGE", "message": f"unsupported type: {msg_type}"}


openclaw_gateway_broker = OpenClawGatewayBroker()
