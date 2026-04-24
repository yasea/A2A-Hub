"""
SSE 和计量端点：任务 SSE 订阅、Prometheus metrics、计量汇总。
"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.api._shared import _record_error_event
from app.api.deps import DbDep, TenantDep
from app.schemas.common import ApiResponse
from app.schemas.integration import MeteringSummaryItem
from app.services.metering_service import MeteringService
from app.services.stream_service import task_event_broker
from app.services.task_service import TaskService

router = APIRouter(tags=["events"])


@router.get(
    "/v1/tasks/{task_id}/subscribe",
    summary="订阅任务状态 SSE",
    description="前端任务详情页或调试工具使用。用于通过 Server-Sent Events 实时接收任务状态变化和相关事件。",
)
async def subscribe_task(task_id: str, db: DbDep, tenant: TenantDep):
    task = await TaskService(db).get(task_id, tenant["tenant_id"])
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        queue = task_event_broker.subscribe(task_id)
        try:
            yield "event: ready\ndata: {\"status\": \"subscribed\"}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            task_event_broker.unsubscribe(task_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    summary="查看基础指标",
    description="监控系统或运维脚本使用。以 Prometheus 文本格式输出当前租户的基础计量聚合指标。",
)
async def metrics(db: DbDep, tenant: TenantDep):
    metering = MeteringService(db)
    summary = await metering.summary(tenant["tenant_id"])
    lines = []
    for item in summary:
        key = f"a2a_{item['event_type']}_{item['metric_name']}".replace("-", "_")
        lines.append(f"{key} {item['total']}")
    return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""))


@router.get(
    "/v1/metering/summary",
    response_model=ApiResponse[list[MeteringSummaryItem]],
    summary="查看租户计量汇总",
    description="平台账单、容量统计或运维页面使用。用于查看当前租户按事件类型和指标名聚合后的用量汇总。",
)
async def metering_summary(db: DbDep, tenant: TenantDep):
    metering = MeteringService(db)
    items = [MeteringSummaryItem(**item) for item in await metering.summary(tenant["tenant_id"])]
    return ApiResponse.ok(items)