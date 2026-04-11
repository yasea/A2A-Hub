import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.routes_agents import get_agent, register_agent
from app.api.routes_messages import send_message
from app.api.routes_routing import delete_rule, toggle_rule
from app.api.routes_tasks import cancel_task, get_task, update_task_state
from app.schemas.agent import AgentCreate
from app.schemas.task import TaskStateUpdate
from app.core.security import create_access_token, decode_access_token, get_current_tenant
from app.services.agent_registry import AgentNotFoundError, AgentRegistry
from app.services.routing_engine import RoutingEngine, RoutingError
from app.services.delivery_service import DeliveryService
from app.services.task_service import InvalidTaskTransitionError, TaskNotFoundError, TaskService


class DummyResult:
    """模拟数据库执行结果。"""

    def __init__(self, rowcount=1):
        self.rowcount = rowcount


class CoreFlowsTest(unittest.IsolatedAsyncioTestCase):
    """覆盖核心流程、鉴权与租户隔离的回归测试。"""

    async def test_create_task_marks_new_record(self):
        """新建任务时应标记为新创建记录。"""
        db = AsyncMock()
        db.add = Mock()
        svc = TaskService(db)
        svc._find_by_idempotency = AsyncMock(return_value=None)
        svc.audit.log = AsyncMock()

        task = await svc.create_task(
            tenant_id="tenant_001",
            context_id="ctx_001",
            input_text="hello",
            idempotency_key="idem-1",
        )

        self.assertTrue(getattr(task, "_is_newly_created"))
        svc._find_by_idempotency.assert_awaited_once_with("tenant_001", "idem-1")

    async def test_create_task_marks_existing_idempotent_record(self):
        """幂等命中已有任务时不应重复创建。"""
        db = AsyncMock()
        db.add = Mock()
        svc = TaskService(db)
        existing = SimpleNamespace(task_id="task_existing")
        svc._find_by_idempotency = AsyncMock(return_value=existing)

        task = await svc.create_task(
            tenant_id="tenant_001",
            context_id="ctx_001",
            idempotency_key="idem-1",
        )

        self.assertIs(task, existing)
        self.assertFalse(getattr(task, "_is_newly_created"))
        db.add.assert_not_called()

    async def test_create_task_reuses_existing_source_message_record(self):
        """命中同源消息时应复用历史任务，兼容旧数据缺少 idempotency_key 的情况。"""
        db = AsyncMock()
        db.add = Mock()
        svc = TaskService(db)
        existing = SimpleNamespace(task_id="task_existing_from_source")
        svc._find_by_source_message = AsyncMock(return_value=existing)

        task = await svc.create_task(
            tenant_id="tenant_001",
            context_id="ctx_001",
            input_text="hello",
            source_system="openclaw",
            source_message_id="evt_001",
            idempotency_key="openclaw:sess_001:evt_001",
        )

        self.assertIs(task, existing)
        self.assertFalse(getattr(task, "_is_newly_created"))
        svc._find_by_source_message.assert_awaited_once_with("tenant_001", "openclaw", "evt_001")
        db.add.assert_not_called()

    async def test_create_task_source_message_lookup_is_tenant_scoped(self):
        """外部消息去重查询必须带 tenant_id，避免跨租户误复用。"""
        db = AsyncMock()
        svc = TaskService(db)
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        await svc._find_by_source_message("tenant_001", "openclaw", "evt_001")

        stmt = db.execute.await_args.args[0]
        sql = str(stmt)
        self.assertIn("tasks.tenant_id", sql)
        self.assertIn("tasks.source_system", sql)
        self.assertIn("tasks.source_message_id", sql)

    async def test_send_message_skips_routing_for_existing_idempotent_task(self):
        """幂等命中旧任务时不应重复写消息和重复路由。"""
        db = AsyncMock()
        req = SimpleNamespace(
            context_id="ctx_001",
            parts=[SimpleNamespace(text="hello")],
            idempotency_key="idem-1",
            target_agent_id=None,
            metadata={},
        )
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        existing_task = SimpleNamespace(
            task_id="task_existing",
            state="WORKING",
            context_id="ctx_001",
            target_agent_id="openclaw:ava",
            _is_newly_created=False,
        )

        with patch("app.api.routes_messages.ContextService") as context_cls, patch(
            "app.api.routes_messages.TaskService"
        ) as task_cls, patch("app.api.routes_messages.RoutingEngine") as routing_cls:
            context_svc = context_cls.return_value
            context_svc.get = AsyncMock(return_value=SimpleNamespace(context_id="ctx_001"))
            context_svc.touch = AsyncMock()

            task_svc = task_cls.return_value
            task_svc.create_task = AsyncMock(return_value=existing_task)
            task_svc.append_message = AsyncMock()
            task_svc.update_state = AsyncMock()

            response = await send_message(req=req, db=db, tenant=tenant, idempotency_key=None)

        self.assertEqual(response.data.task_id, "task_existing")
        self.assertEqual(response.data.state, "WORKING")
        task_svc.append_message.assert_not_awaited()
        task_svc.update_state.assert_not_awaited()
        routing_cls.assert_not_called()

    async def test_send_message_dispatches_online_openclaw_agent(self):
        """任务路由到 OpenClaw Agent 后应尝试通过统一 Agent Link 下发。"""
        db = AsyncMock()
        req = SimpleNamespace(
            context_id="ctx_001",
            parts=[SimpleNamespace(text="hello")],
            idempotency_key=None,
            target_agent_id=None,
            metadata={},
        )
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        new_task = SimpleNamespace(
            task_id="task_new",
            state="SUBMITTED",
            context_id="ctx_001",
            target_agent_id=None,
            _is_newly_created=True,
        )

        with patch("app.api.routes_messages.ContextService") as context_cls, patch(
            "app.api.routes_messages.TaskService"
        ) as task_cls, patch("app.api.routes_messages.RoutingEngine") as routing_cls, patch(
            "app.api.routes_messages.agent_link_service"
        ) as agent_link:
            context_svc = context_cls.return_value
            context_svc.get = AsyncMock(return_value=SimpleNamespace(context_id="ctx_001"))
            context_svc.touch = AsyncMock()

            task_svc = task_cls.return_value
            task_svc.create_task = AsyncMock(return_value=new_task)
            task_svc.append_message = AsyncMock()
            task_svc.update_state = AsyncMock(side_effect=[SimpleNamespace(task_id="task_new", state="ROUTING")])
            routing_cls.return_value.route = AsyncMock(return_value="openclaw:ava")
            agent_link.build_agent_token = Mock(return_value="agent-token-001")
            agent_link.dispatch_task = AsyncMock()

            response = await send_message(req=req, db=db, tenant=tenant, idempotency_key=None)

        self.assertEqual(response.data.task_id, "task_new")
        agent_link.dispatch_task.assert_awaited_once()

    async def test_cancel_task_returns_404_when_missing(self):
        """取消不存在任务时返回 404。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}

        with patch("app.api.routes_tasks.TaskService") as task_cls:
            task_cls.return_value.cancel = AsyncMock(side_effect=TaskNotFoundError("missing"))
            with self.assertRaises(HTTPException) as ctx:
                await cancel_task(task_id="task_missing", db=db, tenant=tenant)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_cancel_task_returns_422_for_invalid_transition(self):
        """取消终态任务时返回 422。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}

        with patch("app.api.routes_tasks.TaskService") as task_cls:
            task_cls.return_value.cancel = AsyncMock(
                side_effect=InvalidTaskTransitionError("invalid transition")
            )
            with self.assertRaises(HTTPException) as ctx:
                await cancel_task(task_id="task_done", db=db, tenant=tenant)

        self.assertEqual(ctx.exception.status_code, 422)

    async def test_agent_register_updates_returned_fields(self):
        """更新已存在 Agent 时，返回对象字段应与最新值一致。"""
        db = AsyncMock()
        svc = AgentRegistry(db)
        svc.audit.log = AsyncMock()
        existing = SimpleNamespace(
            agent_id="openclaw:ava",
            tenant_id="tenant_001",
            display_name="old",
            capabilities={"generic": True},
            auth_scheme=None,
            config_json={},
        )
        svc.get = AsyncMock(return_value=existing)

        updated = await svc.register(
            agent_id="openclaw:ava",
            tenant_id="tenant_001",
            agent_type="federated",
            display_name="AVA",
            capabilities={"analysis": True},
            auth_scheme="jwt",
            config_json={"base_url": "https://example.com"},
        )

        self.assertIs(updated, existing)
        self.assertEqual(updated.display_name, "AVA")
        self.assertEqual(updated.capabilities, {"analysis": True})
        self.assertEqual(updated.auth_scheme, "jwt")
        self.assertEqual(updated.config_json, {"base_url": "https://example.com"})

    async def test_agent_set_status_raises_not_found(self):
        """更新不存在 Agent 状态时抛出未找到异常。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=DummyResult(rowcount=0))
        svc = AgentRegistry(db)
        svc.audit.log = AsyncMock()

        with self.assertRaises(AgentNotFoundError):
            await svc.set_status("missing", "tenant_001", "INACTIVE")

    async def test_routing_engine_rejects_loop_before_recording(self):
        """命中循环路由时不应写入 hop 记录。"""
        engine = RoutingEngine(AsyncMock())
        task = SimpleNamespace(task_id="task_001", tenant_id="tenant_001")
        engine._get_active_agent = AsyncMock(return_value=SimpleNamespace(agent_id="openclaw:ava"))
        engine.check_loop = AsyncMock(return_value=True)
        engine._record_hop = AsyncMock()

        with self.assertRaises(RoutingError):
            await engine._finalize_target(
                task=task,
                hop_count=1,
                from_agent_id=None,
                target_agent_id="openclaw:ava",
                reason="explicit",
                rule_id=None,
                dry_run=False,
            )

        engine._record_hop.assert_not_awaited()

    async def test_delivery_enqueue_idempotency_is_tenant_scoped(self):
        """投递幂等查询必须带 tenant_id，避免不同租户误命中同一记录。"""
        db = AsyncMock()
        db.add = Mock()
        db.flush = AsyncMock()
        existing_result = SimpleNamespace(scalar_one_or_none=lambda: None)
        db.execute = AsyncMock(return_value=existing_result)
        svc = DeliveryService(db)
        svc.audit.log = AsyncMock()

        await svc.enqueue(
            tenant_id="tenant_001",
            target_channel="rocket_chat",
            target_ref={"room_id": "room_001"},
            payload={"text": "hello"},
            idempotency_key="idem-delivery-1",
        )

        stmt = db.execute.await_args_list[0].args[0]
        sql = str(stmt)
        self.assertIn("deliveries.tenant_id", sql)
        self.assertIn("deliveries.idempotency_key", sql)

    async def test_toggle_rule_returns_404_for_missing_rule(self):
        """禁用不存在的路由规则时返回 404。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=DummyResult(rowcount=0))
        tenant = {"tenant_id": "tenant_001"}

        with self.assertRaises(HTTPException) as ctx:
            await toggle_rule(
                rule_id="123e4567-e89b-12d3-a456-426614174000",
                body={"is_active": False},
                db=db,
                tenant=tenant,
            )

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_delete_rule_returns_404_for_missing_rule(self):
        """删除不存在的路由规则时返回 404。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=DummyResult(rowcount=0))
        tenant = {"tenant_id": "tenant_001"}

        with self.assertRaises(HTTPException) as ctx:
            await delete_rule(
                rule_id="123e4567-e89b-12d3-a456-426614174000",
                db=db,
                tenant=tenant,
            )

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_token_round_trip_extracts_tenant(self):
        """生成的测试 Token 应能正确解析出租户与操作者。"""
        token = create_access_token("test-user", {"tenant_id": "tenant_001"})

        payload = decode_access_token(token)
        tenant = await get_current_tenant(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )

        self.assertEqual(payload["sub"], "test-user")
        self.assertEqual(payload["tenant_id"], "tenant_001")
        self.assertEqual(tenant["tenant_id"], "tenant_001")
        self.assertEqual(tenant["sub"], "test-user")
        self.assertEqual(tenant["token_type"], "user")
        self.assertEqual(tenant["scopes"], [])

    async def test_token_without_tenant_id_is_rejected(self):
        """缺少 tenant_id 的 Token 应被拒绝。"""
        token = create_access_token("test-user")

        with self.assertRaises(HTTPException) as ctx:
            await get_current_tenant(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
            )

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Token 缺少 tenant_id")

    async def test_get_task_returns_404_for_cross_tenant_access(self):
        """跨租户读取任务时应返回 404，避免泄露资源存在性。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_002", "sub": "user_2"}

        with patch("app.api.routes_tasks.TaskService") as task_cls:
            task_cls.return_value.get = AsyncMock(return_value=None)
            with self.assertRaises(HTTPException) as ctx:
                await get_task(task_id="task_001", db=db, tenant=tenant)

        task_cls.return_value.get.assert_awaited_once_with("task_001", "tenant_002")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_update_task_state_returns_completed_payload(self):
        """更新任务状态成功时应返回完成态与输出内容。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        task = SimpleNamespace(
            task_id="task_001",
            tenant_id="tenant_001",
            context_id="ctx_001",
            target_agent_id="openclaw:ava",
            task_type="analysis",
            state="COMPLETED",
            priority="normal",
            input_text="请分析需求",
            output_text="分析已完成",
            failure_reason=None,
            approval_required=False,
            retry_count=0,
            created_at="2026-04-08T00:00:00Z",
            updated_at="2026-04-08T00:01:00Z",
            completed_at="2026-04-08T00:01:00Z",
        )

        with patch("app.api.routes_tasks.TaskService") as task_cls:
            task_cls.return_value.update_state = AsyncMock(return_value=task)
            response = await update_task_state(
                task_id="task_001",
                req=TaskStateUpdate(new_state="COMPLETED", output_text="分析已完成"),
                db=db,
                tenant=tenant,
            )

        task_cls.return_value.update_state.assert_awaited_once()
        self.assertEqual(response.data.state, "COMPLETED")
        self.assertEqual(response.data.output_text, "分析已完成")
        self.assertEqual(response.data.target_agent_id, "openclaw:ava")

    async def test_update_task_state_returns_422_for_invalid_transition(self):
        """任务状态更新遇到非法跳转时返回 422。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}

        with patch("app.api.routes_tasks.TaskService") as task_cls:
            task_cls.return_value.update_state = AsyncMock(
                side_effect=InvalidTaskTransitionError("非法状态跳转")
            )
            with self.assertRaises(HTTPException) as ctx:
                await update_task_state(
                    task_id="task_001",
                    req=TaskStateUpdate(new_state="COMPLETED"),
                    db=db,
                    tenant=tenant,
                )

        self.assertEqual(ctx.exception.status_code, 422)

    async def test_get_agent_returns_full_agent_payload(self):
        """查询单个 Agent 时应返回完整详情。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        agent = SimpleNamespace(
            agent_id="openclaw:ava",
            tenant_id="tenant_001",
            agent_type="federated",
            display_name="AVA",
            status="ACTIVE",
            capabilities={"analysis": True},
            auth_scheme="jwt",
            config_json={"base_url": "https://example.com"},
        )

        with patch("app.api.routes_agents.AgentRegistry") as registry_cls:
            registry_cls.return_value.get = AsyncMock(return_value=agent)
            response = await get_agent(agent_id="openclaw:ava", db=db, tenant=tenant)

        self.assertEqual(response.data.agent_id, "openclaw:ava")
        self.assertEqual(response.data.auth_scheme, "jwt")
        self.assertEqual(response.data.config_json, {"base_url": "https://example.com"})

    async def test_register_agent_updates_existing_config_via_api(self):
        """重复注册同一 Agent 时应返回更新后的配置。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        agent = SimpleNamespace(
            agent_id="openclaw:ava",
            tenant_id="tenant_001",
            agent_type="federated",
            display_name="AVA v2",
            status="ACTIVE",
            capabilities={"analysis": True, "generic": True},
            auth_scheme="jwt",
            config_json={"base_url": "https://new.example.com"},
        )

        with patch("app.api.routes_agents.AgentRegistry") as registry_cls:
            registry_cls.return_value.register = AsyncMock(return_value=agent)
            response = await register_agent(
                req=AgentCreate(
                    agent_id="openclaw:ava",
                    agent_type="federated",
                    display_name="AVA v2",
                    capabilities={"analysis": True, "generic": True},
                    auth_scheme="jwt",
                    config_json={"base_url": "https://new.example.com"},
                ),
                db=db,
                tenant=tenant,
            )

        self.assertEqual(response.data.display_name, "AVA v2")
        self.assertEqual(response.data.config_json, {"base_url": "https://new.example.com"})


if __name__ == "__main__":
    unittest.main()
