"""
FastAPI 公共依赖
"""
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_tenant

# DB session 依赖
DbDep = Annotated[AsyncSession, Depends(get_db)]

# 当前租户依赖
TenantDep = Annotated[dict[str, Any], Depends(get_current_tenant)]


def get_idempotency_key(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    """从 Header 提取幂等键"""
    return idempotency_key


IdempotencyDep = Annotated[str | None, Depends(get_idempotency_key)]
