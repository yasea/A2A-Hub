from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ServicePublicationCreateRequest(BaseModel):
    service_id: str | None = None
    handler_agent_id: str
    title: str
    summary: str | None = None
    visibility: str = "listed"
    contact_policy: str = "auto_accept"
    allow_agent_initiated_chat: bool = True
    tags: list[str] = Field(default_factory=list)
    capabilities_public: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServicePublicationUpdateRequest(BaseModel):
    handler_agent_id: str | None = None
    title: str | None = None
    summary: str | None = None
    visibility: str | None = None
    contact_policy: str | None = None
    allow_agent_initiated_chat: bool | None = None
    status: str | None = None
    tags: list[str] | None = None
    capabilities_public: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class ServicePublicationResponse(BaseModel):
    service_id: str
    tenant_id: str
    handler_agent_id: str
    title: str
    summary: str | None = None
    visibility: str
    contact_policy: str
    allow_agent_initiated_chat: bool
    status: str
    tags: list[str]
    capabilities_public: dict[str, Any]
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ServiceThreadCreateRequest(BaseModel):
    title: str | None = None
    initiator_agent_id: str | None = None
    opening_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceThreadMessageCreateRequest(BaseModel):
    text: str
    initiator_agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceThreadResponse(BaseModel):
    thread_id: str
    service_id: str
    consumer_tenant_id: str
    provider_tenant_id: str
    provider_context_id: str
    initiator_agent_id: str | None = None
    handler_agent_id: str
    status: str
    title: str | None = None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime

    class Config:
        from_attributes = True


class ServiceThreadMessageResponse(BaseModel):
    message_id: str
    thread_id: str
    role: str
    sender_tenant_id: str | None = None
    sender_agent_id: str | None = None
    linked_task_id: str | None = None
    content_text: str
    seq_no: int
    metadata_json: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class ServiceThreadCreateResponse(BaseModel):
    thread: ServiceThreadResponse
    task_id: str | None = None


class ServiceThreadMessageCreateResponse(BaseModel):
    thread_id: str
    task_id: str | None = None
    message_id: str

