import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.api.routes_agent_link import accept_agent_invite, agent_link_send_message
from app.api.routes_agent_friends import create_friend_request, list_agent_friends, update_friend
from app.services.friend_service import FriendConflictError
from app.schemas.friend import FriendCreateRequest, FriendUpdateRequest
from app.schemas.integration import AgentLinkSendMessageRequest
from app.schemas.message import MessagePart


class AsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class AgentFriendsTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_friend_request_returns_viewer_context_payload(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_alice", "sub": "user_alice"}
        friend = SimpleNamespace(
            id=1,
            tenant_id="tenant_alice",
            requester_tenant_id="tenant_alice",
            target_tenant_id="tenant_bob",
            requester_agent_id="openclaw:alice",
            target_agent_id="openclaw:bob",
            status="PENDING",
            requester_context_id=None,
            target_context_id=None,
            message="hi",
        )
        payload = {
            "id": 1,
            "tenant_id": "tenant_alice",
            "requester_tenant_id": "tenant_alice",
            "target_tenant_id": "tenant_bob",
            "requester_agent_id": "openclaw:alice",
            "target_agent_id": "openclaw:bob",
            "status": "PENDING",
            "context_id": None,
            "peer_agent_id": "openclaw:bob",
            "can_send_message": False,
            "message": "hi",
        }
        with patch("app.api.routes_agent_friends.FriendService") as svc_cls:
            svc = svc_cls.return_value
            svc.create_request = AsyncMock(return_value=friend)
            svc.view_payload = Mock(return_value=payload)
            response = await create_friend_request(
                "openclaw:alice",
                FriendCreateRequest(target_agent_id="openclaw:bob", message="hi"),
                db,
                tenant,
            )
        self.assertEqual(response.data.peer_agent_id, "openclaw:bob")
        db.commit.assert_awaited_once()

    async def test_update_friend_accept_uses_current_agent_view(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_bob", "sub": "user_bob"}
        friend = SimpleNamespace(
            id=1,
            tenant_id="tenant_alice",
            requester_tenant_id="tenant_alice",
            target_tenant_id="tenant_bob",
            requester_agent_id="openclaw:alice",
            target_agent_id="openclaw:bob",
            status="ACCEPTED",
            requester_context_id="ctx_alice",
            target_context_id="ctx_bob",
            message="hi",
        )
        payload = {
            "id": 1,
            "tenant_id": "tenant_bob",
            "requester_tenant_id": "tenant_alice",
            "target_tenant_id": "tenant_bob",
            "requester_agent_id": "openclaw:alice",
            "target_agent_id": "openclaw:bob",
            "status": "ACCEPTED",
            "context_id": "ctx_bob",
            "peer_agent_id": "openclaw:alice",
            "can_send_message": True,
            "message": "hi",
        }
        with patch("app.api.routes_agent_friends.FriendService") as svc_cls:
            svc = svc_cls.return_value
            svc.assert_agent_owned = AsyncMock()
            svc.update_status = AsyncMock(return_value=friend)
            svc.view_payload = Mock(return_value=payload)
            response = await update_friend(
                "openclaw:bob",
                1,
                FriendUpdateRequest(status="accepted"),
                db,
                tenant,
            )
        self.assertEqual(response.data.context_id, "ctx_bob")
        db.commit.assert_awaited_once()

    async def test_update_friend_rejects_invalid_status(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_bob", "sub": "user_bob"}
        with patch("app.api.routes_agent_friends.FriendService") as svc_cls:
            svc = svc_cls.return_value
            svc.assert_agent_owned = AsyncMock()
            svc.update_status = AsyncMock(side_effect=FriendConflictError("好友请求状态只能是 accepted、rejected 或 blocked"))
            with self.assertRaises(Exception) as ctx:
                await update_friend(
                    "openclaw:bob",
                    1,
                    FriendUpdateRequest(status="archived"),
                    db,
                    tenant,
                )
        self.assertEqual(getattr(ctx.exception, "status_code", None), 409)
        db.commit.assert_not_awaited()

    async def test_list_agent_friends_filters_by_viewer_agent(self):
        db = AsyncMock()
        tenant = {"tenant_id": "tenant_bob", "sub": "user_bob"}
        friend = SimpleNamespace(
            id=1,
            tenant_id="tenant_alice",
            requester_tenant_id="tenant_alice",
            target_tenant_id="tenant_bob",
            requester_agent_id="openclaw:alice",
            target_agent_id="openclaw:bob",
            status="ACCEPTED",
            requester_context_id="ctx_alice",
            target_context_id="ctx_bob",
            message="hi",
        )
        payload = {
            "id": 1,
            "tenant_id": "tenant_bob",
            "requester_tenant_id": "tenant_alice",
            "target_tenant_id": "tenant_bob",
            "requester_agent_id": "openclaw:alice",
            "target_agent_id": "openclaw:bob",
            "status": "ACCEPTED",
            "context_id": "ctx_bob",
            "peer_agent_id": "openclaw:alice",
            "can_send_message": True,
            "message": "hi",
        }
        with patch("app.api.routes_agent_friends.FriendService") as svc_cls:
            svc = svc_cls.return_value
            svc.list_for_agent = AsyncMock(return_value=[friend])
            svc.view_payload = Mock(return_value=payload)
            response = await list_agent_friends("openclaw:bob", db, tenant)
        self.assertEqual(response.data[0].peer_agent_id, "openclaw:alice")

    async def test_accept_agent_invite_uses_current_agent_tenant_for_acceptance(self):
        db = AsyncMock()
        request = SimpleNamespace()
        friend = SimpleNamespace(
            id=1,
            tenant_id="tenant_alice",
            requester_tenant_id="tenant_alice",
            target_tenant_id="tenant_bob",
            requester_agent_id="openclaw:alice",
            target_agent_id="openclaw:bob",
            status="ACCEPTED",
            requester_context_id="ctx_alice",
            target_context_id="ctx_bob",
            message="accepted via invite",
        )
        payload = {
            "id": 1,
            "tenant_id": "tenant_bob",
            "requester_tenant_id": "tenant_alice",
            "target_tenant_id": "tenant_bob",
            "requester_agent_id": "openclaw:alice",
            "target_agent_id": "openclaw:bob",
            "status": "ACCEPTED",
            "context_id": "ctx_bob",
            "peer_agent_id": "openclaw:alice",
            "can_send_message": True,
            "message": "accepted via invite",
        }
        with patch("app.api.routes_agent_link._require_agent_link_identity", new=AsyncMock(return_value=("auth", {}, "tenant_bob", "openclaw:bob"))), patch(
            "app.api.routes_agent_link.decode_access_token",
            return_value={"scope": "agent_invite", "tenant_id": "tenant_alice", "agent_id": "openclaw:alice"},
        ), patch("app.api.routes_agent_link.AsyncSessionLocal", return_value=AsyncSessionContext(db)), patch(
            "app.api.routes_agent_link.FriendService"
        ) as svc_cls:
            svc = svc_cls.return_value
            svc.create_request = AsyncMock(return_value=friend)
            svc.accept = AsyncMock(return_value=friend)
            svc.view_payload = Mock(return_value=payload)
            response = await accept_agent_invite("invite-token", request)
        svc.create_request.assert_awaited_once_with("tenant_alice", "openclaw:alice", "openclaw:bob", message="accepted via invite")
        svc.accept.assert_awaited_once_with(1, "tenant_bob", "openclaw:bob")
        self.assertEqual(response.data.context_id, "ctx_bob")

    async def test_agent_link_send_message_without_context_resolves_target_context(self):
        db = AsyncMock()
        request = SimpleNamespace()
        req = AgentLinkSendMessageRequest(
            target_agent_id="openclaw:bob",
            parts=[MessagePart(type="text/plain", text="hello bob")],
            metadata={"kind": "friend-chat"},
        )
        with patch("app.api.routes_agent_link._require_agent_link_identity", new=AsyncMock(return_value=("auth", {}, "tenant_alice", "openclaw:alice"))), patch(
            "app.api.routes_agent_link.AsyncSessionLocal",
            return_value=AsyncSessionContext(db),
        ), patch("app.api.routes_agent_link.FriendService") as svc_cls, patch(
            "app.api.routes_agent_link.create_and_dispatch_message_task",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    task_id="task_001",
                    state="WORKING",
                    context_id="ctx_bob",
                    model_dump=lambda: {"task_id": "task_001", "state": "WORKING", "context_id": "ctx_bob"},
                )
            ),
        ) as dispatch_mock:
            svc_cls.return_value.resolve_target_context = AsyncMock(
                return_value=("tenant_bob", "ctx_bob", {"mode": "friend", "friend_id": "1"})
            )
            response = await agent_link_send_message(req, request)
        self.assertEqual(response.data["task_id"], "task_001")
        self.assertEqual(dispatch_mock.await_args.args[2]["tenant_id"], "tenant_bob")
        sent_req = dispatch_mock.await_args.args[0]
        self.assertEqual(sent_req.context_id, "ctx_bob")
        self.assertEqual(sent_req.metadata["friend_id"], "1")
        self.assertEqual(sent_req.metadata["source_agent_id"], "openclaw:alice")


if __name__ == "__main__":
    unittest.main()
