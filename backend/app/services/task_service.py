"""
TaskService：任务状态机驱动核心
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task import Task, TaskMessage, TaskStateTransition
from app.services.audit_service import AuditService
from app.services.stream_service import task_event_broker

# 合法状态跳转表
VALID_TRANSITIONS: dict[str, set[str]] = {
    "SUBMITTED":        {"ROUTING", "CANCELED"},
    "ROUTING":          {"WORKING", "FAILED", "CANCELED"},
    "WORKING":          {"WAITING_EXTERNAL", "AUTH_REQUIRED", "COMPLETED", "FAILED", "CANCELED"},
    "WAITING_EXTERNAL": {"WORKING", "FAILED", "CANCELED", "EXPIRED"},
    "AUTH_REQUIRED":    {"WORKING", "FAILED", "CANCELED", "EXPIRED"},
    "COMPLETED":        set(),   # 终态
    "FAILED":           set(),   # 终态
    "CANCELED":         set(),   # 终态
    "EXPIRED":          set(),   # 终态
}

TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELED", "EXPIRED"}


class TaskServiceError(ValueError):
    """任务服务基础异常。"""


class TaskNotFoundError(TaskServiceError):
    """任务不存在。"""


class InvalidTaskTransitionError(TaskServiceError):
    """非法状态跳转。"""


class TaskService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def create_task(
        self,
        tenant_id: str,
        context_id: str,
        input_text: str | None = None,
        task_type: str = "generic",
        priority: str = "normal",
        target_agent_id: str | None = None,
        initiator_agent_id: str | None = None,
        approval_required: bool = False,
        idempotency_key: str | None = None,
        source_system: str | None = None,
        source_message_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> Task:
        """创建任务，写初始状态流水"""
        # 外部消息去重
        if source_system and source_message_id:
            existing = await self._find_by_source_message(tenant_id, source_system, source_message_id)
            if existing:
                setattr(existing, "_is_newly_created", False)
                return existing

        # 幂等检查
        if idempotency_key:
            existing = await self._find_by_idempotency(tenant_id, idempotency_key)
            if existing:
                setattr(existing, "_is_newly_created", False)
                return existing

        task = Task(
            task_id=f"task_{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            context_id=context_id,
            task_type=task_type,
            state="SUBMITTED",
            priority=priority,
            input_text=input_text,
            approval_required=approval_required,
            target_agent_id=target_agent_id,
            initiator_agent_id=initiator_agent_id,
            idempotency_key=idempotency_key,
            source_system=source_system,
            source_message_id=source_message_id,
            trace_id=trace_id,
            metadata_json=metadata or {},
            retry_count=0,
        )
        self.db.add(task)
        await self.db.flush()  # 确保 task 写入 DB，后续 update_state 可查到

        # 写初始状态流水
        self._add_transition(task.task_id, tenant_id, None, "SUBMITTED", "task.create", actor_id, trace_id)

        await self.audit.log(
            tenant_id=tenant_id,
            action="task.create",
            resource_type="task",
            resource_id=task.task_id,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            payload={"task_type": task_type, "priority": priority},
            trace_id=trace_id,
        )
        setattr(task, "_is_newly_created", True)
        await task_event_broker.publish(
            task.task_id,
            {"event": "task.created", "task_id": task.task_id, "state": task.state, "tenant_id": tenant_id},
        )
        return task

    async def update_state(
        self,
        task_id: str,
        new_state: str,
        tenant_id: str,
        reason: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        trace_id: str | None = None,
        output_text: str | None = None,
    ) -> Task:
        """驱动状态机跳转，校验合法性"""
        task = await self.get(task_id, tenant_id)
        if not task:
            raise TaskNotFoundError(f"Task {task_id} 不存在")

        current = task.state
        allowed = VALID_TRANSITIONS.get(current, set())
        if new_state not in allowed:
            raise InvalidTaskTransitionError(f"非法状态跳转: {current} → {new_state}")

        values: dict[str, Any] = {
            "state": new_state,
            "updated_at": datetime.now(timezone.utc),
        }
        if output_text is not None:
            values["output_text"] = output_text
        if new_state in TERMINAL_STATES:
            values["completed_at"] = datetime.now(timezone.utc)
        if new_state == "FAILED" and reason:
            values["failure_reason"] = reason

        await self.db.execute(
            update(Task).where(Task.task_id == task_id).values(**values)
        )

        # 写状态流水
        self._add_transition(task_id, tenant_id, current, new_state, reason, actor_id, trace_id, actor_type)

        await self.audit.log(
            tenant_id=tenant_id,
            action="task.state_change",
            resource_type="task",
            resource_id=task_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload={"from": current, "to": new_state, "reason": reason},
            trace_id=trace_id,
        )

        task.state = new_state
        task.updated_at = values["updated_at"]
        if output_text is not None:
            task.output_text = output_text
        if "completed_at" in values:
            task.completed_at = values["completed_at"]
        if new_state == "FAILED" and reason:
            task.failure_reason = reason
        await task_event_broker.publish(
            task.task_id,
            {
                "event": "task.state_changed",
                "task_id": task.task_id,
                "state": task.state,
                "tenant_id": tenant_id,
                "reason": reason,
            },
        )
        return task

    async def append_message(
        self,
        task_id: str,
        context_id: str,
        role: str,
        content_text: str | None = None,
        content_json: dict | None = None,
        mime_type: str = "text/plain",
        source_agent_id: str | None = None,
        source_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskMessage:
        """追加消息，seq_no 自动递增"""
        # 锁定 task 行，串行化同一 task 下的消息序号分配，避免并发冲突。
        await self.db.execute(
            select(Task.task_id).where(Task.task_id == task_id).with_for_update()
        )

        # 获取当前最大 seq_no
        from sqlalchemy import func
        result = await self.db.execute(
            select(func.coalesce(func.max(TaskMessage.seq_no), 0)).where(
                TaskMessage.task_id == task_id
            )
        )
        max_seq = result.scalar()

        msg = TaskMessage(
            message_id=f"msg_{uuid.uuid4().hex}",
            task_id=task_id,
            context_id=context_id,
            role=role,
            mime_type=mime_type,
            content_text=content_text,
            content_json=content_json,
            source_agent_id=source_agent_id,
            source_message_id=source_message_id,
            seq_no=max_seq + 1,
            metadata_json=metadata or {},
        )
        self.db.add(msg)
        return msg

    async def get(self, task_id: str, tenant_id: str) -> Task | None:
        """查询任务（租户隔离）"""
        result = await self.db.execute(
            select(Task).where(
                Task.task_id == task_id,
                Task.tenant_id == tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_messages(self, task_id: str, tenant_id: str) -> list[TaskMessage]:
        """查询任务消息（租户隔离）"""
        task = await self.get(task_id, tenant_id)
        if not task:
            raise TaskNotFoundError(f"Task {task_id} 不存在")
        result = await self.db.execute(
            select(TaskMessage)
            .where(TaskMessage.task_id == task_id)
            .order_by(TaskMessage.seq_no.asc())
        )
        return list(result.scalars().all())

    async def cancel(
        self,
        task_id: str,
        tenant_id: str,
        actor_id: str | None = None,
        reason: str = "用户取消",
    ) -> Task:
        """取消任务"""
        return await self.update_state(
            task_id=task_id,
            new_state="CANCELED",
            tenant_id=tenant_id,
            reason=reason,
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _add_transition(
        self,
        task_id: str,
        tenant_id: str,
        from_state: str | None,
        to_state: str,
        reason: str | None,
        actor_id: str | None,
        trace_id: str | None,
        actor_type: str = "system",
    ) -> None:
        transition = TaskStateTransition(
            task_id=task_id,
            tenant_id=tenant_id,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            actor_type=actor_type,
            actor_id=actor_id,
            trace_id=trace_id,
        )
        self.db.add(transition)

    async def _find_by_idempotency(self, tenant_id: str, key: str) -> Task | None:
        result = await self.db.execute(
            select(Task).where(
                Task.tenant_id == tenant_id,
                Task.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    async def _find_by_source_message(
        self,
        tenant_id: str,
        source_system: str,
        source_message_id: str,
    ) -> Task | None:
        result = await self.db.execute(
            select(Task).where(
                Task.tenant_id == tenant_id,
                Task.source_system == source_system,
                Task.source_message_id == source_message_id,
            )
        )
        return result.scalar_one_or_none()
