"""
OpenClaw Gateway 适配：Agent 消息处理（HTTP 入口）。
"""
from dataclasses import dataclass
from typing import Any

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
    websocket: None = None
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class OpenClawGatewayBroker:
    def __init__(self):
        pass

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