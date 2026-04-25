"""
AgentRegistry：Agent 注册、查询、能力声明、健康状态维护
"""
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.services.audit_service import AuditService


class AgentNotFoundError(ValueError):
    """Agent 不存在。"""


class AgentRegistry:
    PUBLIC_NUMBER_START = 10000001

    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    async def register(
        self,
        agent_id: str,
        tenant_id: str,
        agent_type: str,
        display_name: str,
        capabilities: dict[str, Any] | None = None,
        auth_scheme: str | None = None,
        config_json: dict[str, Any] | None = None,
        actor_id: str | None = None,
    ) -> Agent:
        """注册或更新 Agent"""
        existing = await self.get(agent_id, tenant_id)
        if existing:
            if getattr(existing, "public_number", None) is None:
                existing.public_number = await self._next_public_number()
            # 更新已有 Agent
            await self.db.execute(
                update(Agent)
                .where(Agent.agent_id == agent_id, Agent.tenant_id == tenant_id)
                .values(
                    public_number=existing.public_number,
                    display_name=display_name,
                    status="ACTIVE",
                    capabilities=capabilities or {},
                    auth_scheme=auth_scheme,
                    config_json=config_json or {},
                )
            )
            await self.audit.log(tenant_id, "agent.update", "agent", agent_id, actor_id=actor_id)
            existing.display_name = display_name
            existing.status = "ACTIVE"
            existing.capabilities = capabilities or {}
            existing.auth_scheme = auth_scheme
            existing.config_json = config_json or {}
            return existing

        agent = Agent(
            agent_id=agent_id,
            public_number=await self._next_public_number(),
            tenant_id=tenant_id,
            agent_type=agent_type,
            display_name=display_name,
            status="ACTIVE",
            capabilities=capabilities or {},
            auth_scheme=auth_scheme,
            config_json=config_json or {},
        )
        self.db.add(agent)
        await self.audit.log(tenant_id, "agent.register", "agent", agent_id, actor_id=actor_id)
        return agent

    async def _next_public_number(self) -> int:
        """Allocate the next public friend number.

        The number is user-facing only. Internal routing continues to use
        agent_id, so this can stay compact and globally unique.
        """

        result = await self.db.execute(select(func.max(Agent.public_number)))
        current = result.scalar_one_or_none()
        if not current:
            return self.PUBLIC_NUMBER_START
        return max(int(current) + 1, self.PUBLIC_NUMBER_START)

    async def get(self, agent_id: str, tenant_id: str) -> Agent | None:
        """查询单个 Agent（租户隔离）"""
        result = await self.db.execute(
            select(Agent).where(Agent.agent_id == agent_id, Agent.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def get_by_ref(self, agent_ref: str, tenant_id: str) -> Agent | None:
        """Query by internal agent_id or public friend number within a tenant."""

        text = str(agent_ref or "").strip()
        if text.isdigit() and len(text) >= 8:
            result = await self.db.execute(
                select(Agent).where(Agent.public_number == int(text), Agent.tenant_id == tenant_id)
            )
            return result.scalar_one_or_none()
        return await self.get(text, tenant_id)

    async def list_active(self, tenant_id: str) -> list[Agent]:
        """列出租户下所有 ACTIVE Agent"""
        result = await self.db.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.status == "ACTIVE")
        )
        return list(result.scalars().all())

    async def set_status(
        self,
        agent_id: str,
        tenant_id: str,
        status: str,
        actor_id: str | None = None,
    ) -> None:
        """更新 Agent 状态（ACTIVE / INACTIVE / SUSPENDED）"""
        result = await self.db.execute(
            update(Agent)
            .where(Agent.agent_id == agent_id, Agent.tenant_id == tenant_id)
            .values(status=status)
        )
        if result.rowcount == 0:
            raise AgentNotFoundError(f"Agent {agent_id} 不存在")
        await self.audit.log(
            tenant_id, "agent.status_change", "agent", agent_id,
            payload={"status": status}, actor_id=actor_id,
        )

    async def healthcheck(self, agent_id: str, tenant_id: str) -> dict[str, Any]:
        """
        简单健康检查：查询 Agent 是否存在且 ACTIVE。
        后续版块可扩展为真实 HTTP ping。
        """
        agent = await self.get(agent_id, tenant_id)
        if not agent:
            return {"agent_id": agent_id, "healthy": False, "reason": "not_found"}
        return {
            "agent_id": agent_id,
            "healthy": agent.status == "ACTIVE",
            "status": agent.status,
        }
