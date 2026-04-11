"""
计量服务：记录事件并提供简单汇总。
"""
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import MeteringEvent


class MeteringService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        tenant_id: str,
        event_type: str,
        metric_name: str,
        metric_value: int | float | Decimal = 1,
        unit: str = "count",
        task_id: str | None = None,
        agent_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> MeteringEvent:
        event = MeteringEvent(
            tenant_id=tenant_id,
            task_id=task_id,
            agent_id=agent_id,
            event_type=event_type,
            metric_name=metric_name,
            metric_value=metric_value,
            unit=unit,
            extra_json=extra or {},
        )
        self.db.add(event)
        return event

    async def summary(self, tenant_id: str) -> list[dict[str, Any]]:
        result = await self.db.execute(
            select(
                MeteringEvent.event_type,
                MeteringEvent.metric_name,
                func.sum(MeteringEvent.metric_value).label("total"),
            )
            .where(MeteringEvent.tenant_id == tenant_id)
            .group_by(MeteringEvent.event_type, MeteringEvent.metric_name)
            .order_by(MeteringEvent.event_type, MeteringEvent.metric_name)
        )
        return [
            {
                "event_type": row.event_type,
                "metric_name": row.metric_name,
                "total": float(row.total or 0),
            }
            for row in result
        ]
