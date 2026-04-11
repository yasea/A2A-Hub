"""
Rocket.Chat 入站与房间绑定。
"""
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import RcRoomContextBinding
from app.models.task import Task
from app.services.context_service import ContextService
from app.services.agent_link_service import agent_link_service
from app.services.delivery_service import DeliveryService
from app.services.metering_service import MeteringService
from app.services.routing_engine import RoutingEngine, RoutingError
from app.services.task_service import TaskService


class RocketChatService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.contexts = ContextService(db)
        self.deliveries = DeliveryService(db)
        self.metering = MeteringService(db)
        self.tasks = TaskService(db)

    async def get_or_create_context(
        self,
        tenant_id: str,
        room_id: str,
        server_url: str | None,
        title: str | None = None,
        owner_user_id: str | None = None,
    ) -> str:
        result = await self.db.execute(
            select(RcRoomContextBinding).where(
                RcRoomContextBinding.tenant_id == tenant_id,
                RcRoomContextBinding.rc_room_id == room_id,
            )
        )
        binding = result.scalar_one_or_none()
        if binding:
            return binding.context_id

        context = await self.contexts.create(
            tenant_id=tenant_id,
            source_channel="rocket_chat",
            source_conversation_id=room_id,
            owner_user_id=owner_user_id,
            title=title,
            metadata={"server_url": server_url} if server_url else {},
            actor_id=owner_user_id,
        )
        self.db.add(
            RcRoomContextBinding(
                tenant_id=tenant_id,
                rc_room_id=room_id,
                rc_server_url=server_url,
                context_id=context.context_id,
            )
        )
        return context.context_id

    async def handle_incoming_message(
        self,
        tenant_id: str,
        room_id: str,
        text: str,
        sender_id: str,
        sender_name: str | None = None,
        server_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_id = await self.get_or_create_context(
            tenant_id=tenant_id,
            room_id=room_id,
            server_url=server_url,
            title=sender_name or "Rocket.Chat 会话",
            owner_user_id=sender_id,
        )
        task = await self.tasks.create_task(
            tenant_id=tenant_id,
            context_id=context_id,
            input_text=text,
            source_system="rocket_chat",
            source_message_id=(metadata or {}).get("message_id"),
            metadata=metadata or {},
            actor_id=sender_id,
        )
        if getattr(task, "_is_newly_created", False):
            dispatch_target_agent_id: str | None = None
            await self.tasks.append_message(
                task_id=task.task_id,
                context_id=context_id,
                role="user",
                content_text=text,
                metadata={"source": "rocket_chat", **(metadata or {})},
            )
            routing = RoutingEngine(self.db)
            try:
                await self.tasks.update_state(task.task_id, "ROUTING", tenant_id, actor_type="system")
                target_agent_id = await routing.route(task)
                await self.db.execute(
                    update(Task)
                    .where(Task.task_id == task.task_id)
                    .values(target_agent_id=target_agent_id)
                )
                task.target_agent_id = target_agent_id
                if target_agent_id.startswith("openclaw:"):
                    dispatch_target_agent_id = target_agent_id
            except RoutingError:
                await self.tasks.update_state(
                    task.task_id,
                    "FAILED",
                    tenant_id,
                    reason="路由失败：无可用 Agent",
                    actor_type="system",
                )
        await self.deliveries.enqueue(
            tenant_id=tenant_id,
            task_id=task.task_id,
            target_channel="rocket_chat",
            target_ref={"room_id": room_id, "server_url": server_url},
            payload={"type": "task_summary", "task_id": task.task_id, "text": text, "state": task.state},
            idempotency_key=f"rc-summary:{task.task_id}",
        )
        await self.metering.record(
            tenant_id=tenant_id,
            task_id=task.task_id,
            event_type="api_call",
            metric_name="request_count",
            metric_value=1,
            extra={"source": "rocket_chat"},
        )
        if getattr(task, "_is_newly_created", False):
            await self.db.commit()
            if dispatch_target_agent_id:
                auth_token = agent_link_service.build_agent_token(tenant_id, dispatch_target_agent_id, sender_id)
                await agent_link_service.dispatch_task(task, auth_token)
        return {"context_id": context_id, "task_id": task.task_id, "state": task.state}
