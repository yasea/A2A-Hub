"""
Docs-test 联调接口：列出 agent、发测试消息、好友查询、服务查询、服务对话、任务查询、错误查询。
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
from app.models.service import ServicePublication, ServiceThread
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
from app.services.service_directory_service import ServiceDirectoryService
from app.services.service_conversation_service import ServiceConversationService, ServiceConversationError
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


# ─────────────────────────────────────────────────────────────────────────
# 服务 (Service) 相关接口
# ─────────────────────────────────────────────────────────────────────────

@router.get("/v1/docs-test/services", response_model=ApiResponse[list[dict]], include_in_schema=False)
async def docs_test_list_services(visibility: str | None = None):
    """列出所有已注册的服务（用于 docs 测试面板）。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        svc = ServiceDirectoryService(db)
        # 获取所有服务，可按 visibility 过滤
        query = select(ServicePublication)
        if visibility:
            query = query.where(ServicePublication.visibility == visibility)
        query = query.order_by(ServicePublication.created_at.desc())
        result = await db.execute(query)
        publications = result.scalars().all()

    data = []
    for pub in publications:
        # 获取 handler agent 的在线状态
        presence = await agent_link_service.get_presence(pub.tenant_id, pub.handler_agent_id)
        data.append({
            "service_id": pub.service_id,
            "tenant_id": pub.tenant_id,
            "handler_agent_id": pub.handler_agent_id,
            "title": pub.title,
            "summary": pub.summary,
            "visibility": pub.visibility,
            "status": pub.status,
            "tags": pub.tags,
            "capabilities_public": pub.capabilities_public,
            "handler_online": bool(presence and presence.get("status") == "online"),
        })
    return ApiResponse.ok(data)


