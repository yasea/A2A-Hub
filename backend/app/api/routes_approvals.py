"""
审批管理端点：创建审批、查询审批、处理审批、批量过期。
"""
from fastapi import APIRouter, HTTPException

from app.api._shared import _record_error_event
from app.api.deps import DbDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.integration import ApprovalCreateRequest, ApprovalResolveRequest, ApprovalResponse
from app.services.approval_service import ApprovalService

router = APIRouter(tags=["approvals"])


@router.post(
    "/v1/approvals",
    response_model=ApiResponse[ApprovalResponse],
    status_code=201,
    summary="创建审批",
    description="平台组件、Agent 或人工流程使用。用于把任务切换到待审批状态，并记录审批人、原因和外部关联键。",
)
async def create_approval(req: ApprovalCreateRequest, db: DbDep, tenant: TenantDep):
    svc = ApprovalService(db)
    approval = await svc.create(
        tenant_id=tenant["tenant_id"],
        task_id=req.task_id,
        approver_user_id=req.approver_user_id,
        requested_by=tenant.get("sub"),
        reason=req.reason,
        external_key=req.external_key,
        metadata=req.metadata,
    )
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.get(
    "/v1/approvals/{approval_id}",
    response_model=ApiResponse[ApprovalResponse],
    summary="查询审批",
    description="前端审批页或调试脚本使用。用于查看当前租户下单个审批项的状态、审批人、任务关联和处理结果。",
)
async def get_approval(approval_id: str, db: DbDep, tenant: TenantDep):
    svc = ApprovalService(db)
    approval = await svc.get(approval_id, tenant["tenant_id"])
    if not approval:
        raise HTTPException(status_code=404, detail="审批不存在")
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.post(
    "/v1/approvals/{approval_id}/resolve",
    response_model=ApiResponse[ApprovalResponse],
    summary="处理审批",
    description="审批人、前端或受控自动化流程使用。用于批准或拒绝待审批项，并根据结果推进关联任务状态。",
)
async def resolve_approval(approval_id: str, req: ApprovalResolveRequest, db: DbDep, tenant: TenantDep):
    if req.decision not in {"APPROVED", "REJECTED"}:
        raise HTTPException(status_code=422, detail="decision 必须是 APPROVED 或 REJECTED")
    svc = ApprovalService(db)
    try:
        approval = await svc.resolve(
            approval_id=approval_id,
            tenant_id=tenant["tenant_id"],
            decision=req.decision,
            note=req.note,
            actor_id=tenant.get("sub"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=404 if "不存在" in str(exc) else 422, detail=str(exc))
    return ApiResponse.ok(ApprovalResponse.model_validate(approval))


@router.post(
    "/v1/approvals/expire",
    response_model=ApiResponse[dict],
    summary="批量过期待审批项",
    description="定时任务或运维脚本使用。用于扫描当前租户下超时未处理的审批项并批量标记过期。",
)
async def expire_approvals(db: DbDep, tenant: TenantDep):
    svc = ApprovalService(db)
    approvals = await svc.expire_pending(tenant["tenant_id"])
    return ApiResponse.ok({"expired_count": len(approvals)})