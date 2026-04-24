from pydantic import BaseModel


class FriendCreateRequest(BaseModel):
    target_agent_id: str
    message: str | None = None


class FriendResponse(BaseModel):
    id: int
    tenant_id: str
    requester_tenant_id: str
    target_tenant_id: str
    requester_agent_id: str
    target_agent_id: str
    status: str
    context_id: str | None = None
    peer_agent_id: str | None = None
    can_send_message: bool = False
    message: str | None = None

    class Config:
        from_attributes = True


class FriendUpdateRequest(BaseModel):
    status: str  # accepted / rejected / blocked
