"""
OpenClaw 注册和管理端点：transcript/approval 事件接收、agent 注册、connect-link、onboarding、bootstrap、dispatch。
"""
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import update

from app.api._shared import (
    _build_openclaw_agent_token,
    _build_openclaw_bootstrap_token,
    _normalize_agent_summary,
    _openclaw_urls,
    _record_error_event,
)
from app.api.deps import DbDep, TenantDep
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, decode_access_token
from app.models.task import Task
from app.schemas.common import ApiResponse
from app.schemas.integration import (
    OpenClawAgentRegisterRequest,
    OpenClawAgentRegistrationResponse,
    OpenClawApprovalEvent,
    OpenClawConnectLinkRequest,
    OpenClawConnectLinkResponse,
    OpenClawDispatchRequest,
    OpenClawDispatchResponse,
    OpenClawOnboardingInfo,
    OpenClawTranscriptEvent,
)
from app.services.agent_link_service import agent_link_service
from app.services.agent_registry import AgentRegistry
from app.services.openclaw_gateway_service import OPENCLAW_AGENT_MESSAGE_TYPES
from app.services.openclaw_service import OpenClawService
from app.services.rocketchat_service import RocketChatService
from app.services.task_service import TaskService
from app.services.webhook_security import WebhookSecurityService

router = APIRouter(tags=["openclaw"])


@router.post(
    "/v1/openclaw/events/transcript",
    response_model=ApiResponse[dict],
    summary="接收 OpenClaw transcript 事件",
    description="OpenClaw Gateway 或插件回调使用。用于把本地 agent 会话转写同步到平台任务和消息体系，必须携带 A2A 签名头。",
)
async def ingest_openclaw_transcript(
    request: Request,
    event: OpenClawTranscriptEvent,
    db: DbDep,
    x_a2a_timestamp: str = Header(...),
    x_a2a_nonce: str = Header(...),
    x_a2a_signature: str = Header(...),
):
    body = await request.body()
    security = WebhookSecurityService(db)
    await security.verify(
        source_system="openclaw",
        secret=settings.OPENCLAW_WEBHOOK_SECRET,
        timestamp=x_a2a_timestamp,
        nonce=x_a2a_nonce,
        signature=x_a2a_signature,
        body=body,
    )
    svc = OpenClawService(db)
    result = await svc.ingest_transcript(
        tenant_id=event.tenant_id,
        session_key=event.session_key,
        event_id=event.event_id,
        text=event.text,
        sender_type=event.sender_type,
        sender_id=event.sender_id,
        task_type=event.task_type,
        metadata=event.metadata,
    )
    return ApiResponse.ok(result)


