"""
版块 4-7：OpenClaw、Rocket.Chat、审批、投递、SSE 与计量接口。
"""
import asyncio
import hashlib
import io
import json
import tarfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import select, update

from app.api.deps import DbDep, TenantDep
from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token, decode_access_token
from app.models.delivery import Delivery
from app.models.agent import Agent
from app.models.tenant import Tenant
from app.models.task import Task
from app.schemas.common import ApiResponse
from app.schemas.integration import (
    AgentLinkManifestResponse,
    AgentLinkErrorEventResponse,
    AgentLinkErrorReportRequest,
    AgentLinkInstallReportRequest,
    AgentLinkSendMessageRequest,
    AgentLinkMessageRequest,
    AgentLinkPresenceRequest,
    AgentLinkSelfRegisterRequest,
    DocsAgentMessageTestRequest,
    ApprovalCreateRequest,
    ApprovalResolveRequest,
    ApprovalResponse,
    OpenClawConnectLinkRequest,
    OpenClawConnectLinkResponse,
    OpenClawAgentRegisterRequest,
    OpenClawAgentRegistrationResponse,
    OpenClawDispatchRequest,
    OpenClawDispatchResponse,
    OpenClawOnboardingInfo,
    DeliveryCreateRequest,
    DeliveryResponse,
    MeteringSummaryItem,
    OpenClawApprovalEvent,
    OpenClawTranscriptEvent,
    RocketChatWebhookPayload,
)
from app.schemas.message import MessagePart, MessageSendRequest
from app.services.context_service import ContextService
from app.services.agent_registry import AgentRegistry
from app.services.agent_link_service import agent_link_service
from app.services.approval_service import ApprovalService
from app.services.delivery_service import DeliveryService
from app.services.error_event_service import ErrorEventService
from app.services.openclaw_gateway_service import (
    OPENCLAW_AGENT_MESSAGE_TYPES,
    OpenClawConnection,
    openclaw_gateway_broker,
)
from app.services.metering_service import MeteringService
from app.services.openclaw_service import OpenClawService
from app.services.rocketchat_service import RocketChatService
from app.services.stream_service import task_event_broker
from app.services.task_service import TaskService
from app.services.webhook_security import WebhookSecurityService
from app.api.routes_messages import create_and_dispatch_message_task

router = APIRouter(tags=["integrations"])
OPENCLAW_CONNECT_MD_PATH = Path(__file__).resolve().parents[1] / "static" / "openclaw_agent_connect.md"
DBIM_MQTT_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "dbim-mqtt-plugin"


def _external_base_url(request: Request | None = None) -> str:
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL
    if request is None:
        return "http://127.0.0.1:1880"
    return str(request.base_url).rstrip("/")


def _openclaw_urls(request: Request | None = None) -> dict[str, str]:
    base_url = _external_base_url(request)
    ws_scheme = "wss" if base_url.startswith("https://") else "ws"
    ws_base = base_url.split("://", 1)[1]
    return {
        "base_url": base_url,
        "ws_url": f"{ws_scheme}://{ws_base}/ws/openclaw/gateway",
        "public_connect_url": f"{base_url}/agent-link/connect",
        "onboarding_url": f"{base_url}/openclaw/agents/connect",
        "register_url": f"{base_url}/v1/openclaw/agents/register",
        "self_register_url": f"{base_url}/v1/agent-link/self-register",
        "plugin_download_url": f"{base_url}/agent-link/plugins/dbim-mqtt.tar.gz",
        "openclaw_install_script_url": f"{base_url}/agent-link/install/openclaw-dbim-mqtt.sh",
        "agent_prompt_url": f"{base_url}/agent-link/prompt",
        "install_report_url": f"{base_url}/v1/agent-link/install-report",
        "presence_url": f"{base_url}/v1/agent-link/presence",
        "transcript_webhook_url": f"{base_url}/v1/openclaw/events/transcript",
        "approval_webhook_url": f"{base_url}/v1/openclaw/events/approval",
    }


def _build_openclaw_agent_token(tenant_id: str, agent_id: str, subject: str | None) -> str:
    return agent_link_service.build_agent_token(tenant_id, agent_id, subject)


def _normalize_openclaw_agent_id(agent_id: str) -> str:
    value = (agent_id or "").strip()
    if not value:
        raise HTTPException(status_code=422, detail="agent_id 不能为空")
    return value if ":" in value else f"openclaw:{value}"


