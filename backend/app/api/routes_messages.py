"""
POST /v1/messages/send — 创建任务入口
"""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, IdempotencyDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.message import MessageSendRequest, MessageSendResponse
from app.services.context_service import ContextService
from app.services.agent_link_service import agent_link_service
from app.services.routing_engine import RoutingEngine, RoutingError
from app.services.task_service import TaskService

router = APIRouter(prefix="/v1/messages", tags=["messages"])


async def create_and_dispatch_message_task(
    req: MessageSendRequest,
    db: DbDep,
    tenant: dict,
    idempotency_key: str | None = None,
    initiator_agent_id: str | None = None,
    source_system: str | None = None,
) -> MessageSendResponse:
    """创建消息任务并下发，供用户、服务账号、agent-to-agent 入口复用。"""
    tenant_id: str = tenant["tenant_id"]
    if tenant.get("token_type") == "service_account" and "messages:send" not in tenant.get("scopes", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="服务账号缺少 messages:send 权限")

    # 校验 context 归属
    ctx_svc = ContextService(db)
    context = await ctx_svc.get(req.context_id, tenant_id)
    if not context:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="context 不存在或无权访问")

    # 合并 idempotency_key（Header 优先，body 次之）
    idem_key = idempotency_key or req.idempotency_key

    # 拼接输入文本
    input_text = " ".join(p.text for p in req.parts if p.text) or None

    task_svc = TaskService(db)
    metadata = {
        **req.metadata,
        **({"initiator_agent_id": initiator_agent_id} if initiator_agent_id else {}),
        **({"source_system": source_system} if source_system else {}),
    }
    task = await task_svc.create_task(
        tenant_id=tenant_id,
        context_id=req.context_id,
        input_text=input_text,
        target_agent_id=req.target_agent_id,
        initiator_agent_id=initiator_agent_id,
        idempotency_key=idem_key,
        metadata=metadata,
        actor_id=tenant.get("sub"),
    )

    # 幂等命中：task 已存在，直接返回当前状态，不重复路由
    is_new = bool(getattr(task, "_is_newly_created", False))
    dispatch_target_agent_id: str | None = None

    # 写入用户消息（仅新建时）
    if is_new and input_text:
        await task_svc.append_message(
            task_id=task.task_id,
            context_id=req.context_id,
            role="user",
            content_text=input_text,
            source_agent_id=initiator_agent_id,
            metadata={"source_system": source_system} if source_system else None,
        )

    # 路由引擎：SUBMITTED → ROUTING → WORKING（仅新建时执行）
    if is_new:
        routing_engine = RoutingEngine(db)
        try:
            await task_svc.update_state(task.task_id, "ROUTING", tenant_id, actor_type="system")
            target_agent_id = await routing_engine.route(task)
            from sqlalchemy import update as sa_update
            from app.models.task import Task as TaskModel
            await db.execute(
                sa_update(TaskModel)
                .where(TaskModel.task_id == task.task_id)
                .values(target_agent_id=target_agent_id)
            )
            task.target_agent_id = target_agent_id
            if target_agent_id.startswith("openclaw:"):
                dispatch_target_agent_id = target_agent_id
        except RoutingError:
            await task_svc.update_state(
                task.task_id, "FAILED", tenant_id,
                reason="路由失败：无可用 Agent", actor_type="system",
            )

    # 更新 context 活跃时间
    await ctx_svc.touch(req.context_id)

    # 先提交任务、消息和路由记录，再对外下发，避免 agent 回 ack/update 时任务尚未落库。
    if is_new:
        await db.commit()
        if dispatch_target_agent_id:
            auth_token = agent_link_service.build_agent_token(tenant_id, dispatch_target_agent_id, tenant.get("sub"))
            await agent_link_service.dispatch_task(task, auth_token)

    return MessageSendResponse(
        task_id=task.task_id,
        state=task.state,
        context_id=task.context_id,
    )


@router.post(
    "/send",
    response_model=ApiResponse[MessageSendResponse],
    summary="发送消息 / 创建任务",
    description="前端、服务账号或平台组件使用。用于把一条用户消息写入 context，创建任务并触发路由，必要时下发到在线 OpenClaw Agent。",
)
async def send_message(
    req: MessageSendRequest,
    db: DbDep,
    tenant: TenantDep,
    idempotency_key: IdempotencyDep = None,
) -> ApiResponse[MessageSendResponse]:
    """
    发送消息并自动创建任务，触发路由引擎选择目标 Agent。

    - `context_id` 必须属于当前租户
    - 支持 `Idempotency-Key` Header 防重复提交
    - 路由成功后任务进入 `WORKING`，无可用 Agent 时进入 `FAILED`
    """
    response = await create_and_dispatch_message_task(req, db, tenant, idempotency_key)
    return ApiResponse.ok(response)
