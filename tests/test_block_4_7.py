import hashlib
import hmac
import io
import tarfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.api.routes_agent_link import (
    agent_link_report_error,
    agent_link_install_report,
    agent_link_presence,
    agent_link_prompt,
    agent_link_friend_tools,
    router as agent_link_router,
    agent_link_self_register,
    openclaw_connect_page,
    openclaw_connect_markdown,
    openclaw_dbim_mqtt_install_script,
    download_dbim_mqtt_plugin,
    get_agent_link_manifest,
)
from app.api.routes_openclaw import (
    create_openclaw_connect_link,
    get_openclaw_bootstrap,
    get_openclaw_onboarding_info,
    ingest_openclaw_approval,
    ingest_openclaw_transcript,
    register_openclaw_agent,
)
from app.api.routes_approvals import resolve_approval
from app.api.routes_deliveries import process_due_deliveries, replay_delivery
from app.api.routes_events import subscribe_task
from app.core.db import Base, AsyncSessionLocal
from app.core.config import settings
from app.core.security import create_access_token, decode_access_token
from app.main import custom_swagger_docs
from app.schemas.integration import (
    AgentLinkErrorReportRequest,
    AgentLinkInstallReportRequest,
    AgentLinkPresenceRequest,
    AgentLinkSelfRegisterRequest,
    OpenClawAgentRegisterRequest,
    ApprovalResolveRequest,
)
from app.schemas.integration import OpenClawConnectLinkRequest
from app.services.agent_link_service import AgentLinkService
from app.services.openclaw_gateway_service import OpenClawConnection, openclaw_gateway_broker
from app.services.approval_service import ApprovalService
from app.services.delivery_service import DeliveryService
from app.services.mqtt_auth import tenant_mqtt_username, tenant_mqtt_password
from app.services.openclaw_service import OpenClawService
from app.services.rocketchat_service import RocketChatService
from app.services.stream_service import task_event_broker
from app.services.webhook_security import WebhookSecurityService


class FakeScalarResult:
    """提供 scalar_one_or_none 的简化结果对象。"""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeScalars:
    """提供 scalars().all() 的简化结果对象。"""

    def __init__(self, values):
        self._values = values

    def all(self):
        return self._values


class FakeListResult:
    """提供 scalars().all() 链式接口。"""

    def __init__(self, values):
        self._values = values

    def scalars(self):
        return FakeScalars(self._values)


