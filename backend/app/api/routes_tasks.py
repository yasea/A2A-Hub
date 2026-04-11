"""
GET   /v1/tasks/{task_id}        — 查询任务
PATCH /v1/tasks/{task_id}/state  — 更新任务状态
POST  /v1/tasks/{task_id}/cancel — 取消任务
"""
from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.task import TaskMessageResponse, TaskResponse, TaskStateUpdate
from app.services.task_service import InvalidTaskTransitionError, TaskNotFoundError, TaskService

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


@router.get(
    "/{task_id}",
    response_model=ApiResponse[TaskResponse],
    summary="查询任务详情",
    description="前端、平台组件或联调脚本使用。用于查看任务状态、目标 Agent、输入输出、重试和审批信息。",
)
async def get_task(
    task_id: str,
    db: DbDep,
    tenant: TenantDep,
) -> ApiResponse[TaskResponse]:
    """查询任务详情，租户隔离，跨租户访问返回 404。"""
    svc = TaskService(db)
    task = await svc.get(task_id, tenant["tenant_id"])
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return ApiResponse.ok(TaskResponse.model_validate(task))


@router.get(
    "/{task_id}/messages",
    response_model=ApiResponse[list[TaskMessageResponse]],
    summary="查询任务消息",
    description="前端对话页或调试脚本使用。用于按顺序查看任务关联的 user/assistant/system 消息。",
)
async def list_task_messages(
    task_id: str,
    db: DbDep,
    tenant: TenantDep,
) -> ApiResponse[list[TaskMessageResponse]]:
    """查询任务下的消息列表，租户隔离，按 seq_no 升序返回。"""
    svc = TaskService(db)
    try:
        messages = await svc.list_messages(task_id, tenant["tenant_id"])
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return ApiResponse.ok([TaskMessageResponse.model_validate(message) for message in messages])


@router.patch(
    "/{task_id}/state",
    response_model=ApiResponse[TaskResponse],
    summary="更新任务状态",
    description="平台组件、人工运维或受控自动化流程使用。用于按状态机推进任务，并可写入原因和输出内容。",
)
async def update_task_state(
    task_id: str,
    req: TaskStateUpdate,
    db: DbDep,
    tenant: TenantDep,
) -> ApiResponse[TaskResponse]:
    """按状态机规则更新任务状态，可选附带原因和输出内容。"""
    svc = TaskService(db)
    try:
        task = await svc.update_state(
            task_id=task_id,
            new_state=req.new_state,
            tenant_id=tenant["tenant_id"],
            reason=req.reason,
            actor_type="user" if tenant.get("sub") else "system",
            actor_id=tenant.get("sub"),
            output_text=req.output_text,
        )
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidTaskTransitionError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return ApiResponse.ok(TaskResponse.model_validate(task))


@router.post(
    "/{task_id}/cancel",
    response_model=ApiResponse[TaskResponse],
    summary="取消任务",
    description="前端用户、平台管理员或自动化流程使用。用于取消仍在进行中的任务，终态任务不会被再次取消。",
)
async def cancel_task(
    task_id: str,
    db: DbDep,
    tenant: TenantDep,
) -> ApiResponse[TaskResponse]:
    """取消任务。终态任务（COMPLETED/FAILED/CANCELED/EXPIRED）不可取消，返回 422。"""
    svc = TaskService(db)
    try:
        task = await svc.cancel(
            task_id=task_id,
            tenant_id=tenant["tenant_id"],
            actor_id=tenant.get("sub"),
        )
    except TaskNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except InvalidTaskTransitionError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    return ApiResponse.ok(TaskResponse.model_validate(task))
