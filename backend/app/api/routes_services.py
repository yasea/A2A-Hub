from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.service import (
    ServicePublicationCreateRequest,
    ServicePublicationResponse,
    ServicePublicationUpdateRequest,
    ServiceThreadCreateRequest,
    ServiceThreadCreateResponse,
    ServiceThreadMessageCreateRequest,
    ServiceThreadMessageCreateResponse,
    ServiceThreadMessageResponse,
    ServiceThreadResponse,
)
from app.services.service_conversation_service import (
    ServiceConversationError,
    ServiceConversationService,
    ServiceThreadForbidden,
)
from app.services.service_directory_service import (
    ServiceDirectoryService,
    ServicePublicationError,
    ServicePublicationNotFound,
)

router = APIRouter(prefix="/v1", tags=["services"])


@router.post(
    "/services",
    response_model=ApiResponse[ServicePublicationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="发布服务",
    description="服务提供者使用。将当前租户下的某个运行时 Agent 发布成可发现、可发起对话的 service。",
)
async def create_service_publication(req: ServicePublicationCreateRequest, db: DbDep, tenant: TenantDep):
    svc = ServiceDirectoryService(db)
    try:
        item = await svc.create(
            tenant_id=tenant["tenant_id"],
            handler_agent_id=req.handler_agent_id,
            title=req.title,
            summary=req.summary,
            visibility=req.visibility,
            contact_policy=req.contact_policy,
            allow_agent_initiated_chat=req.allow_agent_initiated_chat,
            tags=req.tags,
            capabilities_public=req.capabilities_public,
            metadata=req.metadata,
            service_id=req.service_id,
            actor_id=tenant.get("sub"),
        )
    except ServicePublicationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ApiResponse.ok(ServicePublicationResponse.model_validate(item))


@router.patch(
    "/services/{service_id}",
    response_model=ApiResponse[ServicePublicationResponse],
    summary="更新服务发布",
    description="服务提供者使用。更新 service 的标题、摘要、公开可见性、绑定 Agent 或状态。",
)
async def update_service_publication(service_id: str, req: ServicePublicationUpdateRequest, db: DbDep, tenant: TenantDep):
    svc = ServiceDirectoryService(db)
    try:
        item = await svc.update(
            service_id,
            tenant["tenant_id"],
            actor_id=tenant.get("sub"),
            handler_agent_id=req.handler_agent_id,
            title=req.title,
            summary=req.summary,
            visibility=req.visibility,
            contact_policy=req.contact_policy,
            allow_agent_initiated_chat=req.allow_agent_initiated_chat,
            status=req.status,
            tags=req.tags,
            capabilities_public=req.capabilities_public,
            metadata=req.metadata,
        )
    except ServicePublicationNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ServicePublicationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ApiResponse.ok(ServicePublicationResponse.model_validate(item))


@router.get(
    "/services",
    response_model=ApiResponse[list[ServicePublicationResponse]],
    summary="发现服务目录",
    description="任意已认证租户、Agent 或平台组件使用。返回当前可发现的公开 service 列表。",
)
async def list_services(db: DbDep, tenant: TenantDep):
    svc = ServiceDirectoryService(db)
    items = await svc.list_accessible(tenant["tenant_id"])
    return ApiResponse.ok([ServicePublicationResponse.model_validate(item) for item in items])


@router.get(
    "/services/{service_id}",
    response_model=ApiResponse[ServicePublicationResponse],
    summary="查看服务详情",
    description="在目录中发现 service 后调用。返回该 service 的公开描述、公开能力和绑定 handler agent。",
)
async def get_service(service_id: str, db: DbDep, tenant: TenantDep):
    svc = ServiceDirectoryService(db)
    item = await svc.get_accessible(service_id, tenant["tenant_id"])
    if not item:
        raise HTTPException(status_code=404, detail="service 不存在或不可见")
    return ApiResponse.ok(ServicePublicationResponse.model_validate(item))


@router.post(
    "/services/{service_id}/threads",
    response_model=ApiResponse[ServiceThreadCreateResponse],
    status_code=status.HTTP_201_CREATED,
    summary="发起服务会话",
    description="发现公开 service 后调用。创建一个多轮 service thread，并可选发送第一条消息。",
)
async def create_service_thread(service_id: str, req: ServiceThreadCreateRequest, db: DbDep, tenant: TenantDep):
    directory = ServiceDirectoryService(db)
    publication = await directory.get_accessible(service_id, tenant["tenant_id"])
    if not publication:
        raise HTTPException(status_code=404, detail="service 不存在或不可见")
    conversations = ServiceConversationService(db)
    try:
        thread = await conversations.create_thread(
            publication=publication,
            consumer_tenant_id=tenant["tenant_id"],
            initiator_agent_id=req.initiator_agent_id or tenant.get("agent_id"),
            title=req.title,
            metadata=req.metadata,
            actor_id=tenant.get("sub"),
        )
        task_id = None
        if req.opening_message and req.opening_message.strip():
            _, task_id = await conversations.send_consumer_message(
                thread=thread,
                tenant=tenant,
                text=req.opening_message.strip(),
                initiator_agent_id=req.initiator_agent_id or tenant.get("agent_id"),
                metadata={"created_with_thread": True},
            )
    except ServiceConversationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ApiResponse.ok(
        ServiceThreadCreateResponse(
            thread=ServiceThreadResponse.model_validate(thread),
            task_id=task_id,
        )
    )


@router.get(
    "/service-threads",
    response_model=ApiResponse[list[ServiceThreadResponse]],
    summary="列出服务会话",
    description="查看当前租户参与过的 service thread，支持继续多轮对话或排查状态。",
)
async def list_service_threads(db: DbDep, tenant: TenantDep):
    svc = ServiceConversationService(db)
    items = await svc.list_threads(tenant["tenant_id"])
    return ApiResponse.ok([ServiceThreadResponse.model_validate(item) for item in items])


@router.get(
    "/service-threads/{thread_id}",
    response_model=ApiResponse[ServiceThreadResponse],
    summary="查看服务会话详情",
    description="返回 thread 的参与租户、绑定 service、provider context 和当前状态。",
)
async def get_service_thread(thread_id: str, db: DbDep, tenant: TenantDep):
    svc = ServiceConversationService(db)
    thread = await svc.get_thread(thread_id, tenant["tenant_id"])
    if not thread:
        raise HTTPException(status_code=404, detail="thread 不存在")
    await svc.sync_assistant_messages(thread)
    return ApiResponse.ok(ServiceThreadResponse.model_validate(thread))


@router.get(
    "/service-threads/{thread_id}/messages",
    response_model=ApiResponse[list[ServiceThreadMessageResponse]],
    summary="查看服务会话消息",
    description="读取一个多轮 service thread 的消息列表；如果底层 handler agent 已回复，会自动回填 assistant 消息。",
)
async def list_service_thread_messages(thread_id: str, db: DbDep, tenant: TenantDep):
    svc = ServiceConversationService(db)
    thread = await svc.get_thread(thread_id, tenant["tenant_id"])
    if not thread:
        raise HTTPException(status_code=404, detail="thread 不存在")
    try:
        items = await svc.list_messages(thread, tenant["tenant_id"])
    except ServiceThreadForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ApiResponse.ok([ServiceThreadMessageResponse.model_validate(item) for item in items])


@router.post(
    "/service-threads/{thread_id}/messages",
    response_model=ApiResponse[ServiceThreadMessageCreateResponse],
    status_code=status.HTTP_201_CREATED,
    summary="继续服务会话",
    description="对一个已存在的 service thread 继续发送下一轮消息。当前版本由消费方继续发言，服务方由绑定 Agent 自动回复。",
)
async def create_service_thread_message(
    thread_id: str,
    req: ServiceThreadMessageCreateRequest,
    db: DbDep,
    tenant: TenantDep,
):
    svc = ServiceConversationService(db)
    thread = await svc.get_thread(thread_id, tenant["tenant_id"])
    if not thread:
        raise HTTPException(status_code=404, detail="thread 不存在")
    try:
        message, task_id = await svc.send_consumer_message(
            thread=thread,
            tenant=tenant,
            text=req.text,
            initiator_agent_id=req.initiator_agent_id or tenant.get("agent_id"),
            metadata=req.metadata,
        )
    except ServiceThreadForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ServiceConversationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return ApiResponse.ok(
        ServiceThreadMessageCreateResponse(
            thread_id=thread.thread_id,
            task_id=task_id,
            message_id=message.message_id,
        )
    )

