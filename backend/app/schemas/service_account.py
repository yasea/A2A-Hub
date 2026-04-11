"""
Service account 相关 Schema。
"""
from pydantic import BaseModel, Field


class ServiceAccountTokenRequest(BaseModel):
    """签发服务账号 token 请求。"""

    tenant_id: str
    service_account_id: str
    component_type: str = "platform_component"
    scopes: list[str] = Field(default_factory=lambda: ["messages:send"])
    metadata: dict = Field(default_factory=dict)


class ServiceAccountTokenResponse(BaseModel):
    """服务账号 token 响应。"""

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    tenant_id: str
    service_account_id: str
    component_type: str
    scopes: list[str]
