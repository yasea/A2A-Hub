from typing import Any
from pydantic import BaseModel


class AgentCreate(BaseModel):
    agent_id: str
    agent_type: str                          # native / federated / bridged
    display_name: str
    capabilities: dict[str, Any] = {}
    auth_scheme: str | None = None
    config_json: dict[str, Any] = {}


class AgentResponse(BaseModel):
    agent_id: str
    public_number: int | None = None
    tenant_id: str
    agent_type: str
    display_name: str
    status: str
    capabilities: dict[str, Any]
    auth_scheme: str | None = None
    config_json: dict[str, Any] = {}

    class Config:
        from_attributes = True


class AgentStatusUpdate(BaseModel):
    status: str   # ACTIVE / INACTIVE / SUSPENDED


class RoutingRuleCreate(BaseModel):
    name: str
    priority: int = 100
    match_expr: dict[str, Any] = {}          # 匹配条件，如 {"task_type": "analysis"}
    target_agent_id: str
    is_active: bool = True


class RoutingRuleResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    priority: int
    match_expr: dict[str, Any]
    target_agent_id: str
    is_active: bool

    class Config:
        from_attributes = True
