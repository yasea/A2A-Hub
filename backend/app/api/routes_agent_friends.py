from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbDep, TenantDep
from app.schemas.friend import FriendCreateRequest, FriendResponse, FriendUpdateRequest
from app.services.friend_service import (
    FriendConflictError,
    FriendForbiddenError,
    FriendNotFoundError,
    FriendService,
)
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/v1/agents", tags=["agent-friends"])


@router.post("/{agent_id}/friends", response_model=ApiResponse[FriendResponse], status_code=status.HTTP_201_CREATED)
async def create_friend_request(agent_id: str, req: FriendCreateRequest, db: DbDep, tenant: TenantDep):
    svc = FriendService(db)
    try:
        friend = await svc.create_request(tenant["tenant_id"], agent_id, req.target_agent_id, req.message)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FriendForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FriendConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return ApiResponse.ok(FriendResponse.model_validate(svc.view_payload(friend, tenant["tenant_id"], agent_id)))


@router.get("/{agent_id}/friends", response_model=ApiResponse[list[FriendResponse]])
async def list_agent_friends(agent_id: str, db: DbDep, tenant: TenantDep):
    svc = FriendService(db)
    try:
        friends = await svc.list_for_agent(tenant["tenant_id"], agent_id)
    except FriendForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return ApiResponse.ok(
        [FriendResponse.model_validate(svc.view_payload(friend, tenant["tenant_id"], agent_id)) for friend in friends]
    )


@router.patch("/{agent_id}/friends/{friend_id}", response_model=ApiResponse[FriendResponse])
async def update_friend(agent_id: str, friend_id: int, req: FriendUpdateRequest, db: DbDep, tenant: TenantDep):
    svc = FriendService(db)
    try:
        await svc.assert_agent_owned(tenant["tenant_id"], agent_id)
        friend = await svc.update_status(friend_id, tenant["tenant_id"], agent_id, req.status)
    except FriendNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FriendForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except FriendConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return ApiResponse.ok(FriendResponse.model_validate(svc.view_payload(friend, tenant["tenant_id"], agent_id)))