@router.post(
    "/v1/openclaw/events/approval",
    response_model=ApiResponse[dict],
    summary="接收 OpenClaw 审批事件",
    description="OpenClaw Gateway 或插件回调使用。用于把 agent 触发的人工审批请求同步到平台审批流，必须携带 A2A 签名头。",
)
async def ingest_openclaw_approval(
    request: Request,
    event: OpenClawApprovalEvent,
    db: DbDep,
    x_a2a_timestamp: str = Header(...),
    x_a2a_nonce: str = Header(...),
    x_a2a_signature: str = Header(...),
):
    from app.schemas.integration import ApprovalResponse

    body = await request.body()
    security = WebhookSecurityService(db)
    await security.verify(
        source_system="openclaw",
        secret=settings.OPENCLAW_WEBHOOK_SECRET,
        timestamp=x_a2a_timestamp,
        nonce=x_a2a_nonce,
        signature=x_a2a_signature,
        body=body,
    )
    svc = OpenClawService(db)
    approval = await svc.ingest_approval_request(
        tenant_id=event.tenant_id,
        task_id=event.task_id,
        reason=event.reason,
        external_key=event.external_key,
        requested_by=event.requested_by,
        approver_user_id=event.approver_user_id,
        metadata=event.metadata,
    )
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.post(
    "/v1/openclaw/agents/register",
    response_model=ApiResponse[OpenClawAgentRegistrationResponse],
    summary="为 OpenClaw Agent 注册接入并签发长连接 token",
    description="平台用户或受信任接入工具使用。用于预注册指定 OpenClaw Agent，并返回 MQTT/WS 连接所需的 agent token 和 topic 信息。",
)
async def register_openclaw_agent(
    req: OpenClawAgentRegisterRequest,
    request: Request,
    db: DbDep,
    tenant: TenantDep,
):
    registry = AgentRegistry(db)
    agent_summary = _normalize_agent_summary(req.agent_summary, req.agent_id, {}, req.config_json)
    agent = await registry.register(
        agent_id=req.agent_id,
        tenant_id=tenant["tenant_id"],
        agent_type="federated",
        display_name=req.display_name,
        capabilities=req.capabilities,
        auth_scheme="jwt",
        config_json={"adapter": "openclaw_gateway", **req.config_json, "agent_summary": agent_summary},
        actor_id=tenant.get("sub"),
    )
    auth_token = _build_openclaw_agent_token(tenant["tenant_id"], agent.agent_id, tenant.get("sub"))
    urls = _openclaw_urls(request)
    transport = agent_link_service.transport_payload(tenant["tenant_id"], agent.agent_id, auth_token)
    return ApiResponse.ok(
        OpenClawAgentRegistrationResponse(
            agent_id=agent.agent_id,
            tenant_id=tenant["tenant_id"],
            agent_summary=agent_summary,
            auth_token=auth_token,
            ws_url=urls["ws_url"],
            onboarding_url=urls["onboarding_url"],
            transcript_webhook_url=urls["transcript_webhook_url"],
            approval_webhook_url=urls["approval_webhook_url"],
            message_types=OPENCLAW_AGENT_MESSAGE_TYPES,
            transport=transport["transport"],
            mqtt_broker_url=transport["mqtt_broker_url"],
            mqtt_client_id=transport["mqtt_client_id"],
            mqtt_command_topic=transport["mqtt_command_topic"],
            mqtt_username=transport["mqtt_username"],
            mqtt_password=transport["mqtt_password"],
            presence_url=transport["presence_url"],
            qos=transport["qos"],
            invite_url=f"{settings.PUBLIC_BASE_URL}/v1/agents/invite?token={create_access_token(subject=agent.agent_id, extra={'tenant_id': tenant['tenant_id'], 'agent_id': agent.agent_id, 'scope': 'agent_invite'}, expires_minutes=60*24*7)}",
        )
    )


@router.post(
    "/v1/openclaw/agents/{agent_id}/connect-link",
    response_model=ApiResponse[OpenClawConnectLinkResponse],
    summary="生成可直接转发给 OpenClaw Agent 的接入链接",
    description="平台用户或服务账号使用。用于给某个 Agent 生成可直接转发的一次性接入链接，并返回对应 bootstrap 地址。",
)
async def create_openclaw_connect_link(
    agent_id: str,
    request: Request,
    db: DbDep,
    tenant: TenantDep,
    req: OpenClawConnectLinkRequest | None = None,
):
    urls = _openclaw_urls(request)
    registry = AgentRegistry(db)
    agent = await registry.get(agent_id, tenant["tenant_id"])
    display_name = (
        req.display_name if req and req.display_name is not None
        else agent.display_name if agent
        else agent_id
    )
    capabilities = (
        req.capabilities if req and req.capabilities
        else agent.capabilities if agent
        else {}
    )
    config_json = (
        req.config_json if req and req.config_json
        else agent.config_json if agent
        else {}
    )
    bootstrap_token = _build_openclaw_bootstrap_token(
        tenant["tenant_id"],
        agent_id,
        tenant.get("sub"),
        display_name=display_name,
        capabilities=capabilities,
        config_json=config_json,
    )
    connect_url = f'{urls["onboarding_url"]}?token={bootstrap_token}'
    bootstrap_url = f'{urls["base_url"]}/v1/openclaw/agents/bootstrap?token={bootstrap_token}'
    return ApiResponse.ok(
        OpenClawConnectLinkResponse(
            agent_id=agent_id,
            tenant_id=tenant["tenant_id"],
            connect_url=connect_url,
            bootstrap_url=bootstrap_url,
            expires_in_seconds=settings.JWT_EXPIRE_MINUTES * 60,
        )
    )


