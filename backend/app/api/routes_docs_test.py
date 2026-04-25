"""
Docs-test 联调接口：列出 agent、发测试消息、好友查询、任务查询、错误查询。
"""
import uuid
import inspect
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api._shared import (
    _ensure_docs_test_enabled,
    _message_payload,
    _task_payload,
)
from app.core.db import AsyncSessionLocal
from app.models.agent import Agent
from app.schemas.common import ApiResponse
from app.schemas.integration import (
    AgentLinkErrorEventResponse,
    DocsAgentMessageTestRequest,
)
from app.schemas.message import MessagePart, MessageSendRequest
from app.services.agent_link_service import agent_link_service
from app.services.context_service import ContextService
from app.services.error_event_service import ErrorEventService
from app.services.friend_service import FriendService
from app.services.task_service import TaskService
from app.api.routes_messages import create_and_dispatch_message_task

router = APIRouter(tags=["docs-test"])


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _agent_lookup_criteria(agent_ref: str):
    text = str(agent_ref or "").strip()
    if text.isdigit() and len(text) >= 8:
        return Agent.public_number == int(text)
    return Agent.agent_id == text


@router.get("/v1/docs-test/agents", response_model=ApiResponse[list[dict]], include_in_schema=False)
async def docs_test_list_agents():
    """Swagger 文档页联调窗口使用：列出已注册 agent。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Agent).where(Agent.status == "ACTIVE").order_by(Agent.tenant_id.asc(), Agent.agent_id.asc())
        )
        agents = list(result.scalars().all())

    data = []
    for agent in agents:
        presence = await agent_link_service.get_presence(agent.tenant_id, agent.agent_id)
        data.append(
            {
                "tenant_id": agent.tenant_id,
                "agent_id": agent.agent_id,
                "public_number": agent.public_number,
                "display_name": agent.display_name,
                "agent_type": agent.agent_type,
                "status": agent.status,
                "online": bool(presence and presence.get("status") == "online"),
                "presence": presence,
            }
        )
    return ApiResponse.ok(data)


@router.post("/v1/docs-test/messages/send", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_send_message(req: DocsAgentMessageTestRequest):
    """Swagger 文档页联调窗口使用：以平台名义给选中 agent 发测试消息。"""
    _ensure_docs_test_enabled()
    target_agent_id = req.target_agent_id.strip()
    if not target_agent_id:
        raise HTTPException(status_code=422, detail="target_agent_id 不能为空")

    async with AsyncSessionLocal() as db:
        query = select(Agent).where(_agent_lookup_criteria(target_agent_id), Agent.status == "ACTIVE")
        if req.tenant_id:
            query = query.where(Agent.tenant_id == req.tenant_id)
        result = await db.execute(query.order_by(Agent.tenant_id.asc()))
        agent = result.scalars().first()
        if not agent:
            raise HTTPException(status_code=404, detail="agent 不存在或未激活")

        try:
            context = await ContextService(db).create(
                tenant_id=agent.tenant_id,
                source_channel="docs-test",
                source_conversation_id=f"docs-test-{uuid.uuid4().hex[:12]}",
                title=f"Docs 测试 -> {agent.agent_id}",
                metadata={"source": "docs-test-window", "target_agent_id": agent.agent_id},
                actor_id="platform:docs-test",
            )
            await db.flush()
            response = await create_and_dispatch_message_task(
                MessageSendRequest(
                    context_id=context.context_id,
                    target_agent_id=agent.agent_id,
                    parts=[MessagePart(type="text/plain", text=req.message)],
                    metadata={
                        "source": "docs-test-window",
                        "source_agent_id": "platform:docs-test",
                        "target_agent_id": agent.agent_id,
                    },
                    idempotency_key=f"docs-test-{uuid.uuid4().hex}",
                ),
                db,
                {
                    "tenant_id": agent.tenant_id,
                    "sub": "platform:docs-test",
                    "token_type": "service_account",
                    "scopes": ["messages:send"],
                },
                initiator_agent_id=None,
                source_system="docs-test-window",
            )
        except Exception:
            await db.rollback()
            raise

    return ApiResponse.ok(
        {
            "tenant_id": agent.tenant_id,
            "agent_id": agent.agent_id,
            "context_id": response.context_id,
            "task_id": response.task_id,
            "state": response.state,
        }
    )


@router.get("/v1/docs-test/agents/{agent_id}/friends", response_model=ApiResponse[list[dict]], include_in_schema=False)
async def docs_test_list_agent_friends(agent_id: str):
    """列出指定 agent 的好友记录（用于 docs 测试面板）。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        agent = (await db.execute(select(Agent).where(_agent_lookup_criteria(agent_id), Agent.status == "ACTIVE"))).scalars().first()
        if not agent:
            raise HTTPException(status_code=404, detail="agent 不存在或未激活")
        svc = FriendService(db)
        items = await svc.list_for_agent(agent.tenant_id, agent.agent_id)
    data = [await _maybe_await(svc.view_payload(it, agent.tenant_id, agent.agent_id)) for it in items]
    return ApiResponse.ok(data)


