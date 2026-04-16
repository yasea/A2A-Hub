import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes_messages import create_and_dispatch_message_task
from app.models.service import ServicePublication, ServiceThread, ServiceThreadMessage
from app.models.task import TaskMessage
from app.schemas.message import MessagePart, MessageSendRequest
from app.services.audit_service import AuditService
from app.services.context_service import ContextService
from app.services.task_service import TaskService


class ServiceConversationError(ValueError):
    pass


class ServiceThreadNotFound(ServiceConversationError):
    pass


class ServiceThreadForbidden(ServiceConversationError):
    pass


class ServiceConversationService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def create_thread(
        self,
        publication: ServicePublication,
        consumer_tenant_id: str,
        initiator_agent_id: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> ServiceThread:
        self._ensure_publication_chat_allowed(publication)
        thread_id = f"sth_{uuid.uuid4().hex}"
        context_service = ContextService(self.db)
        provider_context = await context_service.create(
            tenant_id=publication.tenant_id,
            source_channel="service_thread",
            source_conversation_id=thread_id,
            owner_user_id=actor_id,
            title=title or publication.title,
            metadata={
                "service_id": publication.service_id,
                "consumer_tenant_id": consumer_tenant_id,
                **(metadata or {}),
            },
            actor_id=actor_id,
        )
        await self.db.flush()
        thread = ServiceThread(
            thread_id=thread_id,
            service_id=publication.service_id,
            consumer_tenant_id=consumer_tenant_id,
            provider_tenant_id=publication.tenant_id,
            provider_context_id=provider_context.context_id,
            initiator_agent_id=initiator_agent_id,
            handler_agent_id=publication.handler_agent_id,
            status="OPEN",
            title=title or publication.title,
            metadata_json=metadata or {},
        )
        self.db.add(thread)
        await self.db.flush()
        await context_service.add_participant(provider_context.context_id, "agent", publication.handler_agent_id, role="handler")
        await context_service.add_participant(provider_context.context_id, "system", consumer_tenant_id, role="consumer_tenant")
        await self.audit.log(
            consumer_tenant_id,
            "service_thread.create",
            "service_thread",
            thread.thread_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            payload={"service_id": publication.service_id, "provider_tenant_id": publication.tenant_id},
        )
        return thread

    async def get_thread(self, thread_id: str, tenant_id: str) -> ServiceThread | None:
        result = await self.db.execute(select(ServiceThread).where(ServiceThread.thread_id == thread_id))
        thread = result.scalar_one_or_none()
        if not thread:
            return None
        if tenant_id not in {thread.consumer_tenant_id, thread.provider_tenant_id}:
            return None
        return thread

    async def list_threads(self, tenant_id: str) -> list[ServiceThread]:
        result = await self.db.execute(
            select(ServiceThread).where(
                (ServiceThread.consumer_tenant_id == tenant_id) | (ServiceThread.provider_tenant_id == tenant_id)
            )
        )
        return list(result.scalars().all())

    async def send_consumer_message(
        self,
        thread: ServiceThread,
        tenant: dict[str, Any],
        text: str,
        initiator_agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[ServiceThreadMessage, str]:
        if tenant["tenant_id"] != thread.consumer_tenant_id:
            raise ServiceThreadForbidden("只有会话发起租户可以继续发送消息")
        if thread.status != "OPEN":
            raise ServiceConversationError("thread 已关闭")
        user_message = await self._append_thread_message(
            thread_id=thread.thread_id,
            role="user",
            sender_tenant_id=tenant["tenant_id"],
            sender_agent_id=initiator_agent_id or tenant.get("agent_id"),
            content_text=text,
            linked_task_id=None,
            metadata=metadata or {},
        )
        provider_tenant = {
            "tenant_id": thread.provider_tenant_id,
            "sub": tenant.get("sub"),
            "token_type": tenant.get("token_type", "user"),
            "scopes": tenant.get("scopes", []),
        }
        response = await create_and_dispatch_message_task(
            req=MessageSendRequest(
                context_id=thread.provider_context_id,
                target_agent_id=thread.handler_agent_id,
                parts=[MessagePart(text=text)],
                metadata={
                    "service_id": thread.service_id,
                    "service_thread_id": thread.thread_id,
                    "consumer_tenant_id": thread.consumer_tenant_id,
                    **(metadata or {}),
                },
            ),
            db=self.db,
            tenant=provider_tenant,
            idempotency_key=None,
            initiator_agent_id=initiator_agent_id or tenant.get("agent_id"),
            source_system="service_thread",
        )
        user_message.linked_task_id = response.task_id
        user_message.metadata_json = {
            **(user_message.metadata_json or {}),
            "linked_task_id": response.task_id,
        }
        await self.db.execute(
            update(ServiceThreadMessage)
            .where(ServiceThreadMessage.message_id == user_message.message_id)
            .values(linked_task_id=response.task_id, metadata_json=user_message.metadata_json)
        )
        await self._touch_thread(thread.thread_id)
        return user_message, response.task_id

    async def list_messages(self, thread: ServiceThread, tenant_id: str) -> list[ServiceThreadMessage]:
        if tenant_id not in {thread.consumer_tenant_id, thread.provider_tenant_id}:
            raise ServiceThreadForbidden("无权访问该 thread")
        await self.sync_assistant_messages(thread)
        result = await self.db.execute(
            select(ServiceThreadMessage)
            .where(ServiceThreadMessage.thread_id == thread.thread_id)
            .order_by(ServiceThreadMessage.seq_no.asc())
        )
        return list(result.scalars().all())

    async def sync_assistant_messages(self, thread: ServiceThread) -> int:
        result = await self.db.execute(
            select(ServiceThreadMessage)
            .where(ServiceThreadMessage.thread_id == thread.thread_id)
            .order_by(ServiceThreadMessage.seq_no.asc())
        )
        existing = list(result.scalars().all())
        mirrored_ids = {
            str((item.metadata_json or {}).get("task_message_id"))
            for item in existing
            if (item.metadata_json or {}).get("task_message_id")
        }
        task_service = TaskService(self.db)
        created = 0
        for item in existing:
            task_id = item.linked_task_id or (item.metadata_json or {}).get("linked_task_id")
            if not task_id:
                continue
            task = await task_service.get(task_id, thread.provider_tenant_id)
            if not task:
                continue
            task_messages = await task_service.list_messages(task_id, thread.provider_tenant_id)
            for task_message in task_messages:
                if task_message.role != "assistant":
                    continue
                if task_message.message_id in mirrored_ids:
                    continue
                await self._append_thread_message(
                    thread_id=thread.thread_id,
                    role="assistant",
                    sender_tenant_id=thread.provider_tenant_id,
                    sender_agent_id=thread.handler_agent_id,
                    content_text=task_message.content_text or "",
                    linked_task_id=task_id,
                    metadata={
                        "task_id": task_id,
                        "task_message_id": task_message.message_id,
                        "provider_context_id": thread.provider_context_id,
                    },
                )
                mirrored_ids.add(task_message.message_id)
                created += 1
        if created:
            await self._touch_thread(thread.thread_id)
        return created

    async def _append_thread_message(
        self,
        thread_id: str,
        role: str,
        sender_tenant_id: str | None,
        sender_agent_id: str | None,
        content_text: str,
        linked_task_id: str | None,
        metadata: dict[str, Any],
    ) -> ServiceThreadMessage:
        await self.db.execute(
            select(ServiceThread.thread_id).where(ServiceThread.thread_id == thread_id).with_for_update()
        )
        result = await self.db.execute(
            select(func.coalesce(func.max(ServiceThreadMessage.seq_no), 0)).where(ServiceThreadMessage.thread_id == thread_id)
        )
        max_seq = int(result.scalar() or 0)
        message = ServiceThreadMessage(
            message_id=f"stmsg_{uuid.uuid4().hex}",
            thread_id=thread_id,
            role=role,
            sender_tenant_id=sender_tenant_id,
            sender_agent_id=sender_agent_id,
            linked_task_id=linked_task_id,
            content_text=content_text,
            seq_no=max_seq + 1,
            metadata_json=metadata,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def _touch_thread(self, thread_id: str) -> None:
        now = datetime.now(timezone.utc)
        await self.db.execute(
            update(ServiceThread)
            .where(ServiceThread.thread_id == thread_id)
            .values(updated_at=now, last_activity_at=now)
        )

    @staticmethod
    def _ensure_publication_chat_allowed(publication: ServicePublication) -> None:
        if publication.status != "ACTIVE":
            raise ServiceConversationError("service 未启用")
        if publication.contact_policy == "deny":
            raise ServiceConversationError("service 当前拒绝对话")
        if publication.contact_policy != "auto_accept":
            raise ServiceConversationError("当前版本仅支持 auto_accept service")
        if not publication.allow_agent_initiated_chat:
            raise ServiceConversationError("service 不允许发起即时对话")
