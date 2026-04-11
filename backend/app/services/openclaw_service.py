"""
OpenClaw transcript 与审批事件映射。
"""
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.context import Context
from app.models.task import TaskMessage
from app.services.approval_service import ApprovalService
from app.services.context_service import ContextService
from app.services.metering_service import MeteringService
from app.services.task_service import TaskService


class OpenClawService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.contexts = ContextService(db)
        self.tasks = TaskService(db)
        self.approvals = ApprovalService(db)
        self.metering = MeteringService(db)

    async def get_or_create_context(
        self,
        tenant_id: str,
        session_key: str,
        title: str | None = None,
        owner_user_id: str | None = None,
    ) -> str:
        result = await self.db.execute(
            select(Context).where(
                Context.tenant_id == tenant_id,
                Context.source_channel == "openclaw",
                Context.source_conversation_id == session_key,
            )
        )
        context = result.scalar_one_or_none()
        if context:
            return context.context_id

        context = await self.contexts.create(
            tenant_id=tenant_id,
            source_channel="openclaw",
            source_conversation_id=session_key,
            owner_user_id=owner_user_id,
            title=title or "OpenClaw 会话",
            actor_id=owner_user_id,
        )
        return context.context_id

    async def ingest_transcript(
        self,
        tenant_id: str,
        session_key: str,
        event_id: str,
        text: str,
        sender_type: str,
        sender_id: str | None = None,
        task_type: str = "generic",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_id = await self.get_or_create_context(tenant_id, session_key, owner_user_id=sender_id)
        task = await self.tasks.create_task(
            tenant_id=tenant_id,
            context_id=context_id,
            input_text=text,
            task_type=task_type,
            source_system="openclaw",
            source_message_id=event_id,
            idempotency_key=f"openclaw:{session_key}:{event_id}",
            initiator_agent_id=sender_id if sender_type == "agent" else None,
            metadata=metadata or {},
            actor_id=sender_id,
        )

        result = await self.db.execute(
            select(TaskMessage).where(
                TaskMessage.task_id == task.task_id,
                TaskMessage.source_message_id == event_id,
            )
        )
        if not result.scalar_one_or_none():
            await self.tasks.append_message(
                task_id=task.task_id,
                context_id=context_id,
                role="assistant" if sender_type == "agent" else "user",
                content_text=text,
                source_agent_id=sender_id if sender_type == "agent" else None,
                source_message_id=event_id,
                metadata={"source": "openclaw", **(metadata or {})},
            )

        await self.metering.record(
            tenant_id=tenant_id,
            task_id=task.task_id,
            agent_id=sender_id,
            event_type="api_call",
            metric_name="request_count",
            metric_value=1,
            extra={"source": "openclaw_transcript"},
        )
        return {"context_id": context_id, "task_id": task.task_id, "state": task.state}

    async def ingest_approval_request(
        self,
        tenant_id: str,
        task_id: str,
        reason: str,
        external_key: str,
        requested_by: str | None = None,
        approver_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return await self.approvals.create(
            tenant_id=tenant_id,
            task_id=task_id,
            approver_user_id=approver_user_id,
            requested_by=requested_by,
            reason=reason,
            external_key=external_key,
            metadata=metadata,
        )
