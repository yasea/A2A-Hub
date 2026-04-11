"""
Agent Link / OpenClaw 接入链路错误观测。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.db import AsyncSessionLocal
from app.models.agent_link_error import AgentLinkErrorEvent

logger = get_logger(__name__)


class ErrorEventService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        source_side: str,
        stage: str,
        category: str,
        summary: str,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        detail: str | None = None,
        status_code: int | None = None,
        request_path: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> AgentLinkErrorEvent:
        event = AgentLinkErrorEvent(
            tenant_id=tenant_id,
            agent_id=agent_id,
            source_side=source_side,
            stage=stage,
            category=category,
            summary=summary[:255],
            detail=(detail or None),
            status_code=status_code,
            request_path=request_path,
            payload_json=payload or {},
        )
        self.db.add(event)
        await self.db.flush()
        return event

    async def list_recent(
        self,
        *,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        source_side: str | None = None,
        limit: int = 50,
    ) -> list[AgentLinkErrorEvent]:
        stmt = select(AgentLinkErrorEvent).order_by(desc(AgentLinkErrorEvent.created_at), desc(AgentLinkErrorEvent.error_id))
        if agent_id:
            stmt = stmt.where(AgentLinkErrorEvent.agent_id == agent_id)
        if tenant_id:
            stmt = stmt.where(AgentLinkErrorEvent.tenant_id == tenant_id)
        if source_side:
            stmt = stmt.where(AgentLinkErrorEvent.source_side == source_side)
        stmt = stmt.limit(max(1, min(limit, 200)))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def record_out_of_band(
        cls,
        *,
        source_side: str,
        stage: str,
        category: str,
        summary: str,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        detail: str | None = None,
        status_code: int | None = None,
        request_path: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            async with AsyncSessionLocal() as db:
                svc = cls(db)
                await svc.record(
                    source_side=source_side,
                    stage=stage,
                    category=category,
                    summary=summary,
                    tenant_id=tenant_id,
                    agent_id=agent_id,
                    detail=detail,
                    status_code=status_code,
                    request_path=request_path,
                    payload=payload,
                )
                await db.commit()
        except Exception as exc:  # pragma: no cover - 监控落库失败不能反向影响主链路
            logger.warning(
                "error_event.record_failed",
                source_side=source_side,
                stage=stage,
                category=category,
                summary=summary,
                error=str(exc),
            )