@router.post("/v1/docs-test/services", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_create_service(body: dict):
    """发布一个新服务（用于 docs 测试面板）。"""
    _ensure_docs_test_enabled()
    handler_agent_id = body.get("handler_agent_id")
    title = body.get("title")
    if not handler_agent_id or not title:
        raise HTTPException(status_code=422, detail="handler_agent_id 和 title 必填")

    async with AsyncSessionLocal() as db:
        # 查找 handler agent
        agent = (await db.execute(select(Agent).where(Agent.agent_id == handler_agent_id, Agent.status == "ACTIVE"))).scalars().first()
        if not agent:
            raise HTTPException(status_code=404, detail="handler agent 不存在或未激活")

        svc = ServiceDirectoryService(db)
        publication = await svc.create(
            tenant_id=agent.tenant_id,
            handler_agent_id=handler_agent_id,
            title=title,
            summary=body.get("summary"),
            visibility=body.get("visibility", "listed"),
            contact_policy=body.get("contact_policy", "open"),
            allow_agent_initiated_chat=body.get("allow_agent_initiated_chat", True),
            tags=body.get("tags"),
            capabilities_public=body.get("capabilities_public"),
            metadata=body.get("metadata"),
            service_id=body.get("service_id"),
            actor_id="platform:docs-test",
        )
        await db.commit()

    presence = await agent_link_service.get_presence(agent.tenant_id, handler_agent_id)
    return ApiResponse.ok({
        "service_id": publication.service_id,
        "tenant_id": publication.tenant_id,
        "handler_agent_id": publication.handler_agent_id,
        "title": publication.title,
        "summary": publication.summary,
        "visibility": publication.visibility,
        "status": publication.status,
        "handler_online": bool(presence and presence.get("status") == "online"),
    })


@router.delete("/v1/docs-test/services", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_delete_services(status: str = "INACTIVE", tenant_id: str | None = None):
    """硬删除指定状态的服务及其关联的 thread（用于清理测试残留）。默认删除所有 INACTIVE 服务。可选按 tenant_id 过滤。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        stmt = select(ServicePublication).where(ServicePublication.status == status)
        if tenant_id:
            stmt = stmt.where(ServicePublication.tenant_id == tenant_id)
        result = await db.execute(stmt)
        publications = result.scalars().all()
        deleted = 0
        for pub in publications:
            # 先删除关联的 service_threads（CASCADE 会自动删除 thread_messages）
            threads_result = await db.execute(
                select(ServiceThread).where(ServiceThread.service_id == pub.service_id)
            )
            threads = threads_result.scalars().all()
            for thread in threads:
                await db.delete(thread)
            # flush 确保 thread 删除操作提交到数据库
            await db.flush()
            # 再删除服务
            await db.delete(pub)
            deleted += 1
        await db.commit()
    return ApiResponse.ok({"deleted": deleted, "status_filter": status, "tenant_filter": tenant_id})


@router.post("/v1/docs-test/services/{service_id}/send", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_service_conversation(service_id: str, body: dict):
    """向服务发起对话（创建 thread 并发送第一条消息）。"""
    _ensure_docs_test_enabled()
    message = body.get("message", "请只回复：SERVICE_TEST_OK")
    initiator_agent_id = body.get("initiator_agent_id")

    async with AsyncSessionLocal() as db:
        # 获取服务信息
        svc_dir = ServiceDirectoryService(db)
        publication = await svc_dir.get_accessible(service_id, None)
        if not publication:
            raise HTTPException(status_code=404, detail="服务不存在或不可见")

        # 创建 thread
        svc_conv = ServiceConversationService(db)
        try:
            thread = await svc_conv.create_thread(
                publication=publication,
                consumer_tenant_id=publication.tenant_id,  # 服务方视角
                initiator_agent_id=initiator_agent_id,
                title=f"Docs 测试 -> {publication.title}",
                metadata={"source": "docs-test-panel"},
                actor_id="platform:docs-test",
            )
            await db.flush()

            # 发送第一条消息
            task_id = None
            if message.strip():
                from app.schemas.service import ServiceThreadMessageCreateRequest
                from app.services.context_service import ContextService

                # 创建 consumer context
                consumer_tenant_id = publication.tenant_id
                context = await ContextService(db).create(
                    tenant_id=consumer_tenant_id,
                    source_channel="docs-test",
                    source_conversation_id=f"docs-test-{uuid.uuid4().hex[:12]}",
                    title=f"Docs 测试 -> {publication.title}",
                    metadata={"source": "docs-test-panel", "service_id": service_id, "thread_id": thread.thread_id},
                    actor_id="platform:docs-test",
                )
                await db.flush()

                _, task_id = await svc_conv.send_consumer_message(
                    thread=thread,
                    tenant={"tenant_id": consumer_tenant_id, "sub": "platform:docs-test", "agent_id": initiator_agent_id},
                    text=message.strip(),
                    initiator_agent_id=initiator_agent_id,
                    metadata={"source": "docs-test-panel"},
                )
                await db.commit()
        except ServiceConversationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    return ApiResponse.ok({
        "service_id": service_id,
        "thread_id": thread.thread_id,
        "task_id": task_id,
        "handler_agent_id": publication.handler_agent_id,
        "title": publication.title,
    })


@router.get("/v1/docs-test/services/{service_id}/threads", response_model=ApiResponse[list[dict]], include_in_schema=False)
async def docs_test_list_service_threads(service_id: str):
    """列出服务的所有会话线程（用于 docs 测试面板）。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        svc_dir = ServiceDirectoryService(db)
        publication = await svc_dir.get_accessible(service_id, None)
        if not publication:
            raise HTTPException(status_code=404, detail="服务不存在")

        svc_conv = ServiceConversationService(db)
        threads = await svc_conv.list_threads(publication.tenant_id, service_id=service_id)

    return ApiResponse.ok([{
        "thread_id": t.thread_id,
        "service_id": t.service_id,
        "title": t.title,
        "status": t.status,
        "consumer_tenant_id": t.consumer_tenant_id,
        "created_at": str(t.created_at) if t.created_at else None,
        "updated_at": str(t.updated_at) if t.updated_at else None,
    } for t in threads])


@router.get("/v1/docs-test/token", response_model=ApiResponse[dict], include_in_schema=False)
async def docs_test_token(tenant_id: str):
    """为指定 tenant 生成测试 JWT token（仅供 /docs/services 测试面板使用）。"""
    _ensure_docs_test_enabled()
    from app.core.security import create_access_token
    token = create_access_token(
        subject=f"docs-test:{tenant_id}",
        extra={"tenant_id": tenant_id, "token_type": "docs-test"},
    )
    return ApiResponse.ok({"token": token, "tenant_id": tenant_id})


@router.get("/v1/docs-test/threads/{thread_id}/messages", response_model=ApiResponse[list[dict]], include_in_schema=False)
async def docs_test_list_thread_messages(thread_id: str, tenant_id: str):
    """列出 thread 的所有消息（用于 docs 测试面板）。"""
    _ensure_docs_test_enabled()
    async with AsyncSessionLocal() as db:
        svc_conv = ServiceConversationService(db)
        thread = await svc_conv.get_thread(thread_id, tenant_id)
        if not thread:
            raise HTTPException(status_code=404, detail="thread 不存在")

        await svc_conv.sync_assistant_messages(thread)
        messages = await svc_conv.list_messages(thread, tenant_id)

    return ApiResponse.ok([{
        "message_id": m.message_id,
        "thread_id": m.thread_id,
        "role": m.role,
        "content_text": m.content_text,
        "created_at": str(m.created_at) if m.created_at else None,
    } for m in messages])
