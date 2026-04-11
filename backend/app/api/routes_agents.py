"""
Agent 注册、查询、状态管理
GET    /v1/agents                    — 列出所有 ACTIVE Agent
POST   /v1/agents                    — 注册 Agent
GET    /v1/agents/{agent_id}         — 查询单个 Agent
PATCH  /v1/agents/{agent_id}/status  — 更新状态
GET    /v1/agents/{agent_id}/health  — 健康检查
"""
from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, TenantDep
from app.schemas.agent import AgentCreate, AgentResponse, AgentStatusUpdate
from app.schemas.common import ApiResponse
from app.services.agent_registry import AgentNotFoundError, AgentRegistry

router = APIRouter(prefix="/v1/agents", tags=["agents"])


@router.post(
    "",
    response_model=ApiResponse[AgentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="注册 Agent",
    description="平台管理员、接入脚本或集成服务使用。用于在当前租户下声明一个可被路由和调用的 Agent；重复 agent_id 会更新配置。",
)
async def register_agent(req: AgentCreate, db: DbDep, tenant: TenantDep):
    """注册新 Agent 或更新已有 Agent 的配置。`agent_id` 已存在时执行更新。"""
    svc = AgentRegistry(db)
    agent = await svc.register(
        agent_id=req.agent_id,
        tenant_id=tenant["tenant_id"],
        agent_type=req.agent_type,
        display_name=req.display_name,
        capabilities=req.capabilities,
        auth_scheme=req.auth_scheme,
        config_json=req.config_json,
        actor_id=tenant.get("sub"),
    )
    return ApiResponse.ok(AgentResponse.model_validate(agent))


@router.get(
    "",
    response_model=ApiResponse[list[AgentResponse]],
    summary="列出 Agent",
    description="平台 UI、调试脚本或路由配置页使用。用于查看当前租户下可用的 ACTIVE Agent 列表。",
)
async def list_agents(db: DbDep, tenant: TenantDep):
    """列出当前租户下所有 `ACTIVE` 状态的 Agent。"""
    svc = AgentRegistry(db)
    agents = await svc.list_active(tenant["tenant_id"])
    return ApiResponse.ok([AgentResponse.model_validate(a) for a in agents])


@router.get(
    "/{agent_id}",
    response_model=ApiResponse[AgentResponse],
    summary="查询 Agent",
    description="平台 UI、运维脚本或调试工具使用。用于查看指定 Agent 的类型、能力、鉴权方式和配置。",
)
async def get_agent(agent_id: str, db: DbDep, tenant: TenantDep):
    """查询单个 Agent 详情。"""
    svc = AgentRegistry(db)
    agent = await svc.get(agent_id, tenant["tenant_id"])
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent 不存在")
    return ApiResponse.ok(AgentResponse.model_validate(agent))


@router.patch(
    "/{agent_id}/status",
    response_model=ApiResponse[dict],
    summary="更新 Agent 状态",
    description="平台管理员或自动化治理流程使用。用于启用、停用或挂起 Agent，影响后续路由和健康判断。",
)
async def update_agent_status(agent_id: str, req: AgentStatusUpdate, db: DbDep, tenant: TenantDep):
    """更新 Agent 状态，可选值：`ACTIVE` / `INACTIVE` / `SUSPENDED`。"""
    valid = {"ACTIVE", "INACTIVE", "SUSPENDED"}
    if req.status not in valid:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"status 必须是 {valid}")
    svc = AgentRegistry(db)
    try:
        await svc.set_status(agent_id, tenant["tenant_id"], req.status, actor_id=tenant.get("sub"))
    except AgentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ApiResponse.ok({"agent_id": agent_id, "status": req.status})


@router.get(
    "/{agent_id}/health",
    response_model=ApiResponse[dict],
    summary="Agent 健康检查",
    description="平台 UI、监控或联调脚本使用。当前检查 Agent 是否存在且为 ACTIVE，后续可扩展为真实探活。",
)
async def agent_health(agent_id: str, db: DbDep, tenant: TenantDep):
    """检查 Agent 是否存在且处于 `ACTIVE` 状态。"""
    svc = AgentRegistry(db)
    result = await svc.healthcheck(agent_id, tenant["tenant_id"])
    return ApiResponse.ok(result)
