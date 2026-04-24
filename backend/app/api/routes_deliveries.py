"""
投递管理端点：创建投递、查看 DLQ、处理待投递、重放 DLQ。
"""
from fastapi import APIRouter, HTTPException

from app.api._shared import _delivery_resp
from app.api.deps import DbDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.integration import DeliveryCreateRequest, DeliveryResponse
from app.services.delivery_service import DeliveryService

router = APIRouter(tags=["deliveries"])


@router.post(
    "/v1/deliveries",
    response_model=ApiResponse[DeliveryResponse],
    status_code=201,
    summary="创建投递任务",
    description="平台组件或集成适配器使用。用于创建一条对外投递记录，支持幂等键、重试次数和 trace_id。",
)
async def create_delivery(req: DeliveryCreateRequest, db: DbDep, tenant: TenantDep):
    svc = DeliveryService(db)
    delivery = await svc.enqueue(
        tenant_id=tenant["tenant_id"],
        target_channel=req.target_channel,
        target_ref=req.target_ref,
        payload=req.payload,
        task_id=req.task_id,
        trace_id=req.trace_id,
        idempotency_key=req.idempotency_key,
        max_attempts=req.max_attempts,
    )
    return ApiResponse.ok(_delivery_resp(delivery))


@router.get(
    "/v1/deliveries/dlq",
    response_model=ApiResponse[list[DeliveryResponse]],
    summary="查看 DLQ",
    description="运维界面或告警处理脚本使用。用于查看当前租户下已经进入 DEAD 状态的投递失败记录。",
)
async def list_dlq(db: DbDep, tenant: TenantDep):
    svc = DeliveryService(db)
    deliveries = await svc.list_dead(tenant["tenant_id"])
    return ApiResponse.ok([_delivery_resp(item) for item in deliveries])


@router.post(
    "/v1/deliveries/process-due",
    response_model=ApiResponse[dict],
    summary="处理待投递或待重试任务",
    description="后台 worker、定时任务或手工运维使用。用于处理到期的投递任务并按策略重试或进入 DLQ。",
)
async def process_due_deliveries(db: DbDep, tenant: TenantDep, limit: int = 20):
    svc = DeliveryService(db)
    try:
        deliveries = await svc.process_due(tenant["tenant_id"], limit=limit)
    except Exception as exc:
        return ApiResponse.ok(
            {
                "processed_count": 0,
                "statuses": [],
                "warning": f"delivery process_due fallback: {exc}",
            }
        )
    return ApiResponse.ok({"processed_count": len(deliveries), "statuses": [item.status for item in deliveries]})


@router.post(
    "/v1/deliveries/{delivery_id}/replay",
    response_model=ApiResponse[DeliveryResponse],
    summary="重放 DLQ 投递",
    description="运维人员或恢复脚本使用。用于把指定 DEAD 投递重新放回待处理队列，便于修复外部故障后重试。",
)
async def replay_delivery(delivery_id: str, db: DbDep, tenant: TenantDep):
    svc = DeliveryService(db)
    try:
        delivery = await svc.replay_dead(delivery_id, tenant["tenant_id"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return ApiResponse.ok(_delivery_resp(delivery))