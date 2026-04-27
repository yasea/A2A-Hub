"""
共享工具函数和常量，供 routes_agent_link / routes_openclaw / routes_approvals / routes_deliveries / routes_docs_test / routes_events 使用。
"""
import hashlib
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from app.models.delivery import Delivery
from app.schemas.integration import DeliveryResponse
from app.services.agent_link_service import agent_link_service
from app.services.error_event_service import ErrorEventService
from app.services.mosquitto_auth_sync import build_default_mosquitto_auth_sync_service

OPENCLAW_CONNECT_MD_PATH = Path(__file__).resolve().parents[1] / "static" / "openclaw_agent_connect.md"
AIMOO_LINK_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "openclaw-aimoo-plugin"


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
        "plugin_download_url": f"{base_url}/agent-link/plugins/aimoo-link.tar.gz",
        "openclaw_install_script_url": f"{base_url}/agent-link/install/openclaw-aimoo-link.sh",
        "agent_prompt_url": f"{base_url}/agent-link/prompt",
        "friend_tools_url": f"{base_url}/agent-link/friend-tools",
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


def _sanitize_agent_identity_part(value: str | None, *, fallback: str = "agent") -> str:
    import re

    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-").lower()
    return (normalized or fallback)[:48]


def _runtime_local_agent_id(agent_id: str, config_json: dict[str, Any] | None = None) -> str:
    config = config_json or {}
    for key in ("local_agent_id", "workspace"):
        value = str(config.get(key) or "").strip()
        if value:
            return _sanitize_agent_identity_part(value.split(":")[-1])
    return _sanitize_agent_identity_part(str(agent_id or "agent").split(":")[-1])


def _namespaced_openclaw_agent_id(agent_id: str, tenant_id: str, config_json: dict[str, Any] | None = None) -> str:
    """Build the stable platform id used by public Agent Link self-registration.

    Local OpenClaw names like "main" are common. The platform id must include a
    runtime key so different users and different machines do not fight over the
    global agents.agent_id primary key or the MQTT client id.
    """

    normalized = _normalize_openclaw_agent_id(agent_id)
    if len(normalized.split(":")) >= 3:
        return normalized
    config = config_json or {}
    local_id = _runtime_local_agent_id(normalized, config)
    identity_key = (
        config.get("runtime_identity_key")
        or config.get("agent_identity_key")
        or config.get("runtimeIdentityKey")
    )
    safe_key = _sanitize_agent_identity_part(str(identity_key or ""), fallback="")
    if not safe_key:
        raise HTTPException(
            status_code=422,
            detail="runtime_identity_key is required. The client must generate a persistent UUID "
                   "and include it as config_json.runtime_identity_key in the self-register request.",
        )
    return f"openclaw:{safe_key}:{local_id}"


def _owner_profile_key(owner_profile: dict[str, Any]) -> str:
    for key in ("owner_id", "user_id", "email", "username", "name"):
        value = str(owner_profile.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    raw_text = str(owner_profile.get("raw_text") or owner_profile.get("user_md") or "").strip()
    if raw_text:
        return f"user_md:{raw_text[:4096]}"
    return "anonymous:default"


def _short_openclaw_agent_id(agent_id: str) -> str:
    value = str(agent_id or "").strip()
    if not value:
        return "agent"
    return value.split(":")[-1] if ":" in value else value


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


def _normalize_agent_summary(
    agent_summary: str | None,
    agent_id: str,
    owner_profile: dict[str, Any],
    config_json: dict[str, Any] | None = None,
) -> str:
    candidates = [
        agent_summary,
        (config_json or {}).get("agent_summary"),
        owner_profile.get("agent_summary"),
        owner_profile.get("agent_intro"),
        owner_profile.get("self_intro"),
        owner_profile.get("bio"),
        owner_profile.get("summary"),
        owner_profile.get("description"),
    ]
    for candidate in candidates:
        text = " ".join(str(candidate or "").strip().split())
        if text:
            return text[:160]
    return f"OpenClaw agent {_short_openclaw_agent_id(agent_id)}"


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


async def _sync_owner_tenant_mosquitto_auth(db) -> None:
    service = build_default_mosquitto_auth_sync_service()
    if not service.configured:
        return
    await service.sync_active_tenants(db)


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
    from app.models.tenant import Tenant

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


def _ensure_docs_test_enabled() -> None:
    if not settings.DOCS_TEST_ENABLED:
        raise HTTPException(status_code=403, detail="docs test window is disabled")
