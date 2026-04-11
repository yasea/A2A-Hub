"""
Task 相关 Pydantic Schema
"""
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class TaskCreate(BaseModel):
    """创建任务请求"""
    context_id: str
    target_agent_id: str | None = None
    task_type: str = "generic"
    priority: str = "normal"
    input_text: str | None = None
    approval_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class TaskResponse(BaseModel):
    """任务响应"""
    task_id: str
    tenant_id: str
    context_id: str
    target_agent_id: str | None = None
    task_type: str
    state: str
    priority: str
    input_text: str | None = None
    output_text: str | None = None
    failure_reason: str | None = None
    approval_required: bool
    retry_count: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    class Config:
        from_attributes = True


class TaskMessageResponse(BaseModel):
    """任务消息响应。"""

    message_id: str
    task_id: str
    context_id: str
    role: str
    mime_type: str
    content_text: str | None = None
    content_json: dict[str, Any] | None = None
    source_agent_id: str | None = None
    source_message_id: str | None = None
    seq_no: int
    metadata_json: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class TaskStateUpdate(BaseModel):
    """任务状态更新请求"""
    new_state: str
    reason: str | None = None
    output_text: str | None = None