class BlockFourToSevenTest(unittest.IsolatedAsyncioTestCase):
    """覆盖版块 4-7 的核心能力回归测试。"""

    async def test_init_metadata_includes_agent_link_error_events_table(self):
        """db-init 使用 Base.metadata.create_all 时必须包含错误事件表。"""
        self.assertIn("agent_link_error_events", Base.metadata.tables)

    async def test_delivery_success_marks_delivered(self):
        """投递成功后应进入 DELIVERED。"""
        db = AsyncMock()
        svc = DeliveryService(db)
        svc.audit.log = AsyncMock()
        svc.metering.record = AsyncMock()
        svc._dispatch = AsyncMock()
        delivery = SimpleNamespace(
            delivery_id=uuid4(),
            tenant_id="tenant_001",
            task_id="task_001",
            target_channel="rocket_chat",
            target_ref={"room_id": "room_1"},
            payload={"text": "hello"},
            status="PENDING",
            attempt_count=0,
            max_attempts=3,
            trace_id=None,
            next_retry_at=None,
            last_error=None,
            dead_letter_reason=None,
        )

        result = await svc.process_delivery(delivery)

        self.assertEqual(result.status, "DELIVERED")
        self.assertEqual(result.attempt_count, 1)
        svc._dispatch.assert_awaited_once()

    async def test_delivery_failure_reaches_dlq(self):
        """超出最大重试次数后应进入 DEAD。"""
        db = AsyncMock()
        svc = DeliveryService(db)
        svc.audit.log = AsyncMock()
        svc.metering.record = AsyncMock()
        delivery = SimpleNamespace(
            delivery_id=uuid4(),
            tenant_id="tenant_001",
            task_id="task_001",
            target_channel="rocket_chat",
            target_ref={"simulate": "fail"},
            payload={"text": "hello"},
            status="PENDING",
            attempt_count=0,
            max_attempts=1,
            trace_id=None,
            next_retry_at=None,
            last_error=None,
            dead_letter_reason=None,
        )

        result = await svc.process_delivery(delivery)

        self.assertEqual(result.status, "DEAD")
        self.assertEqual(result.dead_letter_reason, "模拟投递失败")

    async def test_delivery_failure_ignores_audit_and_metering_errors(self):
        """投递失败进入 DLQ 时，不应因审计或计量失败而再抛 500。"""
        db = AsyncMock()
        svc = DeliveryService(db)
        svc.audit.log = AsyncMock(side_effect=RuntimeError("审计写入失败"))
        svc.metering.record = AsyncMock(side_effect=RuntimeError("计量写入失败"))
        delivery = SimpleNamespace(
            delivery_id=uuid4(),
            tenant_id="tenant_001",
            task_id="task_001",
            target_channel="rocket_chat",
            target_ref={"simulate": "fail"},
            payload={"text": "hello"},
            status="PENDING",
            attempt_count=0,
            max_attempts=1,
            trace_id=None,
            next_retry_at=None,
            last_error=None,
            dead_letter_reason=None,
        )

        result = await svc.process_delivery(delivery)

        self.assertEqual(result.status, "DEAD")

    async def test_process_due_falls_back_to_dead_on_unexpected_error(self):
        """批处理遇到未预期异常时，仍应将该投递标记为 DEAD 并返回结果。"""
        delivery = SimpleNamespace(
            delivery_id=uuid4(),
            tenant_id="tenant_001",
            task_id="task_001",
            target_channel="rocket_chat",
            target_ref={},
            payload={},
            status="PENDING",
            attempt_count=0,
            max_attempts=1,
            trace_id=None,
            next_retry_at=None,
            last_error=None,
            dead_letter_reason=None,
            created_at=datetime.now(timezone.utc),
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeListResult([delivery]))
        svc = DeliveryService(db)
        svc.audit.log = AsyncMock()
        svc.metering.record = AsyncMock()
        svc.process_delivery = AsyncMock(side_effect=RuntimeError("未知异常"))

        result = await svc.process_due("tenant_001", limit=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, "DEAD")

    async def test_approval_create_moves_task_to_auth_required(self):
        """创建审批时应将任务切换到 AUTH_REQUIRED。"""
        db = AsyncMock()
        db.add = Mock()
        db.flush = AsyncMock()
        svc = ApprovalService(db)
        svc.audit.log = AsyncMock()
        svc.metering.record = AsyncMock()
        svc.task_service.get = AsyncMock(
            return_value=SimpleNamespace(task_id="task_001", context_id="ctx_001", state="WORKING")
        )
        svc.task_service.update_state = AsyncMock()

        approval = await svc.create(
            tenant_id="tenant_001",
            task_id="task_001",
            approver_user_id="user_approve",
            requested_by="user_request",
            reason="需要审批",
        )

        self.assertEqual(approval.status, "PENDING")
        db.flush.assert_awaited_once()
        svc.task_service.update_state.assert_awaited_once()

    async def test_approval_resolve_approved_restores_working(self):
        """审批通过后任务应恢复到 WORKING，并产生回写投递。"""
        db = AsyncMock()
        svc = ApprovalService(db)
        svc.audit.log = AsyncMock()
        svc.task_service.update_state = AsyncMock()
        svc.delivery.enqueue = AsyncMock()
        approval = SimpleNamespace(
            approval_id="appr_001",
            tenant_id="tenant_001",
            task_id="task_001",
            context_id="ctx_001",
            status="PENDING",
            decision_note=None,
            resolved_at=None,
        )
        svc.get = AsyncMock(return_value=approval)

        result = await svc.resolve(
            approval_id="appr_001",
            tenant_id="tenant_001",
            decision="APPROVED",
            note="同意执行",
            actor_id="user_approve",
        )

        self.assertEqual(result.status, "APPROVED")
        svc.task_service.update_state.assert_awaited_once()
        svc.delivery.enqueue.assert_awaited_once()

    async def test_rocketchat_message_creates_context_and_routes(self):
        """Rocket.Chat 入站消息应创建任务并触发路由。"""
        db = AsyncMock()
        svc = RocketChatService(db)
        svc.get_or_create_context = AsyncMock(return_value="ctx_rc_001")
        svc.tasks.create_task = AsyncMock(
            return_value=SimpleNamespace(task_id="task_rc_001", state="SUBMITTED", _is_newly_created=True)
        )
        svc.tasks.append_message = AsyncMock()
        svc.tasks.update_state = AsyncMock()
        svc.deliveries.enqueue = AsyncMock()
        svc.metering.record = AsyncMock()

        with patch("app.services.rocketchat_service.RoutingEngine") as routing_cls, patch(
            "app.services.rocketchat_service.agent_link_service"
        ) as agent_link:
            routing_cls.return_value.route = AsyncMock(return_value="openclaw:ava")
            agent_link.build_agent_token = Mock(return_value="agent-token-001")
            agent_link.dispatch_task = AsyncMock()
            result = await svc.handle_incoming_message(
                tenant_id="tenant_001",
                room_id="room_001",
                text="请分析客户需求",
                sender_id="user_001",
                sender_name="张三",
                server_url="https://rc.example.com",
                metadata={"message_id": "msg_rc_001"},
            )

        self.assertEqual(result["context_id"], "ctx_rc_001")
        self.assertEqual(result["task_id"], "task_rc_001")
        svc.tasks.append_message.assert_awaited_once()
        self.assertEqual(svc.tasks.update_state.await_count, 1)
        self.assertTrue(db.execute.await_count >= 1)
        agent_link.dispatch_task.assert_awaited_once()

    async def test_openclaw_transcript_maps_to_task_and_message(self):
        """OpenClaw transcript 应映射为任务与消息。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeScalarResult(None))
        svc = OpenClawService(db)
        svc.get_or_create_context = AsyncMock(return_value="ctx_oc_001")
        svc.tasks.create_task = AsyncMock(
            return_value=SimpleNamespace(task_id="task_oc_001", context_id="ctx_oc_001", state="SUBMITTED")
        )
        svc.tasks.append_message = AsyncMock()
        svc.metering.record = AsyncMock()

        result = await svc.ingest_transcript(
            tenant_id="tenant_001",
            session_key="sess_001",
            event_id="evt_001",
            text="OpenClaw 返回了处理结果",
            sender_type="agent",
            sender_id="openclaw:ava",
        )

        self.assertEqual(result["task_id"], "task_oc_001")
        svc.tasks.append_message.assert_awaited_once()
        _, kwargs = svc.tasks.create_task.await_args
        self.assertEqual(kwargs["idempotency_key"], "openclaw:sess_001:evt_001")

    async def test_webhook_security_rejects_invalid_signature(self):
        """Webhook 签名错误时应拒绝。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeScalarResult(None))
        svc = WebhookSecurityService(db)
        body = b'{"room_id":"room_001"}'

        with self.assertRaises(HTTPException) as ctx:
            await svc.verify(
                source_system="rocket_chat",
                secret="secret-a",
                timestamp=str(int(datetime.now(timezone.utc).timestamp())),
                nonce="nonce_001",
                signature="bad-signature",
                body=body,
            )

        self.assertEqual(ctx.exception.status_code, 401)

    async def test_webhook_security_accepts_valid_signature(self):
        """Webhook 签名正确时应通过。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=FakeScalarResult(None))
        db.add = Mock()
        svc = WebhookSecurityService(db)
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        nonce = "nonce_valid"
        body = b'{"room_id":"room_001"}'
        signature = hmac.new(
            b"secret-a",
            b".".join([timestamp.encode("utf-8"), nonce.encode("utf-8"), body]),
            hashlib.sha256,
        ).hexdigest()

        await svc.verify(
            source_system="rocket_chat",
            secret="secret-a",
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            body=body,
        )

        db.add.assert_called_once()

    async def test_resolve_approval_route_rejects_invalid_decision(self):
        """审批路由应拒绝非法 decision。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}

        with self.assertRaises(HTTPException) as ctx:
            await resolve_approval(
                approval_id="appr_001",
                req=ApprovalResolveRequest(decision="PENDING"),
                db=db,
                tenant=tenant,
            )

        self.assertEqual(ctx.exception.status_code, 422)

    async def test_process_due_route_returns_ok_on_internal_error(self):
        """process-due 路由遇到内部异常时不应返回 500。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}

        with patch("app.api.routes_deliveries.DeliveryService") as delivery_cls:
            response = await process_due_deliveries(db=db, tenant=tenant, limit=1)

        self.assertEqual(response.data["processed_count"], 0)
        self.assertEqual(response.data["statuses"], [])
        self.assertIn("warning", response.data)

    async def test_openclaw_transcript_route_verifies_signature(self):
        """OpenClaw transcript 路由必须先验签，再处理事件。"""
        db = AsyncMock()
        request = SimpleNamespace(body=AsyncMock(return_value=b'{"tenant_id":"tenant_001"}'))
        event = SimpleNamespace(
            tenant_id="tenant_001",
            session_key="sess_001",
            event_id="evt_001",
            text="hello",
            sender_type="agent",
            sender_id="openclaw:ava",
            task_type="generic",
            metadata={},
        )

        with patch("app.api.routes_openclaw.WebhookSecurityService") as security_cls, patch(
            "app.api.routes_openclaw.OpenClawService"
        ) as svc_cls:
            security_cls.return_value.verify = AsyncMock()
            svc_cls.return_value.ingest_transcript = AsyncMock(return_value={"task_id": "task_001"})

            response = await ingest_openclaw_transcript(
                request=request,
                event=event,
                db=db,
                x_a2a_timestamp="100",
                x_a2a_nonce="nonce-1",
                x_a2a_signature="sig-1",
            )

        security_cls.return_value.verify.assert_awaited_once()
        self.assertEqual(response.data["task_id"], "task_001")

    async def test_openclaw_approval_route_verifies_signature(self):
        """OpenClaw approval 路由必须先验签，再处理审批。"""
        db = AsyncMock()
        request = SimpleNamespace(body=AsyncMock(return_value=b'{"tenant_id":"tenant_001"}'))
        approval = SimpleNamespace(
            approval_id="appr_001",
            tenant_id="tenant_001",
            task_id="task_001",
            context_id="ctx_001",
            status="PENDING",
            approver_user_id=None,
            requested_by=None,
            reason="需要审批",
            decision_note=None,
            external_key="ext-1",
            created_at=datetime.now(timezone.utc),
            resolved_at=None,
        )
        event = SimpleNamespace(
            tenant_id="tenant_001",
            task_id="task_001",
            external_key="ext-1",
            reason="需要审批",
            requested_by="user_1",
            approver_user_id="approver_1",
            metadata={},
        )

        with patch("app.api.routes_openclaw.WebhookSecurityService") as security_cls, patch(
            "app.api.routes_openclaw.OpenClawService"
        ) as svc_cls:
            security_cls.return_value.verify = AsyncMock()
            svc_cls.return_value.ingest_approval_request = AsyncMock(return_value=approval)

            response = await ingest_openclaw_approval(
                request=request,
                event=event,
                db=db,
                x_a2a_timestamp="100",
                x_a2a_nonce="nonce-1",
                x_a2a_signature="sig-1",
            )

        security_cls.return_value.verify.assert_awaited_once()
        self.assertEqual(response.data.approval_id, "appr_001")

    async def test_register_openclaw_agent_returns_gateway_token_and_urls(self):
        """注册 OpenClaw Agent 时应返回专用 token 与接入地址。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        req = OpenClawAgentRegisterRequest(
            agent_id="openclaw:ava",
            display_name="AVA",
            agent_summary="擅长技术设计评审",
            capabilities={"analysis": True},
            config_json={"workspace": "ava"},
        )
        request = SimpleNamespace(base_url="https://hub.example.com/")
        agent = SimpleNamespace(agent_id="openclaw:ava")

        with patch("app.api.routes_openclaw.AgentRegistry") as registry_cls, patch.object(
            settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"
        ):
            registry_cls.return_value.register = AsyncMock(return_value=agent)
            response = await register_openclaw_agent(req=req, request=request, db=db, tenant=tenant)

        payload = decode_access_token(response.data.auth_token)
        self.assertEqual(payload["tenant_id"], "tenant_001")
        self.assertEqual(payload["agent_id"], "openclaw:ava")
        self.assertEqual(payload["scope"], "openclaw_gateway")
        self.assertEqual(response.data.transport, "mqtt")
        self.assertEqual(response.data.agent_summary, "擅长技术设计评审")
        self.assertTrue(response.data.mqtt_command_topic.endswith("/tenant_001/agents/openclaw:ava/commands"))
        self.assertTrue(response.data.presence_url.endswith("/v1/agent-link/presence"))
        self.assertEqual(response.data.mqtt_username, tenant_mqtt_username("tenant_001"))
        self.assertEqual(response.data.mqtt_password, tenant_mqtt_password("tenant_001"))
        self.assertEqual(response.data.ws_url, "wss://hub.example.com/ws/openclaw/gateway")
        self.assertTrue(response.data.onboarding_url.endswith("/openclaw/agents/connect"))

    async def test_openclaw_onboarding_info_returns_expected_urls(self):
        """接入信息接口应返回 WS、注册和 webhook 地址。"""
        request = SimpleNamespace(base_url="http://localhost:8000/")

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "http://localhost:8000"):
            response = await get_openclaw_onboarding_info(request=request)

        self.assertEqual(response.data.ws_url, "ws://localhost:8000/ws/openclaw/gateway")
        self.assertTrue(response.data.register_url.endswith("/v1/openclaw/agents/register"))
        self.assertEqual(response.data.transport, "mqtt")
        self.assertEqual(response.data.mqtt_broker_url, settings.MQTT_BROKER_URL)
        self.assertIn("task.update", response.data.message_types)

    async def test_agent_link_presence_logs_missing_bearer(self):
        """presence 缺少 Bearer token 时应记录错误事件。"""
        request = SimpleNamespace(headers={}, url=SimpleNamespace(path="/v1/agent-link/presence"))

        with patch("app.api._shared.ErrorEventService.record_out_of_band", new=AsyncMock()) as record_mock:
            with self.assertRaises(HTTPException) as ctx:
                await agent_link_presence(req=AgentLinkPresenceRequest(), request=request)

        self.assertEqual(ctx.exception.status_code, 401)
        record_mock.assert_awaited_once()
        self.assertEqual(record_mock.await_args.kwargs["stage"], "presence")
        self.assertEqual(record_mock.await_args.kwargs["summary"], "缺少 Bearer token")

    async def test_agent_link_report_error_uses_agent_identity(self):
        """agent 错误上报应记录为 agent 侧事件。"""
        token = create_access_token(
            "openclaw:mia",
            extra={"tenant_id": "owner_001", "agent_id": "openclaw:mia", "scope": "openclaw_gateway"},
        )
        request = SimpleNamespace(
            headers={"authorization": f"Bearer {token}"},
            url=SimpleNamespace(path="/v1/agent-link/errors"),
        )

        with patch("app.api._shared.ErrorEventService.record_out_of_band", new=AsyncMock()) as record_mock:
            response = await agent_link_report_error(
                req=AgentLinkErrorReportRequest(
                    stage="task_update",
                    summary="任务回传失败",
                    category="runtime",
                    detail="HTTP 500",
                    metadata={"task_id": "task_001"},
                ),
                request=request,
            )

        self.assertTrue(response.data["recorded"])
        record_mock.assert_awaited_once()
        self.assertEqual(record_mock.await_args.kwargs["source_side"], "agent")
        self.assertEqual(record_mock.await_args.kwargs["agent_id"], "openclaw:mia")

    async def test_docs_swagger_includes_error_records_link(self):
        """docs 顶部应提供错误记录入口。"""
        response = await custom_swagger_docs()
        html = response.body.decode("utf-8")

        self.assertIn('id="agent-error-link"', html)
        self.assertIn("/docs/errors", html)
        self.assertIn("错误记录过滤", html)

    async def test_openclaw_connect_link_returns_shareable_url(self):
        """应能为未预注册 Agent 生成可直接转发的单入口 connect_url。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        request = SimpleNamespace(base_url="https://hub.example.com/")

        with patch("app.api.routes_openclaw.AgentRegistry") as registry_cls, patch.object(
            settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"
        ):
            registry_cls.return_value.get = AsyncMock(return_value=None)
            response = await create_openclaw_connect_link(
                agent_id="openclaw:ava",
                req=OpenClawConnectLinkRequest(
                    display_name="AVA",
                    capabilities={"analysis": True},
                    config_json={"workspace": "ava"},
                ),
                request=request,
                db=db,
                tenant=tenant,
            )

        payload = decode_access_token(response.data.connect_url.split("token=", 1)[1])
        self.assertEqual(payload["scope"], "openclaw_bootstrap")
        self.assertEqual(payload["display_name"], "AVA")
        self.assertEqual(payload["capabilities"], {"analysis": True})
        self.assertEqual(payload["config_json"], {"workspace": "ava"})
        self.assertIn("/openclaw/agents/connect?token=", response.data.connect_url)
        self.assertIn("/v1/openclaw/agents/bootstrap?token=", response.data.bootstrap_url)
        self.assertEqual(response.data.agent_id, "openclaw:ava")

    async def test_openclaw_bootstrap_auto_registers_agent_and_returns_auth_token(self):
        """bootstrap token 应自动注册 Agent 并换取 WS 启动配置。"""
        request = SimpleNamespace(base_url="https://hub.example.com/")
        bootstrap_token = create_access_token(
            "user_1",
            {
                "tenant_id": "tenant_001",
                "agent_id": "openclaw:ava",
                "scope": "openclaw_bootstrap",
                "display_name": "AVA",
                "capabilities": {"analysis": True},
                "config_json": {"workspace": "ava"},
            },
        )

        with patch("app.api.routes_openclaw.AgentRegistry") as registry_cls, patch.object(
            settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"
        ):
            registry_cls.return_value.register = AsyncMock()
            response = await get_openclaw_bootstrap(token=bootstrap_token, request=request)

        registry_cls.return_value.register.assert_awaited_once_with(
            agent_id="openclaw:ava",
            tenant_id="tenant_001",
            agent_type="federated",
            display_name="AVA",
            capabilities={"analysis": True},
            auth_scheme="jwt",
            config_json={"adapter": "openclaw_gateway", "workspace": "ava"},
            actor_id="user_1",
        )
        payload = decode_access_token(response.data.auth_token)
        self.assertEqual(payload["scope"], "openclaw_gateway")
        self.assertEqual(payload["agent_id"], "openclaw:ava")
        self.assertEqual(response.data.transport, "mqtt")
        self.assertTrue(response.data.mqtt_client_id.startswith("a2a_tenant_001_"))
        self.assertEqual(response.data.mqtt_username, tenant_mqtt_username("tenant_001"))
        self.assertEqual(response.data.mqtt_password, tenant_mqtt_password("tenant_001"))
        self.assertEqual(response.data.ws_url, "wss://hub.example.com/ws/openclaw/gateway")

    async def test_agent_link_presence_accepts_agent_token(self):
        """Agent Link 心跳接口应接受 agent token 并更新在线状态。"""
        token = create_access_token(
            "openclaw:ava",
            {"tenant_id": "tenant_001", "agent_id": "openclaw:ava", "scope": "openclaw_gateway"},
        )
        request = SimpleNamespace(headers={"authorization": f"Bearer {token}"})

        with patch("app.api.routes_agent_link.agent_link_service") as service:
            service.heartbeat = AsyncMock(return_value={"agent_id": "openclaw:ava", "status": "online", "pending_count": 0})
            response = await agent_link_presence(
                req=AgentLinkPresenceRequest(status="online", metadata={"version": "1.0"}),
                request=request,
            )

        self.assertEqual(response.data["agent_id"], "openclaw:ava")
        service.heartbeat.assert_awaited_once_with(
            "tenant_001",
            "openclaw:ava",
            "online",
            {"version": "1.0"},
            auth_token=token,
        )

    async def test_agent_link_self_register_reuses_existing_global_agent_id(self):
        """当前 agents 表按 agent_id 全局唯一，自注册应复用已有 agent 避免 500。"""
        db = AsyncMock()
        db.get = AsyncMock(return_value=SimpleNamespace(tenant_id="owner_old", name="Old Owner"))
        db.execute = AsyncMock(side_effect=[
            FakeScalarResult(SimpleNamespace(agent_id="openclaw:mia", tenant_id="owner_old")),
        ])
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        request = SimpleNamespace(base_url="https://hub.example.com/")
        req = AgentLinkSelfRegisterRequest(
            agent_id="openclaw:mia",
            display_name="MIA",
            capabilities={"generic": True},
            config_json={"local_agent_id": "mia"},
            owner_profile={"source": "debug", "raw_text": "changed owner profile"},
        )

        with patch("app.api.routes_agent_link.AsyncSessionLocal") as session_cls, patch(
            "app.api.routes_agent_link.AgentRegistry"
        ) as registry_cls, patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=db)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            registry_cls.return_value.register = AsyncMock(
                return_value=SimpleNamespace(agent_id="openclaw:mia", tenant_id="owner_old")
            )

            response = await agent_link_self_register(req=req, request=request)

        registry_cls.return_value.register.assert_awaited_once()
        kwargs = registry_cls.return_value.register.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "owner_old")
        self.assertEqual(kwargs["agent_id"], "openclaw:mia")
        self.assertEqual(kwargs["config_json"]["owner_profile"]["tenant_resolution"], "existing_agent_id")
        self.assertNotEqual(kwargs["config_json"]["owner_profile"]["requested_owner_tenant_id"], "owner_old")
        payload = decode_access_token(response.data.auth_token)
        self.assertEqual(payload["tenant_id"], "owner_old")
        self.assertEqual(response.data.tenant_id, "owner_old")

    async def test_agent_link_self_register_persists_agent_summary_and_syncs_mosquitto_auth(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        db.add = Mock()
        db.flush = AsyncMock()
        db.execute = AsyncMock(side_effect=[FakeScalarResult(None)])
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        request = SimpleNamespace(base_url="https://hub.example.com/")
        req = AgentLinkSelfRegisterRequest(
            agent_id="ava",
            display_name="AVA",
            agent_summary="擅长技术排障和多轮协作",
            capabilities={"generic": True},
            config_json={"local_agent_id": "ava"},
            owner_profile={"source": "debug", "raw_text": "owner profile"},
        )

        with patch("app.api.routes_agent_link.AsyncSessionLocal") as session_cls, patch(
            "app.api.routes_agent_link.AgentRegistry"
        ) as registry_cls, patch(
            "app.api.routes_agent_link._sync_owner_tenant_mosquitto_auth", new_callable=AsyncMock
        ) as sync_mock, patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=db)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=None)
            registry_cls.return_value.register = AsyncMock(
                return_value=SimpleNamespace(agent_id="openclaw:ava", tenant_id="owner_x")
            )

            response = await agent_link_self_register(req=req, request=request)

        kwargs = registry_cls.return_value.register.await_args.kwargs
        self.assertEqual(kwargs["agent_id"], "openclaw:ava")
        self.assertEqual(kwargs["config_json"]["agent_summary"], "擅长技术排障和多轮协作")
        sync_mock.assert_awaited_once_with(db)
        self.assertEqual(response.data.agent_summary, "擅长技术排障和多轮协作")

    async def test_agent_link_service_queues_when_publish_fails(self):
        """MQTT publish 失败时应回退到 pending 队列。"""
        publisher = SimpleNamespace(
            publish=AsyncMock(return_value=False),
            last_error=Mock(return_value="publish_failed"),
        )
        service = AgentLinkService(publisher=publisher)
        task = SimpleNamespace(
            task_id="task_001",
            tenant_id="tenant_001",
            target_agent_id="openclaw:ava",
            context_id="ctx_001",
            task_type="analysis",
            input_text="请分析",
            metadata_json={},
            trace_id="trace_001",
        )

        result = await service.dispatch_task(task, "agent-token-001")

        self.assertFalse(result.dispatched)
        self.assertEqual(result.reason, "publish_failed")
        self.assertEqual(len(service._pending[("tenant_001", "openclaw:ava")]), 1)

    async def test_agent_link_service_heartbeat_flushes_pending(self):
        """心跳上报时应尝试补发 pending 消息。"""
        publisher = SimpleNamespace(
            publish=AsyncMock(return_value=True),
            last_error=Mock(return_value=None),
        )
        service = AgentLinkService(publisher=publisher)
        service._pending[("tenant_001", "openclaw:ava")] = [{"type": "task.dispatch", "task_id": "task_001"}]

        state = await service.heartbeat(
            "tenant_001",
            "openclaw:ava",
            "online",
            {"source": "test"},
            auth_token="agent-token-001",
        )

        self.assertEqual(state["pending_count"], 0)
        publisher.publish.assert_awaited_once()

    async def test_openclaw_gateway_task_ack_moves_routing_to_working(self):
        """Agent 显式 task.ack 后任务应从 ROUTING 进入 WORKING。"""
        connection = OpenClawConnection(
            connection_id="ocws_001",
            tenant_id="tenant_001",
            agent_id="openclaw:ava",
            websocket=None,
            metadata={},
        )
        db = AsyncMock()

        with patch("app.services.openclaw_gateway_service.TaskService") as task_cls:
            task_cls.return_value.get = AsyncMock(
                return_value=SimpleNamespace(task_id="task_001", state="ROUTING")
            )
            task_cls.return_value.update_state = AsyncMock(
                return_value=SimpleNamespace(task_id="task_001", state="WORKING")
            )
            response = await openclaw_gateway_broker.handle_agent_message(
                db,
                connection,
                {"type": "task.ack", "task_id": "task_001"},
            )

        self.assertEqual(response["type"], "task.ack.ack")
        self.assertEqual(response["state"], "WORKING")

    async def test_openclaw_connect_markdown_renders_bootstrap_and_mqtt_urls(self):
        """Markdown 接入文档应返回可直接读取的 bootstrap 和 WS 地址。"""
        request = SimpleNamespace(
            base_url="https://hub.example.com/",
            query_params={"token": "bootstrap-token-001"},
            url="https://hub.example.com/openclaw/agents/connect.md?token=bootstrap-token-001",
        )

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            response = await openclaw_connect_markdown(request=request)

        self.assertIn("OpenClaw Agent Link 兼容接入指令", response.body.decode("utf-8"))
        self.assertIn("/v1/openclaw/agents/bootstrap?token=bootstrap-token-001", response.body.decode("utf-8"))
        self.assertIn("wss://hub.example.com/ws/openclaw/gateway", response.body.decode("utf-8"))

    async def test_agent_link_connect_page_is_agent_only_runbook(self):
        """公开接入页应是面向 agent 的可执行 Runbook，不再返回人类营销页。"""
        request = SimpleNamespace(
            base_url="https://hub.example.com/",
            query_params={},
            url="https://hub.example.com/agent-link/connect",
        )

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            response = await openclaw_connect_page(request=request)

        body = response.body.decode("utf-8")
        self.assertIn("# Agent Link 接入指令", body)
        self.assertIn("/agent-link/plugins/dbim-mqtt.tar.gz", body)
        self.assertIn("/agent-link/prompt", body)
        self.assertIn("/agent-link/install/openclaw-dbim-mqtt.sh", body)
        self.assertIn("AGENT_ID=<local-agent-id>", body)
        self.assertIn("不要猜测", body)
        self.assertIn("重启后继续执行", body)
        self.assertIn("Agent Link 安装完成", body)
        self.assertIn("不要向主人索要 tenant_id", body)
        self.assertNotIn("<html", body.lower())

    async def test_agent_link_prompt_is_copyable_agent_task(self):
        """公开 prompt 应是可直接复制给 agent 的完整任务指令。"""
        request = SimpleNamespace(base_url="https://hub.example.com/", query_params={})

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            response = await agent_link_prompt(request=request)

        body = response.body.decode("utf-8")
        self.assertIn("请把当前 OpenClaw agent 接入 A2A Hub", body)
        self.assertIn("不是普通网页阅读任务", body)
        self.assertIn("AGENT_ID=<local-agent-id>", body)
        self.assertIn("当前会话可能短暂中断或显示 SIGTERM", body)
        self.assertIn("openclaw gateway run --force", body)
        self.assertIn(".agent-link/install-result.json", body)
        self.assertIn("无法访问宿主机", body)
        self.assertIn("不要泄露 auth_token", body)
        self.assertIn("https://hub.example.com/agent-link/connect", body)

    async def test_agent_link_friend_tools_is_public_agent_runbook(self):
        request = SimpleNamespace(base_url="https://hub.example.com/", query_params={})

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            response = await agent_link_friend_tools(request=request)

        body = response.body.decode("utf-8")
        self.assertIn("# A2A Hub Agent Link 好友操作说明", body)
        self.assertIn("agent-linkctl accept '<invite-url-or-token>'", body)
        self.assertIn("默认不改 `TOOLS.md`", body)
        self.assertIn("writeWorkspaceTools=true", body)
        self.assertIn("friend_tools_url=https://hub.example.com/agent-link/friend-tools", body)

    def test_agent_link_friend_tools_route_supports_head(self):
        route_methods = {}
        for route in agent_link_router.routes:
            path = getattr(route, "path", None)
            if path in {"/agent-link/friend-tools", "/agent-link/friend-tools.md"}:
                route_methods.setdefault(path, set()).update(route.methods or set())
        self.assertIn("HEAD", route_methods["/agent-link/friend-tools"])
        self.assertIn("HEAD", route_methods["/agent-link/friend-tools.md"])

    async def test_agent_link_install_report_records_public_result(self):
        request = SimpleNamespace(
            url=SimpleNamespace(path="/v1/agent-link/install-report"),
            headers={},
            method="POST",
            client=SimpleNamespace(host="127.0.0.1"),
        )
        req = AgentLinkInstallReportRequest(
            agent_id="ava",
            status="success",
            stage="install_online",
            summary="Agent Link 安装完成，插件已在线",
            owner_profile={"name": "Ava Owner"},
            metadata={"local_agent_id": "ava"},
        )

        with patch("app.api._shared.ErrorEventService.record_out_of_band", new=AsyncMock()) as record_mock:
            response = await agent_link_install_report(req=req, request=request)

        self.assertTrue(response.data["recorded"])
        self.assertEqual(response.data["agent_id"], "openclaw:ava")
        self.assertEqual(response.data["status"], "success")
        record_mock.assert_awaited_once()
        payload = record_mock.await_args.kwargs["payload"]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["local_agent_id"], "ava")

    async def test_agent_link_manifest_includes_plugin_install_urls(self):
        """公开 manifest 应明确告诉 agent 插件下载和安装脚本地址。"""
        request = SimpleNamespace(base_url="https://hub.example.com/")

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            response = await get_agent_link_manifest(request=request)

        self.assertEqual(response.data.plugin_download_url, "https://hub.example.com/agent-link/plugins/dbim-mqtt.tar.gz")
        self.assertEqual(
            response.data.openclaw_install_script_url,
            "https://hub.example.com/agent-link/install/openclaw-dbim-mqtt.sh",
        )
        self.assertEqual(response.data.agent_prompt_url, "https://hub.example.com/agent-link/prompt")
        self.assertEqual(response.data.friend_tools_url, "https://hub.example.com/agent-link/friend-tools")
        self.assertEqual(response.data.required_plugin, "dbim-mqtt")

    async def test_openclaw_install_script_configures_channel_plugin(self):
        """安装脚本应写入 channels.dbim_mqtt，而不是只生成旧 connect_url 文件。"""
        request = SimpleNamespace(
            base_url="https://hub.example.com/",
            query_params={},
            url="https://hub.example.com/agent-link/install/openclaw-dbim-mqtt.sh",
        )

        with patch.object(settings, "A2A_HUB_PUBLIC_BASE_URL", "https://hub.example.com"):
            response = await openclaw_dbim_mqtt_install_script(request=request)

        body = response.body.decode("utf-8")
        self.assertIn("channels.dbim_mqtt", body)
        self.assertIn("instances", body)
        self.assertIn("plugins.allow", body)
        self.assertIn("无法自动推断 AGENT_ID", body)
        self.assertIn("已自动推断 AGENT_ID=", body)
        self.assertIn("已备份已有 dbim-mqtt 插件目录", body)
        self.assertIn("npm install --omit=dev", body)
        self.assertIn('chmod -R u=rwX,go=rX "$PLUGIN_DIR"', body)
        self.assertIn("异步重启", body)
        self.assertIn('RESTART_MODE="manual"', body)
        self.assertIn('nohup "$OPENCLAW_COMMAND" gateway run --force', body)
        self.assertIn('write_install_result running gateway_restart_manual', body)
        self.assertIn('max_wait_seconds="${INSTALL_MAX_WAIT_SECONDS:-240}"', body)
        self.assertIn('poll_interval="${INSTALL_POLL_INTERVAL:-3}"', body)
        self.assertIn('write_result running install_waiting', body)
        self.assertIn('report_result running install_waiting', body)
        self.assertIn("Gateway 已启动，Agent Link 仍在继续初始化，请稍后重新检查结果文件", body)
        self.assertIn("install-result.json", body)
        self.assertIn("/v1/agent-link/install-report", body)
        self.assertIn('JSON.stringify(cfg, null, 2) + "\\n"', body)
        self.assertNotIn('JSON.stringify(cfg, null, 2) + "\\\\n"', body)
        self.assertIn('const instanceDir = path.join(channelDir, shortAgentId);', body)
        self.assertIn('stateFile: path.join(instanceDir, "state.json")', body)
        self.assertNotIn('connectUrlFile: path.join(instanceDir, "connect-url.txt")', body)
        self.assertNotIn('fs.writeFileSync(path.join(instanceDir, "connect-url.txt")', body)
        self.assertIn('delete nextInstance.connectUrlFile;', body)
        self.assertIn('cfg.agents.list = Array.isArray(cfg.agents.list) ? cfg.agents.list : [];', body)
        self.assertIn('if (!cfg.agents.list.some((item) => item && item.id === shortAgentId)) {', body)
        self.assertIn("function parseAgentHint(text)", body)
        self.assertIn("scanWorkspaceFile(scores, path.join(workspaceRoot, entry.name, \"SOUL.md\"), 5);", body)
        self.assertIn('const sessionFile = path.join(process.env.HOME || "", ".openclaw", "agents", shortAgentId, "sessions", "sessions.json");', body)
        self.assertIn('if (bound && typeof bound.sessionId === "string" && bound.sessionId.includes(":")) {', body)
        self.assertIn('INSTANCE_DIR="$INSTANCE_DIR" \\', body)
        self.assertIn("nohup env", body)

    async def test_dbim_mqtt_plugin_package_can_be_downloaded(self):
        """插件包下载接口应返回可解压的 dbim-mqtt 插件源码。"""
        response = await download_dbim_mqtt_plugin()
        self.assertEqual(response.media_type, "application/gzip")

        with tarfile.open(fileobj=io.BytesIO(response.body), mode="r:gz") as tar:
            names = set(tar.getnames())
            manifest = tar.extractfile("openclaw.plugin.json").read().decode("utf-8")

        self.assertIn("package.json", names)
        self.assertIn("index.js", names)
        self.assertIn("lib/channel.js", names)
        self.assertNotIn("node_modules", names)
        self.assertIn('"channels": ["dbim_mqtt"]', manifest)
        self.assertIn('"channelConfigs"', manifest)

    async def test_openclaw_gateway_handles_task_update_message(self):
        """Agent 通过长连接回传 task.update 时应更新任务并回 ACK。"""
        db = AsyncMock()
        connection = OpenClawConnection(
            connection_id="http_openclaw:ava",
            tenant_id="tenant_001",
            agent_id="openclaw:ava",
        )
        task = SimpleNamespace(task_id="task_001", context_id="ctx_001", state="COMPLETED")

        with patch("app.services.openclaw_gateway_service.TaskService") as task_cls:
            task_cls.return_value.get = AsyncMock(
                return_value=SimpleNamespace(task_id="task_001", context_id="ctx_001", state="ROUTING")
            )
            task_cls.return_value.update_state = AsyncMock(return_value=task)
            task_cls.return_value.append_message = AsyncMock()
            response = await openclaw_gateway_broker.handle_agent_message(
                db=db,
                connection=connection,
                payload={
                    "type": "task.update",
                    "task_id": "task_001",
                    "state": "COMPLETED",
                    "output_text": "done",
                    "message_text": "分析完成",
                    "message_id": "oc-msg-001",
                },
            )

        self.assertEqual(response["type"], "task.update.ack")
        self.assertEqual(task_cls.return_value.update_state.await_count, 2)
        task_cls.return_value.append_message.assert_awaited_once()

    async def test_subscribe_task_returns_404_for_cross_tenant_access(self):
        """SSE 订阅必须校验任务归属，跨租户访问返回 404。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_002", "sub": "user_2"}

        with patch("app.api.routes_events.TaskService") as task_cls:
            task_cls.return_value.get = AsyncMock(return_value=None)
            with self.assertRaises(HTTPException) as ctx:
                await subscribe_task(task_id="task_001", db=db, tenant=tenant)

        task_cls.return_value.get.assert_awaited_once_with("task_001", "tenant_002")
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_replay_delivery_route_passes_tenant_scope(self):
        """DLQ 重放必须在服务层按租户过滤。"""
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_001", "sub": "user_1"}
        delivery = SimpleNamespace(
            delivery_id=uuid4(),
            tenant_id="tenant_001",
            task_id="task_001",
            target_channel="rocket_chat",
            target_ref={},
            payload={},
            status="PENDING",
            attempt_count=0,
            max_attempts=3,
            next_retry_at=None,
            last_error=None,
            dead_letter_reason=None,
        )

        with patch("app.api.routes_deliveries.DeliveryService") as delivery_cls:
            delivery_cls.return_value.replay_dead = AsyncMock(return_value=delivery)
            response = await replay_delivery(
                delivery_id=str(delivery.delivery_id),
                db=db,
                tenant=tenant,
            )

        delivery_cls.return_value.replay_dead.assert_awaited_once_with(str(delivery.delivery_id), "tenant_001")
        self.assertEqual(response.data.delivery_id, str(delivery.delivery_id))

    async def test_task_event_broker_publish_and_subscribe(self):
        """任务事件总线应能发布并接收事件。"""
        queue = task_event_broker.subscribe("task_stream_001")
        try:
            await task_event_broker.publish("task_stream_001", {"event": "task.state_changed", "state": "WORKING"})
            event = await queue.get()
        finally:
            task_event_broker.unsubscribe("task_stream_001", queue)

        self.assertEqual(event["event"], "task.state_changed")
        self.assertEqual(event["state"], "WORKING")