@router.get(
    "/v1/openclaw/agents/onboarding",
    response_model=ApiResponse[OpenClawOnboardingInfo],
    summary="查看 OpenClaw Agent 接入信息",
    description="平台 UI、文档页或联调脚本使用。用于查看当前部署对外暴露的 OpenClaw 接入地址、Webhook、MQTT broker 和 topic 模板。",
)
async def get_openclaw_onboarding_info(request: Request):
    urls = _openclaw_urls(request)
    return ApiResponse.ok(
        OpenClawOnboardingInfo(
            ws_url=urls["ws_url"],
            onboarding_url=urls["onboarding_url"],
            register_url=urls["register_url"],
            transcript_webhook_url=urls["transcript_webhook_url"],
            approval_webhook_url=urls["approval_webhook_url"],
            message_types=OPENCLAW_AGENT_MESSAGE_TYPES,
            auth_scheme="JWT Bearer token issued by /v1/openclaw/agents/register",
            transport=settings.AGENT_LINK_TRANSPORT,
            mqtt_broker_url=settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL,
            mqtt_topic_pattern=f'{settings.MQTT_BASE_TOPIC}/<tenant_id>/agents/<agent_id>/commands',
            presence_url=urls["presence_url"],
            public_connect_url=urls["public_connect_url"],
            self_register_url=urls["self_register_url"],
        )
    )


@router.get(
    "/v1/openclaw/agents/bootstrap",
    response_model=ApiResponse[OpenClawAgentRegistrationResponse],
    summary="通过一次性接入 token 获取 OpenClaw Agent 启动配置",
    description="通过接入链接中的一次性 token 换取 agent token、MQTT topic 和 Webhook 地址。",
)
async def get_openclaw_bootstrap(token: str, request: Request):
    tenant_id = None
    agent_id = None
    try:
        payload = decode_access_token(token)
        if payload.get("scope") != "openclaw_bootstrap":
            raise HTTPException(status_code=401, detail="bootstrap token 非法")
        tenant_id = payload.get("tenant_id")
        agent_id = payload.get("agent_id")
        if not tenant_id or not agent_id:
            raise HTTPException(status_code=401, detail="bootstrap token 缺少 tenant_id 或 agent_id")
        display_name = payload.get("display_name") or agent_id
        capabilities = payload.get("capabilities") or {}
        config_json = payload.get("config_json") or {}

        urls = _openclaw_urls(request)
        async with AsyncSessionLocal() as db:
            registry = AgentRegistry(db)
            await registry.register(
                agent_id=agent_id,
                tenant_id=tenant_id,
                agent_type="federated",
                display_name=display_name,
                capabilities=capabilities,
                auth_scheme="jwt",
                config_json={"adapter": "openclaw_gateway", **config_json},
                actor_id=payload.get("sub"),
            )
            await db.commit()
        auth_token = _build_openclaw_agent_token(tenant_id, agent_id, payload.get("sub"))
        transport = agent_link_service.transport_payload(tenant_id, agent_id, auth_token)
        return ApiResponse.ok(
            OpenClawAgentRegistrationResponse(
                agent_id=agent_id,
                tenant_id=tenant_id,
                agent_summary=_normalize_agent_summary(config_json.get("agent_summary"), agent_id, {}, config_json),
                auth_token=auth_token,
                ws_url=urls["ws_url"],
                onboarding_url=f'{urls["onboarding_url"]}?token={token}',
                transcript_webhook_url=urls["transcript_webhook_url"],
                approval_webhook_url=urls["approval_webhook_url"],
                message_types=OPENCLAW_AGENT_MESSAGE_TYPES,
                transport=transport["transport"],
                mqtt_broker_url=transport["mqtt_broker_url"],
                mqtt_client_id=transport["mqtt_client_id"],
                mqtt_command_topic=transport["mqtt_command_topic"],
                mqtt_username=transport["mqtt_username"],
                mqtt_password=transport["mqtt_password"],
                presence_url=transport["presence_url"],
                qos=transport["qos"],
            )
        )
    except HTTPException as exc:
        await _record_error_event(
            source_side="platform",
            stage="bootstrap",
            category="auth" if exc.status_code == 401 else "request",
            summary="bootstrap 获取失败",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=exc.status_code,
            detail=str(exc.detail),
        )
        raise
    except Exception as exc:
        await _record_error_event(
            source_side="platform",
            stage="bootstrap",
            category="server",
            summary="bootstrap 服务异常",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=500,
            detail=str(exc),
        )
        raise


