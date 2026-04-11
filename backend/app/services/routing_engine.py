"""
RoutingEngine：策略路由 + 防循环 + 跳数限制

路由优先级（方案 12.2）：
  1. 显式指定 target_agent_id
  2. routing_rules 按 priority 匹配（task_type / source_channel）
  3. capabilities 模糊匹配
  4. 无匹配 → 抛出 RoutingError
"""
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.agent import Agent
from app.models.routing import RoutingRule, TaskRouteHop
from app.models.task import Task


class RoutingError(Exception):
    """路由失败"""
    pass


class RoutingEngine:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def route(
        self,
        task: Task,
        from_agent_id: str | None = None,
        dry_run: bool = False,
    ) -> str:
        """
        为 task 选择目标 agent_id。
        - 写 task_route_hops 记录
        - 超过最大跳数抛出 RoutingError
        """
        tenant_id = task.tenant_id

        # 1. 检查跳数上限
        hop_count = await self._get_hop_count(task.task_id)
        if hop_count >= settings.TASK_MAX_HOP_COUNT:
            raise RoutingError(f"路由跳数已达上限 {settings.TASK_MAX_HOP_COUNT}，task_id={task.task_id}")

        # 2. 显式指定
        if task.target_agent_id:
            return await self._finalize_target(
                task=task,
                hop_count=hop_count,
                from_agent_id=from_agent_id,
                target_agent_id=task.target_agent_id,
                reason="explicit",
                rule_id=None,
                dry_run=dry_run,
            )

        # 3. routing_rules 匹配
        target_id, rule_id, reason = await self._match_rules(task, tenant_id)
        if target_id:
            return await self._finalize_target(
                task=task,
                hop_count=hop_count,
                from_agent_id=from_agent_id,
                target_agent_id=target_id,
                reason=reason,
                rule_id=rule_id,
                dry_run=dry_run,
            )

        # 4. capabilities 模糊匹配
        target_id = await self._match_capabilities(task, tenant_id)
        if target_id:
            return await self._finalize_target(
                task=task,
                hop_count=hop_count,
                from_agent_id=from_agent_id,
                target_agent_id=target_id,
                reason="capability_match",
                rule_id=None,
                dry_run=dry_run,
            )

        raise RoutingError(f"无可用路由，task_type={task.task_type}, tenant={tenant_id}")

    async def check_loop(self, task_id: str, target_agent_id: str) -> bool:
        """检查是否存在路由循环（同一 agent 在本 task 中已被路由过）"""
        result = await self.db.execute(
            select(TaskRouteHop).where(
                TaskRouteHop.task_id == task_id,
                TaskRouteHop.to_agent_id == target_agent_id,
            )
        )
        return result.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _get_hop_count(self, task_id: str) -> int:
        result = await self.db.execute(
            select(func.count()).where(TaskRouteHop.task_id == task_id)
        )
        return result.scalar() or 0

    async def _get_active_agent(self, agent_id: str, tenant_id: str) -> Agent | None:
        result = await self.db.execute(
            select(Agent).where(
                Agent.agent_id == agent_id,
                Agent.tenant_id == tenant_id,
                Agent.status == "ACTIVE",
            )
        )
        return result.scalar_one_or_none()

    async def _match_rules(
        self, task: Task, tenant_id: str
    ) -> tuple[str | None, Any, str]:
        """按 priority 顺序匹配 routing_rules"""
        result = await self.db.execute(
            select(RoutingRule)
            .where(RoutingRule.tenant_id == tenant_id, RoutingRule.is_active == True)  # noqa: E712
            .order_by(RoutingRule.priority)
        )
        rules = result.scalars().all()

        for rule in rules:
            if self._eval_match_expr(rule.match_expr, task):
                # 确认目标 agent 可用
                agent = await self._get_active_agent(str(rule.target_agent_id), tenant_id)
                if agent:
                    return str(rule.target_agent_id), rule.id, f"rule:{rule.name}"
        return None, None, ""

    def _eval_match_expr(self, expr: dict, task: Task) -> bool:
        """
        简单 match_expr 求值。
        支持字段：task_type, source_channel, source_system
        示例：{"task_type": "analysis"} 或 {"task_type": ["analysis", "quote"]}
        """
        if not expr:
            return True  # 空表达式 = 匹配所有

        for field, expected in expr.items():
            actual = getattr(task, field, None)
            if actual is None:
                return False
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            else:
                if actual != expected:
                    return False
        return True

    async def _match_capabilities(self, task: Task, tenant_id: str) -> str | None:
        """按 task_type 匹配 agent capabilities"""
        result = await self.db.execute(
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.status == "ACTIVE",
                # capabilities JSONB 包含 task_type 键
                Agent.capabilities[task.task_type].isnot(None),
            )
        )
        agent = result.scalars().first()
        return agent.agent_id if agent else None

    async def _record_hop(
        self,
        task: Task,
        hop_seq: int,
        from_agent_id: str | None,
        to_agent_id: str,
        reason: str | None,
        rule_id: Any,
    ) -> None:
        hop = TaskRouteHop(
            task_id=task.task_id,
            tenant_id=task.tenant_id,
            hop_seq=hop_seq,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            route_reason=reason,
            matched_rule_id=rule_id,
        )
        self.db.add(hop)

    async def _finalize_target(
        self,
        task: Task,
        hop_count: int,
        from_agent_id: str | None,
        target_agent_id: str,
        reason: str | None,
        rule_id: Any,
        dry_run: bool,
    ) -> str:
        """统一执行目标校验、防循环和 hop 落库。"""
        agent = await self._get_active_agent(target_agent_id, task.tenant_id)
        if not agent:
            raise RoutingError(f"指定的 target_agent {target_agent_id} 不存在或不可用")
        if await self.check_loop(task.task_id, target_agent_id):
            raise RoutingError(f"检测到路由循环，task_id={task.task_id}, target_agent_id={target_agent_id}")
        if not dry_run:
            await self._record_hop(task, hop_count + 1, from_agent_id, target_agent_id, reason, rule_id)
        return target_agent_id
