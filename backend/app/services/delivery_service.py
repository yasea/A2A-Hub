"""
出站投递、重试与 DLQ。
"""
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.delivery import Delivery
from app.services.audit_service import AuditService
from app.services.metering_service import MeteringService


class DeliveryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)
        self.metering = MeteringService(db)

    async def enqueue(
        self,
        tenant_id: str,
        target_channel: str,
        target_ref: dict[str, Any],
        payload: dict[str, Any],
        task_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
    ) -> Delivery:
        if idempotency_key:
            existing = await self.db.execute(
                select(Delivery).where(
                    Delivery.tenant_id == tenant_id,
                    Delivery.idempotency_key == idempotency_key,
                )
            )
            delivery = existing.scalar_one_or_none()
            if delivery:
                return delivery

        delivery = Delivery(
            tenant_id=tenant_id,
            task_id=task_id,
            target_channel=target_channel,
            target_ref=target_ref,
            payload=payload,
            status="PENDING",
            attempt_count=0,
            max_attempts=max_attempts or settings.DELIVERY_MAX_ATTEMPTS,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
        self.db.add(delivery)
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            action="delivery.enqueue",
            resource_type="delivery",
            resource_id=str(delivery.delivery_id),
            payload={"target_channel": target_channel, "task_id": task_id},
            trace_id=trace_id,
        )
        return delivery

    async def get(self, delivery_id: str, tenant_id: str | None = None) -> Delivery | None:
        query = select(Delivery).where(Delivery.delivery_id == UUID(delivery_id))
        if tenant_id:
            query = query.where(Delivery.tenant_id == tenant_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def list_dead(self, tenant_id: str) -> list[Delivery]:
        result = await self.db.execute(
            select(Delivery)
            .where(Delivery.tenant_id == tenant_id, Delivery.status == "DEAD")
            .order_by(Delivery.created_at.desc())
        )
        return list(result.scalars().all())

    async def process_due(self, tenant_id: str | None = None, limit: int = 20) -> list[Delivery]:
        now = datetime.now(timezone.utc)
        query = (
            select(Delivery)
            .where(
                Delivery.status.in_(["PENDING", "FAILED"]),
                (Delivery.next_retry_at.is_(None) | (Delivery.next_retry_at <= now)),
            )
            .order_by(Delivery.created_at)
            .limit(limit)
        )
        if tenant_id:
            query = query.where(Delivery.tenant_id == tenant_id)

        result = await self.db.execute(query)
        deliveries = list(result.scalars().all())
        processed: list[Delivery] = []
        for delivery in deliveries:
            try:
                processed.append(await self.process_delivery(delivery))
            except Exception as exc:
                processed.append(await self._force_mark_dead(delivery, f"处理异常: {exc}"))
        return processed

    async def replay_dead(self, delivery_id: str, tenant_id: str) -> Delivery:
        delivery = await self.get(delivery_id, tenant_id)
        if not delivery:
            raise ValueError(f"Delivery {delivery_id} 不存在")
        await self.db.execute(
            update(Delivery)
            .where(
                Delivery.delivery_id == UUID(delivery_id),
                Delivery.tenant_id == tenant_id,
            )
            .values(status="PENDING", next_retry_at=None, dead_letter_reason=None, last_error=None)
        )
        delivery.status = "PENDING"
        delivery.next_retry_at = None
        delivery.dead_letter_reason = None
        delivery.last_error = None
        return delivery

    async def process_delivery(self, delivery: Delivery) -> Delivery:
        await self.db.execute(
            update(Delivery)
            .where(Delivery.delivery_id == delivery.delivery_id)
            .values(status="SENDING")
        )
        delivery.status = "SENDING"
        try:
            await self._dispatch(delivery)
        except Exception as exc:
            try:
                return await self._mark_failed(delivery, str(exc))
            except Exception as inner_exc:
                return await self._force_mark_dead(delivery, f"失败处理异常: {inner_exc}")

        await self.db.execute(
            update(Delivery)
            .where(Delivery.delivery_id == delivery.delivery_id)
            .values(
                status="DELIVERED",
                attempt_count=delivery.attempt_count + 1,
                next_retry_at=None,
                last_error=None,
            )
        )
        delivery.status = "DELIVERED"
        delivery.attempt_count += 1
        delivery.next_retry_at = None
        delivery.last_error = None
        await self._safe_record_side_effects(
            tenant_id=delivery.tenant_id,
            action="delivery.delivered",
            delivery=delivery,
            payload={"target_channel": delivery.target_channel},
        )
        return delivery

    async def _mark_failed(self, delivery: Delivery, error: str) -> Delivery:
        attempt_count = delivery.attempt_count + 1
        values: dict[str, Any] = {
            "attempt_count": attempt_count,
            "last_error": error,
        }
        if attempt_count >= delivery.max_attempts:
            values.update(
                {
                    "status": "DEAD",
                    "next_retry_at": None,
                    "dead_letter_reason": error,
                }
            )
            delivery.status = "DEAD"
            delivery.dead_letter_reason = error
        else:
            delay_index = min(attempt_count - 1, len(settings.DELIVERY_RETRY_DELAYS) - 1)
            values.update(
                {
                    "status": "FAILED",
                    "next_retry_at": datetime.now(timezone.utc) + timedelta(seconds=settings.DELIVERY_RETRY_DELAYS[delay_index]),
                }
            )
            delivery.status = "FAILED"
            delivery.next_retry_at = values["next_retry_at"]

        await self.db.execute(
            update(Delivery)
            .where(Delivery.delivery_id == delivery.delivery_id)
            .values(**values)
        )
        delivery.attempt_count = attempt_count
        delivery.last_error = error
        await self._safe_record_side_effects(
            tenant_id=delivery.tenant_id,
            action="delivery.failed" if delivery.status != "DEAD" else "delivery.dead",
            delivery=delivery,
            payload={"error": error, "attempt_count": attempt_count},
        )
        return delivery

    async def _dispatch(self, delivery: Delivery) -> None:
        simulate = delivery.target_ref.get("simulate") or delivery.payload.get("simulate")
        if simulate == "fail":
            raise RuntimeError("模拟投递失败")

    async def _force_mark_dead(self, delivery: Delivery, error: str) -> Delivery:
        attempt_count = (delivery.attempt_count or 0) + 1
        try:
            await self.db.execute(
                update(Delivery)
                .where(Delivery.delivery_id == delivery.delivery_id)
                .values(
                    status="DEAD",
                    attempt_count=attempt_count,
                    next_retry_at=None,
                    last_error=error,
                    dead_letter_reason=error,
                )
            )
        except Exception:
            pass
        delivery.status = "DEAD"
        delivery.attempt_count = attempt_count
        delivery.next_retry_at = None
        delivery.last_error = error
        delivery.dead_letter_reason = error
        await self._safe_record_side_effects(
            tenant_id=delivery.tenant_id,
            action="delivery.dead",
            delivery=delivery,
            payload={"error": error, "attempt_count": attempt_count, "forced": True},
        )
        return delivery

    async def _safe_record_side_effects(
        self,
        tenant_id: str,
        action: str,
        delivery: Delivery,
        payload: dict[str, Any],
    ) -> None:
        try:
            await self.audit.log(
                tenant_id=tenant_id,
                action=action,
                resource_type="delivery",
                resource_id=str(delivery.delivery_id),
                payload=payload,
                trace_id=delivery.trace_id,
            )
        except Exception:
            pass

        try:
            await self.metering.record(
                tenant_id=tenant_id,
                event_type="delivery",
                metric_name="request_count",
                metric_value=1,
                task_id=delivery.task_id,
                extra={"target_channel": delivery.target_channel, "status": delivery.status},
            )
        except Exception:
            pass