@router.post("/v1/docs-test/agents/{agent_id}/friends/send", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_send_as_agent(agent_id: str, req: DocsAgentMessageTestRequest):
    """以指定 agent 身份向另一个 agent 发送消息（admin 测试，平台代发，支持跨租户）。"""
    _ensure_docs_test_enabled()
    target_agent_id = req.target_agent_id.strip()
    if not target_agent_id:
        raise HTTPException(status_code=422, detail="target_agent_id 不能为空")

    async with AsyncSessionLocal() as db:
        source = (await db.execute(select(Agent).where(_agent_lookup_criteria(agent_id), Agent.status == "ACTIVE"))).scalars().first()
        if not source:
            raise HTTPException(status_code=404, detail="source agent not found")
        result = await db.execute(select(Agent).where(_agent_lookup_criteria(target_agent_id), Agent.status == "ACTIVE"))
        target = result.scalars().first()
        if not target:
            raise HTTPException(status_code=404, detail="target agent not found")

        try:
            context = await ContextService(db).create(
                tenant_id=target.tenant_id,
                source_channel="docs-test",
                source_conversation_id=f"docs-test-{uuid.uuid4().hex[:12]}",
                title=f"Docs as {source.agent_id} -> {target.agent_id}",
                metadata={"source": "docs-test-window", "source_agent_id": source.agent_id, "target_agent_id": target.agent_id},
                actor_id="platform:docs-test",
            )
            await db.flush()
            response = await create_and_dispatch_message_task(
                MessageSendRequest(
                    context_id=context.context_id,
                    target_agent_id=target.agent_id,
                    parts=[MessagePart(type="text/plain", text=req.message)],
                    metadata={"source": "docs-test-window", "source_agent_id": source.agent_id, "target_agent_id": target.agent_id},
                    idempotency_key=f"docs-test-{uuid.uuid4().hex}",
                ),
                db,
                {
                    "tenant_id": target.tenant_id,
                    "sub": source.agent_id,
                    "token_type": "service_account",
                    "scopes": ["messages:send"],
                },
                initiator_agent_id=source.agent_id,
                source_system="docs-test-window",
            )
        except Exception:
            await db.rollback()
            raise

    return ApiResponse.ok({"tenant_id": target.tenant_id, "agent_id": target.agent_id, "context_id": response.context_id, "task_id": response.task_id})


@router.post("/v1/docs-test/agents/{agent_id}/friends/create", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_create_friend(agent_id: str, body: dict):
    """Docs helper: 以 admin 身份在后台创建 friend request（可选立即 accept）。"""
    _ensure_docs_test_enabled()
    target_agent_id = body.get("target_agent_id")
    accept = bool(body.get("accept"))
    if not target_agent_id:
        raise HTTPException(status_code=422, detail="target_agent_id required")
    async with AsyncSessionLocal() as db:
        svc = FriendService(db)
        source = (await db.execute(select(Agent).where(_agent_lookup_criteria(agent_id), Agent.status == "ACTIVE"))).scalars().first()
        if not source:
            raise HTTPException(status_code=404, detail="source agent not found")
        friend = await svc.create_request(source.tenant_id, source.agent_id, target_agent_id, message=body.get("message"))
        await db.flush()
        if accept:
            friend = await svc.accept(friend.id, friend.target_tenant_id, friend.target_agent_id)
        await db.commit()
    return ApiResponse.ok(await _maybe_await(svc.view_payload(friend, source.tenant_id, source.agent_id)))


@router.get("/v1/docs-test/tasks/{task_id}", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_get_task(task_id: str, tenant_id: str):
    """Swagger 文档页联调窗口使用：查询任务状态和消息。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        svc = TaskService(db)
        task = await svc.get(task_id, tenant_id)
        if not task:
            raise HTTPException(status_code=404, detail="task 不存在")
        messages = await svc.list_messages(task_id, tenant_id)
    return ApiResponse.ok(
        {
            "task": _task_payload(task),
            "messages": [_message_payload(message) for message in messages],
        }
    )


@router.get("/v1/docs-test/errors", response_model=ApiResponse[list[AgentLinkErrorEventResponse]], include_in_schema=False)
async def docs_test_list_errors(agent_id: str | None = None, source_side: str | None = None, limit: int = 50):
    """Swagger 文档页错误查询使用：查看接入链路最近错误事件。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        items = await ErrorEventService(db).list_recent(agent_id=agent_id, source_side=source_side, limit=limit)
    return ApiResponse.ok([AgentLinkErrorEventResponse.model_validate(item) for item in items])
