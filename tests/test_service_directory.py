import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.core.db import Base
from app.api.routes_services import (
    create_service_publication,
    create_service_thread,
    create_service_thread_message,
    get_service,
    list_services,
)
from app.schemas.service import (
    ServicePublicationCreateRequest,
    ServiceThreadCreateRequest,
    ServiceThreadMessageCreateRequest,
)
from app.services.service_conversation_service import ServiceConversationService, ServiceThreadForbidden
from app.services.service_directory_service import ServiceDirectoryService


def _publication(**kwargs):
    now = datetime.now(timezone.utc)
    data = {
        "service_id": "svc_design",
        "tenant_id": "tenant_provider",
        "handler_agent_id": "openclaw:mia",
        "title": "Design Review",
        "summary": "review service",
        "visibility": "listed",
        "contact_policy": "auto_accept",
        "allow_agent_initiated_chat": True,
        "status": "ACTIVE",
        "tags": ["review"],
        "capabilities_public": {"analysis": True},
        "metadata_json": {},
        "created_at": now,
        "updated_at": now,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


def _thread(**kwargs):
    now = datetime.now(timezone.utc)
    data = {
        "thread_id": "sth_001",
        "service_id": "svc_design",
        "consumer_tenant_id": "tenant_consumer",
        "provider_tenant_id": "tenant_provider",
        "provider_context_id": "ctx_provider_001",
        "initiator_agent_id": "openclaw:ava",
        "handler_agent_id": "openclaw:mia",
        "status": "OPEN",
        "title": "Design Review",
        "metadata_json": {},
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
    }
    data.update(kwargs)
    return SimpleNamespace(**data)


class ServiceDirectoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_includes_service_tables(self):
        self.assertIn("service_publications", Base.metadata.tables)
        self.assertIn("service_threads", Base.metadata.tables)
        self.assertIn("service_thread_messages", Base.metadata.tables)

    async def test_create_service_publication_route_returns_created_service(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_provider", "sub": "user_1"}
        publication = _publication()
        with patch("app.api.routes_services.ServiceDirectoryService") as svc_cls:
            svc_cls.return_value.create = AsyncMock(return_value=publication)
            response = await create_service_publication(
                req=ServicePublicationCreateRequest(
                    handler_agent_id="openclaw:mia",
                    title="Design Review",
                    summary="review service",
                ),
                db=db,
                tenant=tenant,
            )
        self.assertEqual(response.data.service_id, "svc_design")
        self.assertEqual(response.data.handler_agent_id, "openclaw:mia")

    async def test_list_services_route_returns_accessible_publications(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_consumer", "sub": "user_2"}
        with patch("app.api.routes_services.ServiceDirectoryService") as svc_cls:
            svc_cls.return_value.list_accessible = AsyncMock(
                return_value=[
                    _publication(),
                    _publication(service_id="svc_ops", title="Ops", handler_agent_id="openclaw:ops"),
                ]
            )
            response = await list_services(db=db, tenant=tenant)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0].service_id, "svc_design")

    async def test_get_service_route_returns_404_for_hidden_service(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_consumer", "sub": "user_2"}
        with patch("app.api.routes_services.ServiceDirectoryService") as svc_cls:
            svc_cls.return_value.get_accessible = AsyncMock(return_value=None)
            with self.assertRaises(HTTPException) as ctx:
                await get_service("svc_private", db=db, tenant=tenant)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_create_service_thread_route_with_opening_message_returns_task(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_consumer", "sub": "user_2", "agent_id": "openclaw:ava"}
        publication = _publication()
        thread = _thread()
        with patch("app.api.routes_services.ServiceDirectoryService") as dir_cls, patch(
            "app.api.routes_services.ServiceConversationService"
        ) as conv_cls:
            dir_cls.return_value.get_accessible = AsyncMock(return_value=publication)
            conv_cls.return_value.create_thread = AsyncMock(return_value=thread)
            conv_cls.return_value.send_consumer_message = AsyncMock(
                return_value=(SimpleNamespace(message_id="stmsg_001"), "task_001")
            )
            response = await create_service_thread(
                "svc_design",
                req=ServiceThreadCreateRequest(opening_message="请评审这个方案"),
                db=db,
                tenant=tenant,
            )
        self.assertEqual(response.data.thread.thread_id, "sth_001")
        self.assertEqual(response.data.task_id, "task_001")

    async def test_create_service_thread_message_route_rejects_non_consumer(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_provider", "sub": "user_provider"}
        thread = _thread()
        with patch("app.api.routes_services.ServiceConversationService") as conv_cls:
            conv_cls.return_value.get_thread = AsyncMock(return_value=thread)
            conv_cls.return_value.send_consumer_message = AsyncMock(side_effect=ServiceThreadForbidden("forbidden"))
            with self.assertRaises(HTTPException) as ctx:
                await create_service_thread_message(
                    thread_id="sth_001",
                    req=ServiceThreadMessageCreateRequest(text="继续"),
                    db=db,
                    tenant=tenant,
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_service_directory_list_accessible_filters_by_visibility(self):
        db = AsyncMock()
        svc = ServiceDirectoryService(db)
        db.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(
                    all=lambda: [
                        _publication(service_id="svc_listed", tenant_id="tenant_provider", visibility="listed"),
                        _publication(service_id="svc_private", tenant_id="tenant_provider", visibility="private"),
                        _publication(service_id="svc_own", tenant_id="tenant_consumer", visibility="direct_link"),
                    ]
                )
            )
        )
        items = await svc.list_accessible("tenant_consumer")
        self.assertEqual([item.service_id for item in items], ["svc_listed", "svc_own"])

    async def test_service_conversation_send_message_uses_provider_tenant(self):
        db = AsyncMock()
        svc = ServiceConversationService(db)
        thread = _thread()
        tenant = {"tenant_id": "tenant_consumer", "sub": "user_2", "agent_id": "openclaw:ava"}
        user_message = SimpleNamespace(message_id="stmsg_001", metadata_json={}, linked_task_id=None)
        svc._append_thread_message = AsyncMock(return_value=user_message)
        svc._touch_thread = AsyncMock()
        db.execute = AsyncMock()
        with patch("app.services.service_conversation_service.create_and_dispatch_message_task", new=AsyncMock()) as send_mock:
            send_mock.return_value = SimpleNamespace(task_id="task_001")
            message, task_id = await svc.send_consumer_message(
                thread=thread,
                tenant=tenant,
                text="请评审方案",
                initiator_agent_id="openclaw:ava",
                metadata={"source": "test"},
            )
        self.assertEqual(message.message_id, "stmsg_001")
        self.assertEqual(task_id, "task_001")
        self.assertEqual(send_mock.await_args.kwargs["tenant"]["tenant_id"], "tenant_provider")
        self.assertEqual(send_mock.await_args.kwargs["req"].target_agent_id, "openclaw:mia")


if __name__ == "__main__":
    unittest.main()