def _owner_profile_key(owner_profile: dict[str, Any]) -> str:
    for key in ("owner_id", "user_id", "email", "username", "name"):
        value = str(owner_profile.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    raw_text = str(owner_profile.get("raw_text") or owner_profile.get("user_md") or "").strip()
    if raw_text:
        return f"user_md:{raw_text[:4096]}"
    return "anonymous:default"


def _owner_tenant_id(owner_profile: dict[str, Any]) -> str:
    digest = hashlib.sha256(_owner_profile_key(owner_profile).encode("utf-8")).hexdigest()[:16]
    return f"owner_{digest}"


def _owner_display_name(owner_profile: dict[str, Any]) -> str:
    for key in ("name", "username", "email", "owner_id", "user_id"):
        value = str(owner_profile.get(key) or "").strip()
        if value:
            return value[:120]
    return "OpenClaw Owner"


def _truncate(value: str | None, limit: int = 1000) -> str | None:
    if value is None:
        return None
    value = str(value)
    return value if len(value) <= limit else value[:limit] + "..."


def _error_payload_request_path(request: Request | None) -> str | None:
    if not request:
        return None
    try:
        return request.url.path
    except Exception:
        return None


async def _record_error_event(
    *,
    source_side: str,
    stage: str,
    category: str,
    summary: str,
    request: Request | None = None,
    tenant_id: str | None = None,
    agent_id: str | None = None,
    detail: str | None = None,
    status_code: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    await ErrorEventService.record_out_of_band(
        source_side=source_side,
        stage=stage,
        category=category,
        summary=summary,
        tenant_id=tenant_id,
        agent_id=agent_id,
        detail=_truncate(detail, 4000),
        status_code=status_code,
        request_path=_error_payload_request_path(request),
        payload=payload,
    )


async def _require_agent_link_identity(request: Request, stage: str) -> tuple[str, dict[str, Any], str, str]:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        await _record_error_event(
            source_side="platform",
            stage=stage,
            category="auth",
            summary="缺少 Bearer token",
            request=request,
            status_code=401,
        )
        raise HTTPException(status_code=401, detail="缺少 Bearer token")

    token = auth.split(" ", 1)[1]
    try:
        payload = decode_access_token(token)
    except HTTPException as exc:
        await _record_error_event(
            source_side="platform",
            stage=stage,
            category="auth",
            summary="Agent token 校验失败",
            request=request,
            status_code=exc.status_code,
            detail=str(exc.detail),
        )
        raise

    tenant_id = payload.get("tenant_id")
    agent_id = payload.get("agent_id")
    if payload.get("scope") != "openclaw_gateway" or not tenant_id or not agent_id:
        await _record_error_event(
            source_side="platform",
            stage=stage,
            category="auth",
            summary="Agent token scope 或身份信息非法",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=401,
        )
        raise HTTPException(status_code=401, detail="Agent Link token 非法")
    return token, payload, tenant_id, agent_id


async def _ensure_owner_tenant(db, tenant_id: str, owner_profile: dict[str, Any]) -> None:
    existing = await db.get(Tenant, tenant_id)
    if existing:
        return
    db.add(
        Tenant(
            tenant_id=tenant_id,
            name=_owner_display_name(owner_profile),
            status="ACTIVE",
            config_json={
                "owner_profile": owner_profile,
                "tenant_model": "owner_profile",
                "source": "agent_link_self_register",
            },
        )
    )
    await db.flush()


def _build_openclaw_bootstrap_token(
    tenant_id: str,
    agent_id: str,
    subject: str | None,
    display_name: str | None = None,
    capabilities: dict[str, Any] | None = None,
    config_json: dict[str, Any] | None = None,
) -> str:
    return create_access_token(
        subject=subject or agent_id,
        extra={
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "scope": "openclaw_bootstrap",
            "display_name": display_name,
            "capabilities": capabilities or {},
            "config_json": config_json or {},
        },
    )


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
    response_model=ApiResponse[ApprovalResponse],
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
    agent = await registry.register(
        agent_id=req.agent_id,
        tenant_id=tenant["tenant_id"],
        agent_type="federated",
        display_name=req.display_name,
        capabilities=req.capabilities,
        auth_scheme="jwt",
        config_json={"adapter": "openclaw_gateway", **req.config_json},
        actor_id=tenant.get("sub"),
    )
    auth_token = _build_openclaw_agent_token(tenant["tenant_id"], agent.agent_id, tenant.get("sub"))
    urls = _openclaw_urls(request)
    transport = agent_link_service.transport_payload(tenant["tenant_id"], agent.agent_id, auth_token)
    return ApiResponse.ok(
        OpenClawAgentRegistrationResponse(
            agent_id=agent.agent_id,
            tenant_id=tenant["tenant_id"],
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
        )
    )


@router.post(
    "/v1/openclaw/agents/{agent_id}/connect-link",
    response_model=ApiResponse[OpenClawConnectLinkResponse],
    summary="生成可直接转发给 OpenClaw Agent 的接入链接",
    description="平台用户或服务账号使用。用于给某个 Agent 生成一次性兼容接入链接，适合旧版 connect_url/bootstrap 流程。",
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
    "/v1/agent-link/manifest",
    response_model=ApiResponse[AgentLinkManifestResponse],
    summary="公开 Agent Link 接入 manifest",
    description="任何 agent 或安装脚本可匿名读取。用于发现公开接入 URL、插件包、安装脚本、复制给 agent 的 prompt 和 MQTT 对外地址。",
)
async def get_agent_link_manifest(request: Request):
    urls = _openclaw_urls(request)
    return ApiResponse.ok(
        AgentLinkManifestResponse(
            public_connect_url=urls["public_connect_url"],
            self_register_url=urls["self_register_url"],
            onboarding_url=urls["onboarding_url"],
            plugin_download_url=urls["plugin_download_url"],
            openclaw_install_script_url=urls["openclaw_install_script_url"],
            agent_prompt_url=urls["agent_prompt_url"],
            transport=settings.AGENT_LINK_TRANSPORT,
            mqtt_public_broker_url=settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL,
            notes=[
                "这是面向 agent 的公开接入 manifest，不要求主人理解或提供 tenant_id。",
                "OpenClaw agent 应先安装 dbim-mqtt 插件，再用 public_connect_url 自注册并建立 MQTT 长连接。",
                "安装插件、修改本地 OpenClaw 配置或缺少 agent_id 时，应向主人确认。",
            ],
        )
    )


@router.post(
    "/v1/agent-link/self-register",
    response_model=ApiResponse[OpenClawAgentRegistrationResponse],
    summary="公开 Agent Link 自注册",
    description="OpenClaw dbim-mqtt 插件或其他 agent 客户端匿名调用。读取本地 USER.md 后提交 owner_profile，平台自动注册、认证并返回 MQTT 长连接配置。",
)
async def agent_link_self_register(req: AgentLinkSelfRegisterRequest, request: Request):
    agent_id = None
    tenant_id = None
    owner_profile = {
        **req.owner_profile,
        "registration_model": "owner_profile",
    }
    try:
        agent_id = _normalize_openclaw_agent_id(req.agent_id)
        requested_tenant_id = _owner_tenant_id(owner_profile)
        display_name = req.display_name or agent_id
        urls = _openclaw_urls(request)

        async with AsyncSessionLocal() as db:
            try:
                existing_result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
                existing_agent = existing_result.scalar_one_or_none()
                tenant_id = existing_agent.tenant_id if existing_agent else requested_tenant_id
                if existing_agent and tenant_id != requested_tenant_id:
                    owner_profile = {
                        **owner_profile,
                        "requested_owner_tenant_id": requested_tenant_id,
                        "resolved_owner_tenant_id": tenant_id,
                        "tenant_resolution": "existing_agent_id",
                    }
                await _ensure_owner_tenant(db, tenant_id, owner_profile)
                registry = AgentRegistry(db)
                agent = await registry.register(
                    agent_id=agent_id,
                    tenant_id=tenant_id,
                    agent_type="federated",
                    display_name=display_name,
                    capabilities=req.capabilities,
                    auth_scheme="jwt",
                    config_json={
                        "adapter": "openclaw_gateway",
                        "registration_mode": "self_register",
                        "owner_profile": owner_profile,
                        **req.config_json,
                    },
                    actor_id=str(owner_profile.get("user_id") or owner_profile.get("owner_id") or agent_id),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

        subject = str(owner_profile.get("user_id") or owner_profile.get("owner_id") or agent_id)
        auth_token = _build_openclaw_agent_token(tenant_id, agent.agent_id, subject)
        transport = agent_link_service.transport_payload(tenant_id, agent.agent_id, auth_token)
        return ApiResponse.ok(
            OpenClawAgentRegistrationResponse(
                agent_id=agent.agent_id,
                tenant_id=tenant_id,
                auth_token=auth_token,
                ws_url=urls["ws_url"],
                onboarding_url=urls["public_connect_url"],
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
            stage="self_register",
            category="request",
            summary="公开自注册请求非法",
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
            stage="self_register",
            category="server",
            summary="公开自注册失败",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=500,
            detail=str(exc),
            payload={"owner_profile_key": _owner_profile_key(owner_profile)},
        )
        raise


@router.post(
    "/v1/agent-link/install-report",
    response_model=ApiResponse[dict],
    summary="公开安装结果上报",
    description="安装脚本后台检查器匿名调用。用于把安装成功或失败结果回传到平台观测链路，适配沙盒 agent 无法直接读取宿主机状态文件或 systemd 日志的场景。",
)
async def agent_link_install_report(req: AgentLinkInstallReportRequest, request: Request):
    agent_id = _normalize_openclaw_agent_id(req.agent_id)
    owner_profile = {
        **req.owner_profile,
        "report_model": "install_result",
    }
    requested_tenant_id = _owner_tenant_id(owner_profile)

    async with AsyncSessionLocal() as db:
        existing_result = await db.execute(select(Agent).where(Agent.agent_id == agent_id))
        existing_agent = existing_result.scalar_one_or_none()
        tenant_id = existing_agent.tenant_id if existing_agent else requested_tenant_id

    await _record_error_event(
        source_side="agent",
        stage=req.stage,
        category="install",
        summary=req.summary,
        request=request,
        tenant_id=tenant_id,
        agent_id=agent_id,
        detail=req.detail,
        payload={
            "status": req.status,
            "owner_profile_key": _owner_profile_key(owner_profile),
            **req.metadata,
        },
    )
    return ApiResponse.ok(
        {
            "recorded": True,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "status": req.status,
            "stage": req.stage,
        }
    )


@router.get(
    "/v1/openclaw/agents/bootstrap",
    response_model=ApiResponse[OpenClawAgentRegistrationResponse],
    summary="通过一次性接入 token 获取 OpenClaw Agent 启动配置",
    description="旧版 OpenClaw 接入链接或兼容插件使用。用 connect_url 中的一次性 token 换取 agent token、MQTT topic 和 Webhook 地址。",
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
    "/v1/agent-link/presence",
    response_model=ApiResponse[dict],
    summary="Agent Link 心跳上报",
    description="已接入的 agent 插件使用。定期上报在线状态、元数据并触发 pending 消息补发；需要 agent scope Bearer token。",
)
async def agent_link_presence(req: AgentLinkPresenceRequest, request: Request):
    token, _, tenant_id, agent_id = await _require_agent_link_identity(request, "presence")
    try:
        state = await agent_link_service.heartbeat(tenant_id, agent_id, req.status, req.metadata, auth_token=token)
        return ApiResponse.ok(state)
    except Exception as exc:
        await _record_error_event(
            source_side="platform",
            stage="presence",
            category="server",
            summary="presence 处理失败",
            request=request,
            tenant_id=tenant_id,
            agent_id=agent_id,
            status_code=500,
            detail=str(exc),
        )
        raise


@router.post(
    "/v1/agent-link/messages",
    response_model=ApiResponse[dict],
    summary="Agent Link 上行消息入口",
    description="已接入的 agent 插件使用。用于回传 task.ack、task.update、审批结果或其他上行事件；需要 agent scope Bearer token。",
)
async def agent_link_message(req: AgentLinkMessageRequest, request: Request):
    _, payload, tenant_id, agent_id = await _require_agent_link_identity(request, "agent_message")

    connection = OpenClawConnection(
        connection_id=f"http_{agent_id}",
        tenant_id=tenant_id,
        agent_id=agent_id,
        websocket=None,
        metadata={"sub": payload.get("sub"), "transport": "http"},
    )
    async with AsyncSessionLocal() as db:
        try:
            response = await openclaw_gateway_broker.handle_agent_message(db, connection, req.payload)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            await _record_error_event(
                source_side="platform",
                stage="agent_message",
                category="server",
                summary="Agent 上行消息处理失败",
                request=request,
                tenant_id=tenant_id,
                agent_id=agent_id,
                status_code=500,
                detail=str(exc),
                payload={"message_type": req.payload.get("type")},
            )
            raise
    return ApiResponse.ok(response)


@router.post(
    "/v1/agent-link/messages/send",
    response_model=ApiResponse[dict],
    summary="Agent Link agent-to-agent 发消息",
    description="已接入的 agent 使用自己的 agent token 调用。用于模拟或实现 agent 向另一个 agent 发消息，平台负责创建任务、路由和下发。",
)
async def agent_link_send_message(req: AgentLinkSendMessageRequest, request: Request):
    _, _, tenant_id, source_agent_id = await _require_agent_link_identity(request, "agent_send_message")
    if req.target_agent_id == source_agent_id:
        raise HTTPException(status_code=422, detail="target_agent_id 不能等于当前 agent")

    message_req = MessageSendRequest(
        context_id=req.context_id,
        target_agent_id=req.target_agent_id,
        parts=req.parts,
        metadata={
            **req.metadata,
            "source": "agent-link",
            "source_agent_id": source_agent_id,
        },
        idempotency_key=req.idempotency_key,
    )
    async with AsyncSessionLocal() as db:
        try:
            response = await create_and_dispatch_message_task(
                message_req,
                db,
                {
                    "tenant_id": tenant_id,
                    "sub": source_agent_id,
                    "token_type": "agent",
                    "agent_id": source_agent_id,
                },
                initiator_agent_id=source_agent_id,
                source_system="agent-link",
            )
        except Exception as exc:
            await db.rollback()
            await _record_error_event(
                source_side="platform",
                stage="agent_send_message",
                category="server",
                summary="Agent-to-Agent 发消息失败",
                request=request,
                tenant_id=tenant_id,
                agent_id=source_agent_id,
                status_code=500,
                detail=str(exc),
                payload={"target_agent_id": req.target_agent_id},
            )
            raise
    return ApiResponse.ok(response.model_dump())


@router.post(
    "/v1/agent-link/errors",
    response_model=ApiResponse[dict],
    summary="Agent Link 错误上报",
    description="已接入的 agent 插件使用。用于把 MQTT、presence、task.update、OpenClaw 本地调用等失败阶段回传到平台错误记录，便于在 Docs 中按 agent 查询。",
)
async def agent_link_report_error(req: AgentLinkErrorReportRequest, request: Request):
    _, _, tenant_id, agent_id = await _require_agent_link_identity(request, "agent_report_error")
    await _record_error_event(
        source_side="agent",
        stage=req.stage,
        category=req.category,
        summary=req.summary,
        request=request,
        tenant_id=tenant_id,
        agent_id=agent_id,
        detail=req.detail,
        status_code=400 if req.category == "request" else None,
        payload=req.metadata,
    )
    return ApiResponse.ok({"recorded": True, "agent_id": agent_id, "tenant_id": tenant_id})


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


@router.post(
    "/v1/approvals",
    response_model=ApiResponse[ApprovalResponse],
    status_code=201,
    summary="创建审批",
    description="平台组件、Agent 或人工流程使用。用于把任务切换到待审批状态，并记录审批人、原因和外部关联键。",
)
async def create_approval(req: ApprovalCreateRequest, db: DbDep, tenant: TenantDep):
    svc = ApprovalService(db)
    approval = await svc.create(
        tenant_id=tenant["tenant_id"],
        task_id=req.task_id,
        approver_user_id=req.approver_user_id,
        requested_by=tenant.get("sub"),
        reason=req.reason,
        external_key=req.external_key,
        metadata=req.metadata,
    )
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.get(
    "/v1/approvals/{approval_id}",
    response_model=ApiResponse[ApprovalResponse],
    summary="查询审批",
    description="前端审批页或调试脚本使用。用于查看当前租户下单个审批项的状态、审批人、任务关联和处理结果。",
)
async def get_approval(approval_id: str, db: DbDep, tenant: TenantDep):
    svc = ApprovalService(db)
    approval = await svc.get(approval_id, tenant["tenant_id"])
    if not approval:
        raise HTTPException(status_code=404, detail="审批不存在")
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.post(
    "/v1/approvals/{approval_id}/resolve",
    response_model=ApiResponse[ApprovalResponse],
    summary="处理审批",
    description="审批人、前端或受控自动化流程使用。用于批准或拒绝待审批项，并根据结果推进关联任务状态。",
)
async def resolve_approval(approval_id: str, req: ApprovalResolveRequest, db: DbDep, tenant: TenantDep):
    if req.decision not in {"APPROVED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="decision 必须是 APPROVED 或 REJECTED")
    svc = ApprovalService(db)
    try:
        approval = await svc.resolve(
            approval_id=approval_id,
            tenant_id=tenant["tenant_id"],
            decision=req.decision,
            note=req.note,
            actor_id=tenant.get("sub"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 422, detail=str(exc))
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.post(
    "/v1/approvals/expire",
    response_model=ApiResponse[dict],
    summary="批量过期待审批项",
    description="定时任务或运维脚本使用。用于扫描当前租户下超时未处理的审批项并批量标记过期。",
)
async def expire_approvals(db: DbDep, tenant: TenantDep):
    svc = ApprovalService(db)
    approvals = await svc.expire_pending(tenant["tenant_id"])
    return ApiResponse.ok({"expired_count": len(approvals)})


@router.post(
    "/v1/deliveries",
    response_model=ApiResponse[DeliveryResponse],
    status_code=201,
    summary="创建投递任务",
    description="平台组件或集成适配器使用。用于创建一条对外投递记录，支持幂等键、重试次数和 trace_id。",
)
async def create_delivery(req: DeliveryCreateRequest, db: DbDep, tenant: TenantDep):
    svc = DeliveryService(db)
    delivery = await svc.enqueue(
        tenant_id=tenant["tenant_id"],
        target_channel=req.target_channel,
        target_ref=req.target_ref,
        payload=req.payload,
        task_id=req.task_id,
        trace_id=req.trace_id,
        idempotency_key=req.idempotency_key,
        max_attempts=req.max_attempts,
    )
    return ApiResponse.ok(_delivery_resp(delivery))


@router.get(
    "/v1/deliveries/dlq",
    response_model=ApiResponse[list[DeliveryResponse]],
    summary="查看 DLQ",
    description="运维界面或告警处理脚本使用。用于查看当前租户下已经进入 DEAD 状态的投递失败记录。",
)
async def list_dlq(db: DbDep, tenant: TenantDep):
    svc = DeliveryService(db)
    deliveries = await svc.list_dead(tenant["tenant_id"])
    return ApiResponse.ok([_delivery_resp(item) for item in deliveries])


@router.post(
    "/v1/deliveries/process-due",
    response_model=ApiResponse[dict],
    summary="处理待投递或待重试任务",
    description="后台 worker、定时任务或手工运维使用。用于处理到期的投递任务并按策略重试或进入 DLQ。",
)
async def process_due_deliveries(db: DbDep, tenant: TenantDep, limit: int = 20):
    svc = DeliveryService(db)
    try:
        deliveries = await svc.process_due(tenant["tenant_id"], limit=limit)
    except Exception as exc:
        return ApiResponse.ok(
            {
                "processed_count": 0,
                "statuses": [],
                "warning": f"delivery process_due fallback: {exc}",
            }
        )
    return ApiResponse.ok({"processed_count": len(deliveries), "statuses": [item.status for item in deliveries]})


@router.post(
    "/v1/deliveries/{delivery_id}/replay",
    response_model=ApiResponse[DeliveryResponse],
    summary="重放 DLQ 投递",
    description="运维人员或恢复脚本使用。用于把指定 DEAD 投递重新放回待处理队列，便于修复外部故障后重试。",
)
async def replay_delivery(delivery_id: str, db: DbDep, tenant: TenantDep):
    svc = DeliveryService(db)
    try:
        delivery = await svc.replay_dead(delivery_id, tenant["tenant_id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ApiResponse.ok(_delivery_resp(delivery))


@router.get(
    "/v1/tasks/{task_id}/subscribe",
    summary="订阅任务状态 SSE",
    description="前端任务详情页或调试工具使用。用于通过 Server-Sent Events 实时接收任务状态变化和相关事件。",
)
async def subscribe_task(task_id: str, db: DbDep, tenant: TenantDep):
    task = await TaskService(db).get(task_id, tenant["tenant_id"])
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        queue = task_event_broker.subscribe(task_id)
        try:
            yield "event: ready\ndata: {\"status\": \"subscribed\"}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            task_event_broker.unsubscribe(task_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _agent_link_connect_markdown(request: Request) -> str:
    urls = _openclaw_urls(request)
    token = request.query_params.get("token")
    token_section = ""
    if token:
        token_section = f"""
## 兼容一次性 Token

当前 URL 带有一次性 token。你可以直接读取：

```text
{urls["base_url"]}/v1/openclaw/agents/bootstrap?token={token}
```

如果你已经安装 `dbim-mqtt`，也可以把当前完整 URL 写入插件配置的 `connectUrl` 或 `connectUrlFile`。
"""

    return f"""# Agent Link 接入指令

这是发给 agent 的执行页，不是普通说明页。目标只有一件事：把当前 OpenClaw agent 接入 A2A Hub，并在完成后把结果回报给主人。

如果主人更适合直接复制一段任务给你，使用：

```text
{urls["agent_prompt_url"]}
```

## 你要完成的结果

1. 安装或升级 `dbim-mqtt` 插件。
2. 启用 `channels.dbim_mqtt`，必要时在同一个网关里追加多个 agent 实例。
3. 读取本机 `USER.md` 自注册。
4. 连上 MQTT 并让对应 agent 的 `state.json` 变成 `online`。
5. 把安装结果和错误摘要回给主人。

## 平台地址

```text
public_connect_url={urls["public_connect_url"]}
self_register_url={urls["self_register_url"]}
plugin_download_url={urls["plugin_download_url"]}
openclaw_install_script_url={urls["openclaw_install_script_url"]}
mqtt_public_broker_url={settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL}
presence_url={urls["presence_url"]}
```

## 执行约束

1. 先确认本地 OpenClaw agent id，例如 `mia`。如果无法判断，询问主人。
2. 如果需要安装插件或修改 `~/.openclaw/openclaw.json`，先向主人说明将要执行的动作。
3. 不要向主人索要 tenant_id。平台会根据 `USER.md` 自动派生内部租户。
4. 不要把 `auth_token`、MQTT password 或完整 bootstrap 响应发到公开聊天窗口。
5. 如果安装命令触发 OpenClaw Gateway 重启，当前对话可能中断；重启后继续执行“安装后检查”，不要让主人重新分析问题。
6. 如果出现连接失败、401、500、无法回复等问题，优先给主人回报“失败阶段 + 错误摘要 + 最近相关日志”。
7. 最后必须给主人报告：插件版本、Gateway 是否运行、对应 agent 的 `state.json` 是否 online、agentId、tenantId、MQTT topic，以及是否已完成一次平台消息测试。

## 推荐自动安装

在 OpenClaw 所在机器执行：

```bash
AGENT_ID=<local-agent-id> \\
CONNECT_URL="{urls["public_connect_url"]}" \\
curl -fsSL "{urls["openclaw_install_script_url"]}" | bash
```

`AGENT_ID` 必须是本机 OpenClaw 的短 agent id，例如 `mia`、`ava`。如果你无法确认，不要猜测，先询问主人。

自动安装脚本会：

1. 下载 `dbim-mqtt` 插件包。
2. 如果本地已有 `dbim-mqtt`，先备份旧目录，再安装平台提供的新版本。
3. 执行 `npm install --omit=dev`。
4. 备份并更新 `~/.openclaw/openclaw.json`。
5. 在 `channels.dbim_mqtt.instances` 中新增或更新当前 agent 实例。
6. 异步延迟重启 `openclaw-gateway.service`；如果没有 systemd user service，会提示手动重启。

如果你看到 `channels.dbim_mqtt: unknown channel id: dbim_mqtt`，说明本机 OpenClaw 还没有识别到带 `dbim_mqtt` channel 声明的插件 manifest，或配置先于插件安装生效。也要检查日志里是否有 `world-writable path`，这种情况下 OpenClaw 会出于安全原因阻止加载插件。重新执行上面的自动安装脚本；脚本会先安装新插件包、修正插件目录权限，再写入 `channels.dbim_mqtt` 配置。

## 手工安装（仅在自动安装失败时使用）

```bash
mkdir -p ~/.openclaw/plugins ~/.openclaw/channels/dbim_mqtt
curl -fsSL "{urls["plugin_download_url"]}" -o /tmp/dbim-mqtt.tar.gz
[ ! -d ~/.openclaw/plugins/dbim-mqtt ] || mv ~/.openclaw/plugins/dbim-mqtt ~/.openclaw/plugins/dbim-mqtt.bak.$(date +%Y%m%d%H%M%S)
mkdir -p ~/.openclaw/plugins/dbim-mqtt
tar -xzf /tmp/dbim-mqtt.tar.gz -C ~/.openclaw/plugins/dbim-mqtt
cd ~/.openclaw/plugins/dbim-mqtt
npm install --omit=dev
chmod -R u=rwX,go=rX ~/.openclaw/plugins/dbim-mqtt
```

然后在 `~/.openclaw/openclaw.json` 中启用。单 agent 可继续写顶层字段；如果同一个 OpenClaw Gateway 里要接多个 agent，推荐使用 `instances`：

```json
{{
  "plugins": {{
    "allow": ["dbim-mqtt"],
    "load": {{
      "paths": ["~/.openclaw/plugins/dbim-mqtt"]
    }},
    "entries": {{
      "dbim-mqtt": {{
        "enabled": true
      }}
    }}
  }},
  "channels": {{
    "dbim_mqtt": {{
      "enabled": true,
      "replyMode": "openclaw-agent",
      "recordOpenClawSession": true,
      "instances": [
        {{
          "localAgentId": "<local-agent-id>",
          "agentId": "<local-agent-id>",
          "connectUrl": "{urls["public_connect_url"]}",
          "userProfileFile": "~/.openclaw/workspace-<local-agent-id>/USER.md",
          "stateFile": "~/.openclaw/channels/dbim_mqtt/<local-agent-id>/state.json"
        }}
      ]
    }}
  }}
}}
```

## 自注册协议

插件安装后会自动执行。其他 agent 也可以按这个协议实现：

```http
POST {urls["self_register_url"]}
Content-Type: application/json

{{
  "agent_id": "openclaw:<local-agent-id>",
  "display_name": "<LOCAL_AGENT_ID>",
  "capabilities": {{"analysis": true, "generic": true}},
  "config_json": {{"workspace": "<local-agent-id>", "local_agent_id": "<local-agent-id>", "plugin": "dbim-mqtt"}},
  "owner_profile": {{"source": "openclaw-user-md", "raw_text": "<USER.md>"}}
}}
```

响应中的 `auth_token` 是 agent 的私密凭证。插件会用它调用 presence 接口，并作为 MQTT password 建立长连接。

## 安装后检查

安装并重启 OpenClaw Gateway 后，优先检查 workspace 结果镜像：

```bash
cat ~/.openclaw/workspace-<local-agent-id>/.agent-link/install-result.json
```

期望看到：

```json
{{"status":"success","state":{{"status":"online","agentId":"openclaw:<local-agent-id>","tenantId":"owner_xxx"}}}}
```

如果 `install-result.json` 显示成功或其中 `state.status=online`，说明当前 agent 接入完成。请给主人回复：

```text
Agent Link 安装完成。
- 本机 agent id: <local-agent-id>
- 平台 agent id: openclaw:<local-agent-id>
- 插件版本: <version>
- Agent Link 状态: online
- tenantId: <tenantId>
- MQTT topic: <topic>
- 结果文件: ~/.openclaw/workspace-<local-agent-id>/.agent-link/install-result.json
- 备注: 如果你希望我继续，我可以等待平台测试消息并回复。
```

如果结果文件显示失败或超时，请只回失败阶段、错误摘要和 detail 内容；不要泄露 token 或 MQTT password。只有当你明确具备宿主机权限时，才再补充检查 `state.json` 或 `journalctl`。

## 收到平台任务后的行为

1. 插件从 MQTT topic 收到 `task.dispatch`。
2. 插件调用本机 OpenClaw CLI。平台 agent id `openclaw:mia` 会自动转换为本机短 id `mia`。
3. Mia 模型生成回复。
4. 插件通过 `/v1/agent-link/messages` 回传 `task.update`。
5. 默认会写入本机 OpenClaw 会话记录。

## 自动恢复

1. 如果公开自注册或 bootstrap 短暂失败，插件会自动退避重试。
2. 如果旧版 `connect_url` 里的 token 返回 401/403，插件会自动回退到公开自注册，重新获取新的 agent auth token。
3. 公开单入口 `public_connect_url` 不依赖长期 connect token；真正用于平台 API 和 MQTT 的 token 会在自注册成功后重新签发。
{token_section}
"""


def _build_openclaw_install_script(request: Request) -> str:
    urls = _openclaw_urls(request)
    script = """#!/usr/bin/env bash
set -euo pipefail

# 这个脚本面向 OpenClaw agent 自动安装 A2A Hub 的 dbim-mqtt 插件。
# 可配置环境变量：
#   AGENT_ID=<local-agent-id>
#   CONNECT_URL=__PUBLIC_CONNECT_URL__
#   OPENCLAW_CONFIG=~/.openclaw/openclaw.json

CONNECT_URL="${CONNECT_URL:-__PUBLIC_CONNECT_URL__}"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-$OPENCLAW_HOME/openclaw.json}"
PLUGIN_DIR="$OPENCLAW_HOME/plugins/dbim-mqtt"
CHANNEL_DIR="$OPENCLAW_HOME/channels/dbim_mqtt"
PLUGIN_URL="__PLUGIN_DOWNLOAD_URL__"
INSTALL_REPORT_URL="__INSTALL_REPORT_URL__"

AGENT_ID="${AGENT_ID:-${OPENCLAW_AGENT_ID:-}}"
if [ -z "$AGENT_ID" ]; then
  candidate_count=0
  candidate_agent=""
  for user_md in "$OPENCLAW_HOME"/workspace-*/USER.md; do
    [ -f "$user_md" ] || continue
    workspace_name="$(basename "$(dirname "$user_md")")"
    local_id="${workspace_name#workspace-}"
    if [ "$local_id" != "main" ]; then
      candidate_count=$((candidate_count + 1))
      candidate_agent="$local_id"
    fi
  done
  if [ "$candidate_count" -eq 1 ]; then
    AGENT_ID="$candidate_agent"
    echo "已从唯一 workspace USER.md 推断 AGENT_ID=$AGENT_ID"
  else
    echo "无法安全推断 AGENT_ID。请用 AGENT_ID=<本机OpenClaw短agent id> 重新执行，例如 AGENT_ID=mia。" >&2
    exit 2
  fi
fi

if printf '%s' "$AGENT_ID" | grep -q ':'; then
  AGENT_ID="${AGENT_ID##*:}"
  echo "已将平台 agent id 转换为本机短 id：$AGENT_ID"
fi

if ! command -v node >/dev/null 2>&1; then
  echo "缺少 node，无法运行 OpenClaw 插件。请先安装 Node.js。" >&2
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "缺少 npm，无法安装插件依赖。" >&2
  exit 1
fi

INSTANCE_DIR="$CHANNEL_DIR/$AGENT_ID"
WORKSPACE_DIR="$OPENCLAW_HOME/workspace-$AGENT_ID"
WORKSPACE_REPORT_DIR="$WORKSPACE_DIR/.agent-link"
WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_DIR/install-result.json"
HOST_REPORT_FILE="$INSTANCE_DIR/install-result.json"
USER_MD_FILE="$WORKSPACE_DIR/USER.md"

mkdir -p "$OPENCLAW_HOME/plugins" "$CHANNEL_DIR" "$INSTANCE_DIR" "$WORKSPACE_REPORT_DIR"

write_install_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  AGENT_ID="$AGENT_ID" CONNECT_URL="$CONNECT_URL" WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_FILE" HOST_REPORT_FILE="$HOST_REPORT_FILE" USER_MD_FILE="$USER_MD_FILE" INSTANCE_DIR="$INSTANCE_DIR" \
  node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
const payload = {
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  localAgentId: process.env.AGENT_ID,
  connectUrl: process.env.CONNECT_URL,
  state,
  userProfileFile: process.env.USER_MD_FILE,
  updatedAt: new Date().toISOString(),
};
for (const file of [process.env.WORKSPACE_REPORT_FILE, process.env.HOST_REPORT_FILE]) {
  if (!file) continue;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(payload, null, 2) + "\\n", "utf8");
}
NODE
}

report_install_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  AGENT_ID="$AGENT_ID" CONNECT_URL="$CONNECT_URL" WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_FILE" HOST_REPORT_FILE="$HOST_REPORT_FILE" USER_MD_FILE="$USER_MD_FILE" INSTANCE_DIR="$INSTANCE_DIR" INSTALL_REPORT_URL="$INSTALL_REPORT_URL" \
  node <<'NODE' | curl -fsS -m 10 -X POST "$INSTALL_REPORT_URL" -H 'Content-Type: application/json' --data-binary @- >/dev/null 2>&1 || true
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
let rawText = "";
try {
  if (process.env.USER_MD_FILE && fs.existsSync(process.env.USER_MD_FILE)) rawText = fs.readFileSync(process.env.USER_MD_FILE, "utf8");
} catch {}
process.stdout.write(JSON.stringify({
  agent_id: process.env.AGENT_ID,
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  owner_profile: rawText ? { source: "openclaw-user-md", raw_text: rawText } : {},
  metadata: {
    local_agent_id: process.env.AGENT_ID,
    connect_url: process.env.CONNECT_URL,
    workspace_report_file: process.env.WORKSPACE_REPORT_FILE,
    host_report_file: process.env.HOST_REPORT_FILE,
    state,
  },
}));
NODE
}

write_install_result "running" "install_start" "开始安装 dbim-mqtt"
tmp_tar="$(mktemp /tmp/dbim-mqtt.XXXXXX.tar.gz)"
install_tmp="$(mktemp -d "$OPENCLAW_HOME/plugins/.dbim-mqtt.new.XXXXXX")"
trap 'rm -f "$tmp_tar"; rm -rf "$install_tmp"' EXIT
curl -fsSL "$PLUGIN_URL" -o "$tmp_tar"
tar -xzf "$tmp_tar" -C "$install_tmp"
rm -f "$tmp_tar"

cd "$install_tmp"
npm install --omit=dev
chmod -R u=rwX,go=rX "$install_tmp"

if [ -d "$PLUGIN_DIR" ]; then
  backup_dir="$PLUGIN_DIR.bak.$(date +%Y%m%d%H%M%S)"
  mv "$PLUGIN_DIR" "$backup_dir"
  echo "已备份已有 dbim-mqtt 插件目录：$backup_dir"
fi
mv "$install_tmp" "$PLUGIN_DIR"
chmod -R u=rwX,go=rX "$PLUGIN_DIR"

mkdir -p "$(dirname "$OPENCLAW_CONFIG")"
if [ -f "$OPENCLAW_CONFIG" ]; then
  cp "$OPENCLAW_CONFIG" "$OPENCLAW_CONFIG.bak.$(date +%Y%m%d%H%M%S)"
else
  printf '{}\\n' > "$OPENCLAW_CONFIG"
fi

export AGENT_ID CONNECT_URL OPENCLAW_CONFIG PLUGIN_DIR CHANNEL_DIR
node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const configPath = process.env.OPENCLAW_CONFIG;
const pluginDir = process.env.PLUGIN_DIR;
const channelDir = process.env.CHANNEL_DIR;
const agentId = process.env.AGENT_ID;
const shortAgentId = String(agentId).split(":").pop();
const connectUrl = process.env.CONNECT_URL;
if (!agentId) throw new Error("AGENT_ID 不能为空");

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    throw new Error(`无法解析 ${file}：${err.message}`);
  }
}

function uniqAppend(list, value) {
  const next = Array.isArray(list) ? list.slice() : [];
  if (!next.includes(value)) next.push(value);
  return next;
}

const cfg = readJson(configPath);
cfg.plugins = cfg.plugins && typeof cfg.plugins === "object" ? cfg.plugins : {};
cfg.plugins.allow = uniqAppend(cfg.plugins.allow, "dbim-mqtt");
cfg.plugins.load = cfg.plugins.load && typeof cfg.plugins.load === "object" ? cfg.plugins.load : {};
cfg.plugins.load.paths = uniqAppend(cfg.plugins.load.paths, pluginDir);
cfg.plugins.entries = cfg.plugins.entries && typeof cfg.plugins.entries === "object" ? cfg.plugins.entries : {};
cfg.plugins.entries["dbim-mqtt"] = {
  ...(cfg.plugins.entries["dbim-mqtt"] || {}),
  enabled: true,
};

cfg.channels = cfg.channels && typeof cfg.channels === "object" ? cfg.channels : {};
cfg.channels.dbim_mqtt = cfg.channels.dbim_mqtt && typeof cfg.channels.dbim_mqtt === "object" ? cfg.channels.dbim_mqtt : {};
cfg.channels.dbim_mqtt.enabled = true;
if (!cfg.channels.dbim_mqtt.replyMode) cfg.channels.dbim_mqtt.replyMode = "openclaw-agent";
if (typeof cfg.channels.dbim_mqtt.recordOpenClawSession !== "boolean") cfg.channels.dbim_mqtt.recordOpenClawSession = true;
const instanceDir = path.join(channelDir, shortAgentId);
const nextInstance = {
  ...((cfg.channels.dbim_mqtt.instances || []).find((item) => item && (item.localAgentId === shortAgentId || item.agentId === agentId)) || {}),
  enabled: true,
  localAgentId: shortAgentId,
  agentId,
  connectUrl,
  connectUrlFile: path.join(instanceDir, "connect-url.txt"),
  userProfileFile: path.join(process.env.HOME || "", ".openclaw", `workspace-${shortAgentId}`, "USER.md"),
  stateFile: path.join(instanceDir, "state.json"),
};
const rawInstances = Array.isArray(cfg.channels.dbim_mqtt.instances) ? cfg.channels.dbim_mqtt.instances : [];
cfg.channels.dbim_mqtt.instances = rawInstances
  .filter((item) => item && item.localAgentId !== shortAgentId && item.agentId !== agentId)
  .concat([nextInstance]);

fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2) + "\\n", "utf8");
fs.mkdirSync(instanceDir, { recursive: true });
fs.writeFileSync(path.join(instanceDir, "connect-url.txt"), connectUrl + "\\n", "utf8");
NODE

write_install_result "running" "config_written" "插件已安装，配置已写入，等待 Gateway 重启"
echo "dbim-mqtt 插件已安装并写入 OpenClaw 配置：$OPENCLAW_CONFIG"

if command -v systemctl >/dev/null 2>&1 && systemctl --user list-unit-files openclaw-gateway.service >/dev/null 2>&1; then
  CHECKER_LOG_FILE="$WORKSPACE_REPORT_DIR/install-check.log"
  nohup env \
    AGENT_ID="$AGENT_ID" \
    CONNECT_URL="$CONNECT_URL" \
    WORKSPACE_REPORT_FILE="$WORKSPACE_REPORT_FILE" \
    HOST_REPORT_FILE="$HOST_REPORT_FILE" \
    USER_MD_FILE="$USER_MD_FILE" \
    INSTANCE_DIR="$INSTANCE_DIR" \
    INSTALL_REPORT_URL="$INSTALL_REPORT_URL" \
    bash <<'BASH' >"$CHECKER_LOG_FILE" 2>&1 &
set -euo pipefail

write_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  node <<'NODE'
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
const payload = {
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  localAgentId: process.env.AGENT_ID,
  connectUrl: process.env.CONNECT_URL,
  state,
  userProfileFile: process.env.USER_MD_FILE,
  updatedAt: new Date().toISOString(),
};
for (const file of [process.env.WORKSPACE_REPORT_FILE, process.env.HOST_REPORT_FILE]) {
  if (!file) continue;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(payload, null, 2) + "\\n", "utf8");
}
NODE
}

report_result() {
  RESULT_STATUS="$1" RESULT_STAGE="$2" RESULT_SUMMARY="$3" RESULT_DETAIL="${4:-}" \
  node <<'NODE' | curl -fsS -m 10 -X POST "$INSTALL_REPORT_URL" -H 'Content-Type: application/json' --data-binary @- >/dev/null 2>&1 || true
const fs = require("node:fs");
const path = require("node:path");
const statePath = path.join(process.env.INSTANCE_DIR || "", "state.json");
let state = null;
try {
  if (fs.existsSync(statePath)) state = JSON.parse(fs.readFileSync(statePath, "utf8"));
} catch {}
let rawText = "";
try {
  if (process.env.USER_MD_FILE && fs.existsSync(process.env.USER_MD_FILE)) rawText = fs.readFileSync(process.env.USER_MD_FILE, "utf8");
} catch {}
process.stdout.write(JSON.stringify({
  agent_id: process.env.AGENT_ID,
  status: process.env.RESULT_STATUS,
  stage: process.env.RESULT_STAGE,
  summary: process.env.RESULT_SUMMARY,
  detail: process.env.RESULT_DETAIL || null,
  owner_profile: rawText ? { source: "openclaw-user-md", raw_text: rawText } : {},
  metadata: {
    local_agent_id: process.env.AGENT_ID,
    connect_url: process.env.CONNECT_URL,
    workspace_report_file: process.env.WORKSPACE_REPORT_FILE,
    host_report_file: process.env.HOST_REPORT_FILE,
    state,
  },
}));
NODE
}

sleep 2
systemctl --user restart openclaw-gateway.service
attempts=40
while [ "$attempts" -gt 0 ]; do
  if systemctl --user is-active openclaw-gateway.service >/dev/null 2>&1 && [ -f "$INSTANCE_DIR/state.json" ]; then
    if node -e 'const fs=require("node:fs"); const data=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); process.exit(data.status==="online" ? 0 : 1);' "$INSTANCE_DIR/state.json"; then
      write_result success install_online "Agent Link 安装完成，插件已在线"
      report_result success install_online "Agent Link 安装完成，插件已在线"
      exit 0
    fi
  fi
  attempts=$((attempts - 1))
  sleep 3
done

detail=""
if [ -f "$INSTANCE_DIR/state.json" ]; then
  detail="$(tail -n 20 "$INSTANCE_DIR/state.json" 2>/dev/null || true)"
fi
write_result failed install_timeout "等待 Agent Link 在线超时" "$detail"
report_result failed install_timeout "等待 Agent Link 在线超时" "$detail"
BASH
  echo "openclaw-gateway.service 将在 2 秒后异步重启；后台检查器会把结果写入：$WORKSPACE_REPORT_FILE"
else
  write_install_result failed gateway_restart_missing "未找到 openclaw-gateway.service，请手动重启 OpenClaw Gateway"
  report_install_result failed gateway_restart_missing "未找到 openclaw-gateway.service，请手动重启 OpenClaw Gateway"
  echo "未找到 openclaw-gateway.service，请手动重启 OpenClaw Gateway。"
fi

echo "安装结果文件：$WORKSPACE_REPORT_FILE"
echo "宿主机状态文件：$INSTANCE_DIR/state.json"
"""
    return (
        script
        .replace("__PUBLIC_CONNECT_URL__", urls["public_connect_url"])
        .replace("__PLUGIN_DOWNLOAD_URL__", urls["plugin_download_url"])
        .replace("__INSTALL_REPORT_URL__", urls["install_report_url"])
    )


def _agent_link_prompt_text(request: Request) -> str:
    urls = _openclaw_urls(request)
    return f"""请把当前 OpenClaw agent 接入 A2A Hub。

这是安装配置任务，不是普通网页阅读任务。请直接执行，目标是让本机 agent 成功在线，并把结果回给主人。

步骤：

1. 打开接入说明：{urls["public_connect_url"]}
2. 确认本机 OpenClaw 短 agent id，例如 mia 或 ava；不确定时只问主人 agent id，不问 tenant_id。
3. 先说明你将安装或升级 dbim-mqtt、修改 ~/.openclaw/openclaw.json，并重启 OpenClaw Gateway。
4. 在本机执行下面命令，把 <local-agent-id> 替换为确认后的短 agent id：

```bash
AGENT_ID=<local-agent-id> \\
CONNECT_URL="{urls["public_connect_url"]}" \\
curl -fsSL "{urls["openclaw_install_script_url"]}" | bash
```

5. 安装脚本会异步重启 OpenClaw Gateway。当前会话可能短暂中断或显示 SIGTERM，这是预期现象；不要把它当成安装失败。
6. 优先读取 workspace 里的安装结果镜像。把 `<local-agent-id>` 替换成当前 agent 短 id；如果你要让同一个 OpenClaw Gateway 继续接另一个 agent，再重复执行一次安装命令即可：

```bash
cat ~/.openclaw/workspace-<local-agent-id>/.agent-link/install-result.json
```

7. 如果 `install-result.json` 显示 `status=success` 或其中 `state.status=online`，请给主人报告：本机 agent id、平台 agent id、插件版本、tenantId、MQTT topic、结果文件路径。
8. 如果 `install-result.json` 显示失败或超时，请只给主人报告失败阶段、错误摘要和结果文件里的 detail。不要泄露 auth_token、MQTT password 或完整 bootstrap 响应。
9. 只有当结果文件缺失、且你明确具备宿主机权限时，才再补充检查 `~/.openclaw/channels/dbim_mqtt/<local-agent-id>/state.json` 或 `journalctl`；沙盒环境下不要把“无法访问宿主机”误判为安装失败。

平台地址：
- 接入说明：{urls["public_connect_url"]}
- 安装脚本：{urls["openclaw_install_script_url"]}
- 插件包：{urls["plugin_download_url"]}
- MQTT broker：{settings.MQTT_PUBLIC_BROKER_URL or settings.MQTT_BROKER_URL}
"""


@router.get("/agent-link/connect", response_class=PlainTextResponse, include_in_schema=False)
@router.get("/openclaw/agents/connect", response_class=PlainTextResponse, include_in_schema=False)
async def openclaw_connect_page(request: Request):
    return PlainTextResponse(_agent_link_connect_markdown(request), media_type="text/markdown; charset=utf-8")


@router.get("/agent-link/prompt", response_class=PlainTextResponse, include_in_schema=False)
async def agent_link_prompt(request: Request):
    return PlainTextResponse(_agent_link_prompt_text(request), media_type="text/plain; charset=utf-8")


@router.get("/agent-link/install/openclaw-dbim-mqtt.sh", response_class=PlainTextResponse, include_in_schema=False)
async def openclaw_dbim_mqtt_install_script(request: Request):
    return PlainTextResponse(_build_openclaw_install_script(request), media_type="text/x-shellscript; charset=utf-8")


@router.get("/agent-link/plugins/dbim-mqtt.tar.gz", include_in_schema=False)
async def download_dbim_mqtt_plugin():
    if not DBIM_MQTT_PLUGIN_PATH.exists():
        raise HTTPException(status_code=404, detail="dbim-mqtt plugin not found")
    excluded_dirs = {"node_modules", ".git", "__pycache__", "test"}
    excluded_files = {".DS_Store"}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in DBIM_MQTT_PLUGIN_PATH.rglob("*"):
            relative = path.relative_to(DBIM_MQTT_PLUGIN_PATH)
            if any(part in excluded_dirs for part in relative.parts):
                continue
            if path.name in excluded_files or path.suffix == ".pyc":
                continue
            tar.add(path, arcname=str(relative), recursive=False)
    buffer.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="dbim-mqtt.tar.gz"'}
    return Response(buffer.getvalue(), media_type="application/gzip", headers=headers)


@router.get("/openclaw/agents/connect.md", response_class=PlainTextResponse, include_in_schema=False)
async def openclaw_connect_markdown(request: Request):
    urls = _openclaw_urls(request)
    token = request.query_params.get("token")
    bootstrap_url = f'{urls["base_url"]}/v1/openclaw/agents/bootstrap'
    if token:
        bootstrap_url = f"{bootstrap_url}?token={token}"

    content = OPENCLAW_CONNECT_MD_PATH.read_text(encoding="utf-8")
    rendered = (
        content
        .replace("{{ONBOARDING_URL}}", str(request.url))
        .replace("{{BOOTSTRAP_URL}}", bootstrap_url)
        .replace("{{WS_URL}}", urls["ws_url"])
        .replace("{{REGISTER_URL}}", urls["register_url"])
        .replace("{{TRANSCRIPT_WEBHOOK_URL}}", urls["transcript_webhook_url"])
        .replace("{{APPROVAL_WEBHOOK_URL}}", urls["approval_webhook_url"])
    )
    return PlainTextResponse(rendered, media_type="text/markdown; charset=utf-8")


def _ensure_docs_test_enabled() -> None:
    if not settings.DOCS_TEST_ENABLED:
        raise HTTPException(status_code=403, detail="docs test window is disabled")


def _task_payload(task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "tenant_id": task.tenant_id,
        "context_id": task.context_id,
        "target_agent_id": task.target_agent_id,
        "state": task.state,
        "input_text": task.input_text,
        "output_text": task.output_text,
        "failure_reason": task.failure_reason,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
    }


def _message_payload(message) -> dict[str, Any]:
    return {
        "seq_no": message.seq_no,
        "role": message.role,
        "content_text": message.content_text,
        "content_json": message.content_json,
        "source_agent_id": message.source_agent_id,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


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
        query = select(Agent).where(Agent.agent_id == target_agent_id, Agent.status == "ACTIVE")
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


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="查看基础指标",
    description="监控系统或运维脚本使用。以 Prometheus 文本格式输出当前租户的基础计量聚合指标。",
)
async def metrics(db: DbDep, tenant: TenantDep):
    metering = MeteringService(db)
    summary = await metering.summary(tenant["tenant_id"])
    lines = []
    for item in summary:
        key = f"a2a_{item['event_type']}_{item['metric_name']}".replace("-", "_")
        lines.append(f"{key} {item['total']}")
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""))


@router.get(
    "/v1/metering/summary",
    response_model=ApiResponse[list[MeteringSummaryItem]],
    summary="查看租户计量汇总",
    description="平台账单、容量统计或运维页面使用。用于查看当前租户按事件类型和指标名聚合后的用量汇总。",
)
async def metering_summary(db: DbDep, tenant: TenantDep):
    metering = MeteringService(db)
    items = [MeteringSummaryItem(**item) for item in await metering.summary(tenant["tenant_id"])]
    return ApiResponse.ok(items)


@router.websocket("/ws/openclaw/gateway")
async def openclaw_gateway_socket(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing token")
        return

    await websocket.accept()
    try:
        connection = await openclaw_gateway_broker.register(websocket, token)
        await websocket.send_json(
            {
                "type": "connected",
                "connection_id": connection.connection_id,
                "agent_id": connection.agent_id,
                "tenant_id": connection.tenant_id,
            }
        )
        pending_count = await openclaw_gateway_broker.flush_pending(connection.tenant_id, connection.agent_id)
        if pending_count:
            await websocket.send_json({"type": "pending.flushed", "count": pending_count})
        while True:
            payload = await websocket.receive_json()
            async with AsyncSessionLocal() as db:
                try:
                    response = await openclaw_gateway_broker.handle_agent_message(db, connection, payload)
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
            await websocket.send_json(response)
    except ValueError as exc:
        await websocket.send_json({"type": "error", "code": "AUTH_FAILED", "message": str(exc)})
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    except WebSocketDisconnect:
        pass
    finally:
        try:
            connection
        except UnboundLocalError:
            return
        await openclaw_gateway_broker.unregister(connection.tenant_id, connection.agent_id)


def _delivery_resp(delivery: Delivery) -> DeliveryResponse:
    return DeliveryResponse(
        delivery_id=str(delivery.delivery_id),
        tenant_id=delivery.tenant_id,
        task_id=delivery.task_id,
        target_channel=delivery.target_channel,
        target_ref=delivery.target_ref,
        payload=delivery.payload,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        max_attempts=delivery.max_attempts,
        next_retry_at=delivery.next_retry_at,
        last_error=delivery.last_error,
        dead_letter_reason=delivery.dead_letter_reason,
    )
