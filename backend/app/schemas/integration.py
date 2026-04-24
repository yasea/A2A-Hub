"""
OpenClaw、Rocket.Chat、审批、投递等扩展能力的 Schema。
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.message import MessagePart


class OpenClawTranscriptEvent(BaseModel):
    tenant_id: str
    session_key: str
    event_id: str
    text: str
    sender_type: str = "agent"
    sender_id: str | None = None
    task_type: str = "generic"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenClawApprovalEvent(BaseModel):
    tenant_id: str
    task_id: str
    external_key: str
    reason: str
    requested_by: str | None = None
    approver_user_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RocketChatWebhookPayload(BaseModel):
    tenant_id: str
    room_id: str
    text: str
    sender_id: str
    sender_name: str | None = None
    server_url: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalCreateRequest(BaseModel):
    task_id: str
    approver_user_id: str | None = None
    reason: str
    external_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalResolveRequest(BaseModel):
    decision: str
    note: str | None = None


class ApprovalResponse(BaseModel):
    approval_id: str
    tenant_id: str
    task_id: str
    context_id: str
    status: str
    approver_user_id: str | None = None
    requested_by: str | None = None
    reason: str | None = None
    decision_note: str | None = None
    external_key: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None

    class Config:
        from_attributes = True


class DeliveryCreateRequest(BaseModel):
    target_channel: str
    target_ref: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    max_attempts: int | None = None


class DeliveryResponse(BaseModel):
    delivery_id: str
    tenant_id: str
    task_id: str | None = None
    target_channel: str
    target_ref: dict[str, Any]
    payload: dict[str, Any]
    status: str
    attempt_count: int
    max_attempts: int
    next_retry_at: datetime | None = None
    last_error: str | None = None
    dead_letter_reason: str | None = None


class MeteringSummaryItem(BaseModel):
    event_type: str
    metric_name: str
    total: float


class OpenClawAgentRegisterRequest(BaseModel):
    agent_id: str
    display_name: str
    agent_summary: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    config_json: dict[str, Any] = Field(default_factory=dict)


class OpenClawAgentRegistrationResponse(BaseModel):
    agent_id: str
    tenant_id: str
    agent_summary: str | None = None
    auth_token: str
    ws_url: str
    onboarding_url: str
    transcript_webhook_url: str
    approval_webhook_url: str
    message_types: list[str]
    invite_url: str | None = None
    transport: str = "mqtt"
    mqtt_broker_url: str | None = None
    mqtt_client_id: str | None = None
    mqtt_command_topic: str | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    presence_url: str | None = None
    qos: int = 1


class OpenClawDispatchRequest(BaseModel):
    task_id: str


class OpenClawDispatchResponse(BaseModel):
    task_id: str
    agent_id: str
    dispatched: bool
    connection_id: str | None = None
    reason: str | None = None


class OpenClawOnboardingInfo(BaseModel):
    ws_url: str
    onboarding_url: str
    register_url: str
    transcript_webhook_url: str
    approval_webhook_url: str
    message_types: list[str]
    auth_scheme: str
    transport: str = "mqtt"
    mqtt_broker_url: str | None = None
    mqtt_topic_pattern: str | None = None
    presence_url: str | None = None
    public_connect_url: str | None = None
    self_register_url: str | None = None


class OpenClawConnectLinkResponse(BaseModel):
    agent_id: str
    tenant_id: str
    connect_url: str
    bootstrap_url: str
    expires_in_seconds: int


class OpenClawConnectLinkRequest(BaseModel):
    display_name: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    config_json: dict[str, Any] = Field(default_factory=dict)


class AgentLinkSelfRegisterRequest(BaseModel):
    """公开 Agent Link 自注册请求。"""

    agent_id: str
    display_name: str | None = None
    agent_summary: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    config_json: dict[str, Any] = Field(default_factory=dict)
    owner_profile: dict[str, Any] = Field(default_factory=dict)


class AgentLinkManifestResponse(BaseModel):
    """公开接入入口返回的 manifest。"""

    public_connect_url: str
    self_register_url: str
    onboarding_url: str
    plugin_download_url: str
    openclaw_install_script_url: str
    agent_prompt_url: str
    friend_tools_url: str | None = None
    transport: str = "mqtt"
    mqtt_public_broker_url: str | None = None
    required_plugin: str = "dbim-mqtt"
    owner_profile_source: str = "OpenClaw USER.md"
    notes: list[str] = Field(default_factory=list)


class AgentLinkPresenceRequest(BaseModel):
    status: str = "online"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentLinkErrorReportRequest(BaseModel):
    stage: str
    summary: str
    category: str = "runtime"
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentLinkInstallReportRequest(BaseModel):
    agent_id: str
    status: str
    stage: str
    summary: str
    detail: str | None = None
    owner_profile: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentLinkErrorEventResponse(BaseModel):
    error_id: int
    tenant_id: str | None = None
    agent_id: str | None = None
    source_side: str
    stage: str
    category: str
    summary: str
    detail: str | None = None
    status_code: int | None = None
    request_path: str | None = None
    payload_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True


class AgentLinkMessageRequest(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentLinkSendMessageRequest(BaseModel):
    context_id: str | None = None
    target_agent_id: str
    parts: list[MessagePart] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class DocsAgentMessageTestRequest(BaseModel):
    """Swagger 文档页内置联调窗口发送消息请求。"""

    target_agent_id: str
    tenant_id: str | None = None
    message: str = "请只回复：DOCS_AGENT_TEST_OK"
