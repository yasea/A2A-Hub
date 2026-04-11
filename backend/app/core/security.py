"""
JWT 鉴权工具
"""
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer()


def create_access_token(
    subject: str,
    extra: dict[str, Any] | None = None,
    expires_minutes: int | None = None,
) -> str:
    """生成 JWT access token"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "exp": now + timedelta(minutes=expires_minutes or settings.JWT_EXPIRE_MINUTES),
        "iat": now,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_service_account_token(
    service_account_id: str,
    tenant_id: str,
    component_type: str,
    scopes: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """生成平台内部服务账号 token。"""
    return create_access_token(
        subject=service_account_id,
        expires_minutes=settings.SERVICE_ACCOUNT_TOKEN_EXPIRE_MINUTES,
        extra={
            "tenant_id": tenant_id,
            "token_type": "service_account",
            "service_account_id": service_account_id,
            "component_type": component_type,
            "scopes": scopes or ["messages:send"],
            "metadata": metadata or {},
        },
    )


def decode_access_token(token: str) -> dict[str, Any]:
    """解码并校验 JWT token"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 无效")


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> dict[str, Any]:
    """FastAPI 依赖：从 JWT 中提取当前租户信息"""
    payload = decode_access_token(credentials.credentials)
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token 缺少 tenant_id")
    return {
        "tenant_id": tenant_id,
        "sub": payload.get("sub"),
        "token_type": payload.get("token_type", "user"),
        "service_account_id": payload.get("service_account_id"),
        "component_type": payload.get("component_type"),
        "scopes": payload.get("scopes", []),
        "agent_id": payload.get("agent_id"),
    }