@router.post(
    "/v1/openclaw/agents/{agent_id}/dispatch",
    response_model=ApiResponse[OpenClawDispatchResponse],
    summary="将任务直接下发给在线 OpenClaw Agent",
    description="平台内部调度器或运维联调使用。用于把已有 task 强制指定给某个 OpenClaw Agent 并通过 Agent Link 下发。",
)
async def dispatch_openclaw_task(
    agent_id: str,
    req: OpenClawDispatchRequest,
    db: DbDep,
    tenant: TenantDep,
):
    task = await TaskService(db).get(req.task_id, tenant["tenant_id"])
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.target_agent_id != agent_id:
        await db.execute(
            update(Task)
            .where(Task.task_id == task.task_id, Task.tenant_id == tenant["tenant_id"])
            .values(target_agent_id=agent_id)
        )
        task.target_agent_id = agent_id

    auth_token = _build_openclaw_agent_token(tenant["tenant_id"], agent_id, tenant.get("sub"))
    dispatch = await agent_link_service.dispatch_task(task, auth_token)
    return ApiResponse.ok(
        OpenClawDispatchResponse(
            task_id=task.task_id,
            agent_id=agent_id,
            dispatched=dispatch.dispatched,
            connection_id=None,
            reason=dispatch.reason,
        )
    )


@router.post(
    "/v1/rocketchat/webhook",
    response_model=ApiResponse[dict],
    summary="处理 Rocket.Chat 入站 Webhook",
    description="Rocket.Chat 连接器或网关回调使用。用于把聊天室消息转换成平台 context/task 并触发路由，必须携带 A2A 签名头。",
)
async def rocketchat_webhook(
    request: Request,
    db: DbDep,
    x_a2a_timestamp: str = Header(...),
    x_a2a_nonce: str = Header(...),
    x_a2a_signature: str = Header(...),
):
    from app.schemas.integration import RocketChatWebhookPayload

    body = await request.body()
    security = WebhookSecurityService(db)
    await security.verify(
        source_system="rocket_chat",
        secret=settings.ROCKETCHAT_WEBHOOK_SECRET,
        timestamp=x_a2a_timestamp,
        nonce=x_a2a_nonce,
        signature=x_a2a_signature,
        body=body,
    )
    payload = RocketChatWebhookPayload.model_validate_json(body)
    svc = RocketChatService(db)
    result = await svc.handle_incoming_message(
        tenant_id=payload.tenant_id,
        room_id=payload.room_id,
        text=payload.text,
        sender_id=payload.sender_id,
        sender_name=payload.sender_name,
        server_url=payload.server_url,
        metadata={"message_id": payload.message_id, **payload.metadata},
    )
    return ApiResponse.ok(result)
