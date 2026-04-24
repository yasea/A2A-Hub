import uuid
from typing import List

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent
from app.models.agent_friend import AgentFriend
from app.models.context import Context
from app.services.context_service import ContextService


class FriendServiceError(ValueError):
    pass


class FriendNotFoundError(FriendServiceError):
    pass


class FriendForbiddenError(FriendServiceError):
    pass


class FriendConflictError(FriendServiceError):
    pass


ALLOWED_FRIEND_UPDATE_STATUSES = {"ACCEPTED", "REJECTED", "BLOCKED"}


class FriendService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_agent(self, agent_id: str) -> Agent | None:
        result = await self.db.execute(select(Agent).where(Agent.agent_id == agent_id, Agent.status == "ACTIVE"))
        return result.scalar_one_or_none()

    async def assert_agent_owned(self, tenant_id: str, agent_id: str) -> Agent:
        agent = await self._get_agent(agent_id)
        if not agent or agent.tenant_id != tenant_id:
            raise FriendForbiddenError(f"agent {agent_id} 不存在或不属于当前租户")
        return agent

    async def _find_pair(self, requester_agent_id: str, target_agent_id: str) -> AgentFriend | None:
        result = await self.db.execute(
            select(AgentFriend).where(
                or_(
                    (AgentFriend.requester_agent_id == requester_agent_id) & (AgentFriend.target_agent_id == target_agent_id),
                    (AgentFriend.requester_agent_id == target_agent_id) & (AgentFriend.target_agent_id == requester_agent_id),
                )
            ).order_by(AgentFriend.created_at.desc())
        )
        return result.scalars().first()

    async def _get_context_by_source(self, tenant_id: str, source_channel: str, source_conversation_id: str) -> Context | None:
        result = await self.db.execute(
            select(Context).where(
                Context.tenant_id == tenant_id,
                Context.source_channel == source_channel,
                Context.source_conversation_id == source_conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_request(
        self,
        tenant_id: str,
        requester_agent_id: str,
        target_agent_id: str,
        message: str | None = None,
    ):
        requester = await self.assert_agent_owned(tenant_id, requester_agent_id)
        target = await self._get_agent(target_agent_id)
        if not target:
            raise FriendNotFoundError(f"target agent {target_agent_id} 不存在或未激活")
        if requester.agent_id == target.agent_id:
            raise FriendConflictError("不能添加自己为好友")
        existing = await self._find_pair(requester.agent_id, target.agent_id)
        if existing and existing.status == "ACCEPTED":
            raise FriendConflictError("双方已经是好友")
        if existing and existing.status == "PENDING":
            raise FriendConflictError("好友请求已存在，等待对方处理")

        friend = AgentFriend(
            tenant_id=tenant_id,
            requester_tenant_id=requester.tenant_id,
            target_tenant_id=target.tenant_id,
            requester_agent_id=requester.agent_id,
            target_agent_id=target.agent_id,
            status="PENDING",
            invite_token=str(uuid.uuid4().hex),
            message=message,
        )
        self.db.add(friend)
        await self.db.flush()
        return friend

    async def list_for_agent(self, tenant_id: str, agent_id: str) -> List[AgentFriend]:
        await self.assert_agent_owned(tenant_id, agent_id)
        result = await self.db.execute(
            select(AgentFriend).where(
                ((AgentFriend.requester_agent_id == agent_id) & (AgentFriend.requester_tenant_id == tenant_id))
                | ((AgentFriend.target_agent_id == agent_id) & (AgentFriend.target_tenant_id == tenant_id))
            ).order_by(AgentFriend.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, friend_id: int) -> AgentFriend | None:
        result = await self.db.execute(select(AgentFriend).where(AgentFriend.id == friend_id))
        return result.scalar_one_or_none()

    async def get_visible_friend(self, friend_id: int, tenant_id: str, agent_id: str) -> AgentFriend:
        friend = await self.get(friend_id)
        if not friend:
            raise FriendNotFoundError("friend request not found")
        if (
            friend.requester_agent_id == agent_id
            and friend.requester_tenant_id == tenant_id
        ) or (
            friend.target_agent_id == agent_id
            and friend.target_tenant_id == tenant_id
        ):
            return friend
        raise FriendForbiddenError("无权访问该好友关系")

    async def accept(self, friend_id: int, tenant_id: str, agent_id: str) -> AgentFriend:
        friend = await self.get_visible_friend(friend_id, tenant_id, agent_id)
        if friend.target_agent_id != agent_id or friend.target_tenant_id != tenant_id:
            raise FriendForbiddenError("只有被邀请方可以接受好友请求")
        if friend.status == "ACCEPTED":
            return friend
        if friend.status != "PENDING":
            raise FriendConflictError(f"当前状态不允许接受: {friend.status}")
        friend.status = "ACCEPTED"
        ctx_svc = ContextService(self.db)
        requester_context = await self._get_context_by_source(
            friend.requester_tenant_id, "friend", f"friend:{friend.id}:requester"
        )
        if not requester_context:
            requester_context = await ctx_svc.create(
                tenant_id=friend.requester_tenant_id,
                source_channel="friend",
                source_conversation_id=f"friend:{friend.id}:requester",
                title=f"Chat {friend.requester_agent_id} ↔ {friend.target_agent_id}",
                metadata={
                    "friend_id": friend.id,
                    "peer_agent_id": friend.target_agent_id,
                    "peer_tenant_id": friend.target_tenant_id,
                },
            )
        target_context = await self._get_context_by_source(
            friend.target_tenant_id, "friend", f"friend:{friend.id}:target"
        )
        if not target_context:
            target_context = await ctx_svc.create(
                tenant_id=friend.target_tenant_id,
                source_channel="friend",
                source_conversation_id=f"friend:{friend.id}:target",
                title=f"Chat {friend.target_agent_id} ↔ {friend.requester_agent_id}",
                metadata={
                    "friend_id": friend.id,
                    "peer_agent_id": friend.requester_agent_id,
                    "peer_tenant_id": friend.requester_tenant_id,
                },
            )
        await self.db.flush()
        friend.requester_context_id = requester_context.context_id
        friend.target_context_id = target_context.context_id
        friend.context_id = requester_context.context_id
        await ctx_svc.add_participant(requester_context.context_id, "agent", friend.requester_agent_id, role="self")
        await ctx_svc.add_participant(requester_context.context_id, "agent", friend.target_agent_id, role="peer")
        await ctx_svc.add_participant(target_context.context_id, "agent", friend.target_agent_id, role="self")
        await ctx_svc.add_participant(target_context.context_id, "agent", friend.requester_agent_id, role="peer")
        return friend

    async def update_status(self, friend_id: int, tenant_id: str, agent_id: str, status: str) -> AgentFriend:
        friend = await self.get_visible_friend(friend_id, tenant_id, agent_id)
        next_status = (status or "").upper()
        if next_status not in ALLOWED_FRIEND_UPDATE_STATUSES:
            raise FriendConflictError("好友请求状态只能是 accepted、rejected 或 blocked")
        if next_status == "ACCEPTED":
            return await self.accept(friend_id, tenant_id, agent_id)
        if friend.target_agent_id != agent_id or friend.target_tenant_id != tenant_id:
            raise FriendForbiddenError("只有被邀请方可以更新好友请求状态")
        if friend.status not in {"PENDING", next_status}:
            raise FriendConflictError(f"当前状态不允许更新为 {next_status}: {friend.status}")
        friend.status = next_status
        return friend

    def view_payload(self, friend: AgentFriend, tenant_id: str, agent_id: str) -> dict:
        is_requester = friend.requester_agent_id == agent_id and friend.requester_tenant_id == tenant_id
        context_id = friend.requester_context_id if is_requester else friend.target_context_id
        peer_agent_id = friend.target_agent_id if is_requester else friend.requester_agent_id
        return {
            "id": friend.id,
            "tenant_id": tenant_id,
            "requester_tenant_id": friend.requester_tenant_id,
            "target_tenant_id": friend.target_tenant_id,
            "requester_agent_id": friend.requester_agent_id,
            "target_agent_id": friend.target_agent_id,
            "status": friend.status,
            "context_id": context_id,
            "peer_agent_id": peer_agent_id,
            "can_send_message": friend.status == "ACCEPTED" and bool(context_id),
            "message": friend.message,
        }

    async def resolve_target_context(
        self,
        source_tenant_id: str,
        source_agent_id: str,
        target_agent_id: str,
    ) -> tuple[str, str, dict[str, str]]:
        target = await self._get_agent(target_agent_id)
        if not target:
            raise FriendNotFoundError(f"target agent {target_agent_id} 不存在或未激活")

        friend = await self._find_pair(source_agent_id, target.agent_id)
        if friend and friend.status == "ACCEPTED":
            if friend.requester_agent_id == source_agent_id and friend.requester_tenant_id == source_tenant_id:
                context_id = friend.target_context_id
                target_tenant_id = friend.target_tenant_id
            elif friend.target_agent_id == source_agent_id and friend.target_tenant_id == source_tenant_id:
                context_id = friend.requester_context_id
                target_tenant_id = friend.requester_tenant_id
            else:
                raise FriendForbiddenError("当前 agent 不属于该好友关系")

            if not context_id:
                raise FriendConflictError("好友会话上下文尚未初始化，请重新接受邀请或重建好友关系")
            return target_tenant_id, context_id, {"mode": "friend", "friend_id": str(friend.id)}

        if target.tenant_id != source_tenant_id:
            raise FriendForbiddenError("跨租户 agent 对话需要先建立已接受的好友关系")

        ctx_svc = ContextService(self.db)
        pair_key = "::".join(sorted([source_agent_id, target.agent_id]))
        context = await self._get_context_by_source(target.tenant_id, "agent_link_dm", f"dm:{pair_key}")
        if not context:
            context = await ctx_svc.create(
                tenant_id=target.tenant_id,
                source_channel="agent_link_dm",
                source_conversation_id=f"dm:{pair_key}",
                title=f"Chat {source_agent_id} ↔ {target.agent_id}",
                metadata={
                    "source_agent_id": source_agent_id,
                    "target_agent_id": target.agent_id,
                    "mode": "same_tenant_direct",
                },
            )
            await self.db.flush()
        await ctx_svc.add_participant(context.context_id, "agent", source_agent_id, role="peer")
        await ctx_svc.add_participant(context.context_id, "agent", target.agent_id, role="self")
        return target.tenant_id, context.context_id, {"mode": "same_tenant_direct"}
