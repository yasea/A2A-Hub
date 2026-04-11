"""
routing_rules CRUD
GET    /v1/routing-rules             — 列出规则
POST   /v1/routing-rules             — 创建规则
DELETE /v1/routing-rules/{rule_id}   — 删除规则
PATCH  /v1/routing-rules/{rule_id}   — 启用/禁用规则

路由测试
POST   /v1/routing/test              — 测试任务路由结果（不写 hop 记录）
"""
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select, update

from app.api.deps import DbDep, TenantDep
from app.models.routing import RoutingRule
from app.schemas.agent import RoutingRuleCreate, RoutingRuleResponse
from app.schemas.common import ApiResponse
from app.services.routing_engine import RoutingEngine, RoutingError
from app.models.task import Task

router = APIRouter(tags=["routing"])


# ── routing_rules CRUD ──────────────────────────────────────────────

@router.post(
    "/v1/routing-rules",
    response_model=ApiResponse[RoutingRuleResponse],
    status_code=201,
    summary="创建路由规则",
    description="平台管理员或租户配置页使用。用于定义任务匹配条件与目标 Agent，路由引擎按 priority 从小到大匹配。",
)
async def create_rule(req: RoutingRuleCreate, db: DbDep, tenant: TenantDep):
    """
    创建路由规则。`match_expr` 支持字段匹配：

    ```json
    {"task_type": "analysis"}
    {"task_type": ["analysis", "quote"]}
    {}
    ```

    空 `match_expr` 表示匹配所有任务（适合作为兜底规则）。`priority` 越小越优先。
    """
    rule = RoutingRule(
        tenant_id=tenant["tenant_id"],
        name=req.name,
        priority=req.priority,
        match_expr=req.match_expr,
        target_agent_id=req.target_agent_id,
        is_active=req.is_active,
    )
    db.add(rule)
    await db.flush()
    return ApiResponse.ok(_rule_resp(rule))


@router.get(
    "/v1/routing-rules",
    response_model=ApiResponse[list[RoutingRuleResponse]],
    summary="列出路由规则",
    description="平台 UI 或调试脚本使用。用于查看当前租户所有路由规则及启用状态，按优先级排序。",
)
async def list_rules(db: DbDep, tenant: TenantDep):
    """列出当前租户所有路由规则，按 priority 升序排列。"""
    result = await db.execute(
        select(RoutingRule)
        .where(RoutingRule.tenant_id == tenant["tenant_id"])
        .order_by(RoutingRule.priority)
    )
    rules = result.scalars().all()
    return ApiResponse.ok([_rule_resp(r) for r in rules])


@router.patch(
    "/v1/routing-rules/{rule_id}",
    response_model=ApiResponse[dict],
    summary="启用/禁用路由规则",
    description="平台管理员或运营配置流程使用。用于临时打开或关闭某条路由规则，不删除规则本身。",
)
async def toggle_rule(rule_id: str, body: dict[str, Any], db: DbDep, tenant: TenantDep):
    """启用或禁用路由规则。请求体：`{"is_active": true}` 或 `{"is_active": false}`。"""
    is_active = body.get("is_active")
    if is_active is None:
        raise HTTPException(status_code=422, detail="需要 is_active 字段")
    result = await db.execute(
        update(RoutingRule)
        .where(RoutingRule.id == uuid.UUID(rule_id), RoutingRule.tenant_id == tenant["tenant_id"])
        .values(is_active=is_active)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="路由规则不存在")
    return ApiResponse.ok({"rule_id": rule_id, "is_active": is_active})


@router.delete(
    "/v1/routing-rules/{rule_id}",
    response_model=ApiResponse[dict],
    summary="删除路由规则",
    description="平台管理员使用。用于永久删除当前租户下不再需要的路由规则。",
)
async def delete_rule(rule_id: str, db: DbDep, tenant: TenantDep):
    """永久删除路由规则。"""
    result = await db.execute(
        delete(RoutingRule).where(
            RoutingRule.id == uuid.UUID(rule_id),
            RoutingRule.tenant_id == tenant["tenant_id"],
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="路由规则不存在")
    return ApiResponse.ok({"rule_id": rule_id, "deleted": True})


# ── 路由测试（dry-run，不写 hop 记录）──────────────────────────────

@router.post(
    "/v1/routing/test",
    response_model=ApiResponse[dict],
    summary="路由测试（dry-run）",
    description="平台配置页或联调脚本使用。用于验证某个任务或虚拟 task_type 会路由到哪个 Agent，不写入 hop 记录。",
)
async def test_routing(body: dict[str, Any], db: DbDep, tenant: TenantDep):
    """
    测试路由结果，**不写入任何数据**。

    请求体示例：
    ```json
    {"task_type": "analysis"}
    {"task_type": "generic", "target_agent_id": "openclaw:ava"}
    {"task_id": "task_xxx"}
    ```
    """
    task_id = body.get("task_id")
    if task_id:
        result = await db.execute(
            select(Task).where(Task.task_id == task_id, Task.tenant_id == tenant["tenant_id"])
        )
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="task 不存在")
    else:
        # 构造虚拟 task 用于测试
        task = Task(
            task_id="__test__",
            tenant_id=tenant["tenant_id"],
            context_id="__test__",
            task_type=body.get("task_type", "generic"),
            state="SUBMITTED",
            priority="normal",
            target_agent_id=body.get("target_agent_id"),
            retry_count=0,
            approval_required=False,
            metadata_json={},
        )

    engine = RoutingEngine(db)
    try:
        target = await engine.route(task, dry_run=True)
        return ApiResponse.ok({"target_agent_id": target, "routed": True})
    except RoutingError as e:
        return ApiResponse.ok({"routed": False, "reason": str(e)})


def _rule_resp(rule: RoutingRule) -> RoutingRuleResponse:
    return RoutingRuleResponse(
        id=str(rule.id),
        tenant_id=rule.tenant_id,
        name=rule.name,
        priority=rule.priority,
        match_expr=rule.match_expr,
        target_agent_id=rule.target_agent_id,
        is_active=rule.is_active,
    )
