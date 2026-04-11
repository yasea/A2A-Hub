"""
ContextService：会话容器的创建、查询、关闭
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.context import Context, ContextParticipant
from app.services.audit_service import AuditService


class ContextService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def create(
        self,
        tenant_id: str,
        source_channel: str | None = None,
        source_conversation_id: str | None = None,
        owner_user_id: str | None = None,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> Context:
        """创建新 context"""
        context = Context(
            context_id=f"ctx_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            source_channel=source_channel,
            source_conversation_id=source_conversation_id,
            status="OPEN",
            title=title,
            metadata_json=metadata or {},
        )
        self.db.add(context)
        await self.audit.log(
            tenant_id=tenant_id,
            action="context.create",
            resource_type="context",
            resource_id=context.context_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
        )
        return context

    async def get(self, context_id: str, tenant_id: str) -> Context | None:
        """查询 context（租户隔离）"""
        result = await self.db.execute(
            select(Context).where(
                Context.context_id == context_id,
                Context.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def touch(self, context_id: str) -> None:
        """更新最近活跃时间"""
        await self.db.execute(
            update(Context)
            .where(Context.context_id == context_id)
            .values(last_activity_at=datetime.now(timezone.utc))
        )

    async def close(self, context_id: str, tenant_id: str, actor_id: str | None = None) -> None:
        """关闭 context"""
        await self.db.execute(
            update(Context)
            .where(Context.context_id == context_id, Context.tenant_id == tenant_id)
            .values(status="CLOSED")
        )
        await self.audit.log(
            tenant_id=tenant_id,
            action="context.close",
            resource_type="context",
            resource_id=context_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
        )

    async def add_participant(
        self,
        context_id: str,
        participant_type: str,
        participant_id: str,
        role: str | None = None,
    ) -> ContextParticipant:
        """添加参与者（已存在则忽略）"""
        # 检查是否已存在
        result = await self.db.execute(
            select(ContextParticipant).where(
                ContextParticipant.context_id == context_id,
                ContextParticipant.participant_type == participant_type,
                ContextParticipant.participant_id == participant_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        participant = ContextParticipant(
            context_id=context_id,
            participant_type=participant_type,
            participant_id=participant_id,
            role=role,
        )
        self.db.add(participant)
        return participant
