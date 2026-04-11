"""
审批流：创建审批、解决审批、超时过期。
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import Approval
from app.models.task import Task
from app.services.audit_service import AuditService
from app.services.delivery_service import DeliveryService
from app.services.metering_service import MeteringService
from app.services.task_service import TaskService


class ApprovalService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)
        self.delivery = DeliveryService(db)
        self.metering = MeteringService(db)
        self.task_service = TaskService(db)

    async def create(
        self,
        tenant_id: str,
        task_id: str,
        approver_user_id: str | None,
        requested_by: str | None,
        reason: str,
        context_id: str | None = None,
        external_key: str | None = None,
        metadata: dict | None = None,
    ) -> Approval:
        task = await self.task_service.get(task_id, tenant_id)
        if not task:
            raise ValueError(f"Task {task_id} 不存在")

        if task.state != "AUTH_REQUIRED":
            await self.task_service.update_state(
                task_id=task_id,
                new_state="AUTH_REQUIRED",
                tenant_id=tenant_id,
                reason=reason,
                actor_type="system",
                actor_id=requested_by,
            )

        approval = Approval(
            approval_id=f"appr_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            task_id=task_id,
            context_id=context_id or task.context_id,
            status="PENDING",
            approver_user_id=approver_user_id,
            requested_by=requested_by,
            reason=reason,
            external_key=external_key,
            metadata_json=metadata or {},
        )
        self.db.add(approval)
        await self.db.flush()
        await self.audit.log(
            tenant_id=tenant_id,
            action="approval.create",
            resource_type="approval",
            resource_id=approval.approval_id,
            actor_type="system",
            actor_id=requested_by,
            payload={"task_id": task_id, "reason": reason},
        )
        await self.metering.record(
            tenant_id=tenant_id,
            task_id=task_id,
            event_type="approval",
            metric_name="request_count",
            metric_value=1,
        )
        return approval

    async def get(self, approval_id: str, tenant_id: str) -> Approval | None:
        result = await self.db.execute(
            select(Approval).where(Approval.approval_id == approval_id, Approval.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def resolve(
        self,
        approval_id: str,
        tenant_id: str,
        decision: str,
        note: str | None = None,
        actor_id: str | None = None,
    ) -> Approval:
        approval = await self.get(approval_id, tenant_id)
        if not approval:
            raise ValueError(f"Approval {approval_id} 不存在")
        if approval.status != "PENDING":
            raise ValueError(f"Approval {approval_id} 已处理")

        resolved_at = datetime.now(timezone.utc)
        next_task_state = "WORKING" if decision == "APPROVED" else "FAILED"
        await self.db.execute(
            update(Approval)
            .where(Approval.approval_id == approval_id)
            .values(status=decision, decision_note=note, resolved_at=resolved_at)
        )
        approval.status = decision
        approval.decision_note = note
        approval.resolved_at = resolved_at

        await self.task_service.update_state(
            task_id=approval.task_id,
            new_state=next_task_state,
            tenant_id=tenant_id,
            reason=note or approval.reason,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
        )
        await self.delivery.enqueue(
            tenant_id=tenant_id,
            task_id=approval.task_id,
            target_channel="rocket_chat",
            target_ref={"kind": "approval_result"},
            payload={
                "approval_id": approval_id,
                "task_id": approval.task_id,
                "decision": decision,
                "note": note,
            },
            idempotency_key=f"approval-result:{approval_id}:{decision}",
        )
        await self.audit.log(
            tenant_id=tenant_id,
            action="approval.resolve",
            resource_type="approval",
            resource_id=approval_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            payload={"decision": decision},
        )
        return approval

    async def expire_pending(self, tenant_id: str | None = None) -> list[Approval]:
        result = await self.db.execute(select(Approval).where(Approval.status == "PENDING"))
        approvals = list(result.scalars().all())
        expired: list[Approval] = []
        for approval in approvals:
            if tenant_id and approval.tenant_id != tenant_id:
                continue
            await self.db.execute(
                update(Approval)
                .where(Approval.approval_id == approval.approval_id)
                .values(status="EXPIRED", resolved_at=datetime.now(timezone.utc))
            )
            approval.status = "EXPIRED"
            await self.task_service.update_state(
                task_id=approval.task_id,
                new_state="EXPIRED",
                tenant_id=approval.tenant_id,
                reason="审批超时",
                actor_type="worker",
            )
            expired.append(approval)
        return expired
