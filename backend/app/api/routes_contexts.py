"""
Context API
"""
from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.context import ContextCreateRequest, ContextResponse
from app.services.context_service import ContextService

router = APIRouter(prefix="/v1/contexts", tags=["contexts"])


@router.post(
    "",
    response_model=ApiResponse[ContextResponse],
    summary="创建 context",
    description="前端、平台组件或外部系统在开始一段对话前调用。context 用于把多条消息、任务和来源会话绑定在一起。",
)
async def create_context(
    req: ContextCreateRequest,
    db: DbDep,
    tenant: TenantDep,
) -> ApiResponse[ContextResponse]:
    """创建一个当前租户下的会话容器，供消息/任务复用。"""
    svc = ContextService(db)
    context = await svc.create(
        tenant_id=tenant["tenant_id"],
        source_channel=req.source_channel,
        source_conversation_id=req.source_conversation_id,
        owner_user_id=tenant.get("sub"),
        title=req.title,
        metadata=req.metadata,
        actor_id=tenant.get("sub"),
    )
    await db.commit()
    return ApiResponse.ok(ContextResponse.model_validate(context))


@router.get(
    "/{context_id}",
    response_model=ApiResponse[ContextResponse],
    summary="查询 context",
    description="前端或调试脚本使用。用于查看当前租户下某个会话容器的来源、标题、元数据和活跃时间。",
)
async def get_context(
    context_id: str,
    db: DbDep,
    tenant: TenantDep,
) -> ApiResponse[ContextResponse]:
    """查询 context，租户隔离，跨租户访问返回 404。"""
    svc = ContextService(db)
    context = await svc.get(context_id, tenant["tenant_id"])
    if not context:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="context 不存在")
    return ApiResponse.ok(ContextResponse.model_validate(context))
