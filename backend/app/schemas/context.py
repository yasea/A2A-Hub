"""
Context 相关 Pydantic Schema
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContextCreateRequest(BaseModel):
    """创建 context 请求。"""

    source_channel: str | None = "api"
    source_conversation_id: str | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextResponse(BaseModel):
    """context 响应。"""

    context_id: str
    tenant_id: str
    owner_user_id: str | None = None
    source_channel: str | None = None
    source_conversation_id: str | None = None
    status: str
    title: str | None = None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime

    class Config:
        from_attributes = True
