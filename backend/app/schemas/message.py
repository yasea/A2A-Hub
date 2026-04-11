"""
Message 相关 Pydantic Schema
"""
from typing import Any
from pydantic import BaseModel, Field


class MessagePart(BaseModel):
    """消息片段"""
    type: str = "text/plain"  # MIME type
    text: str | None = None
    json_data: dict[str, Any] | None = None  # 避免与 BaseModel.json 冲突


class MessageSendRequest(BaseModel):
    """发送消息/创建任务请求"""
    context_id: str
    target_agent_id: str | None = None
    parts: list[MessagePart] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class MessageSendResponse(BaseModel):
    """发送消息响应"""
    task_id: str
    state: str
    context_id: str
