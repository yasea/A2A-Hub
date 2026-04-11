"""
AuditService：关键写操作自动写审计日志
"""
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        tenant_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """写入一条审计日志"""
        entry = AuditLog(
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload_json=payload or {},
            trace_id=trace_id,
            request_id=request_id,
        )
        self.db.add(entry)
        # 不单独 commit，由外层 session 统一提交
