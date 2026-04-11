"""
Service account API。
"""
from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import settings
from app.core.security import create_service_account_token
from app.schemas.common import ApiResponse
from app.schemas.service_account import ServiceAccountTokenRequest, ServiceAccountTokenResponse

router = APIRouter(prefix="/v1/service-accounts", tags=["service-accounts"])


@router.post(
    "/token",
    response_model=ApiResponse[ServiceAccountTokenResponse],
    summary="签发服务账号 token",
    description="平台内部组件、调度器或系统 Agent 使用。用签发密钥换取带 scopes 的 Bearer token，再调用消息、路由等受保护接口。",
)
async def issue_service_account_token(
    req: ServiceAccountTokenRequest,
    x_service_account_issuer_secret: str | None = Header(default=None, alias="X-Service-Account-Issuer-Secret"),
) -> ApiResponse[ServiceAccountTokenResponse]:
    """
    签发平台内部服务账号 token。

    生产环境必须配置 `SERVICE_ACCOUNT_ISSUER_SECRET`，并通过 Header 传入同值。
    token 内会携带 `component_type`，便于后续区分调度器、工作流、系统 agent 等平台组件。
    """
    if not settings.SERVICE_ACCOUNT_ISSUER_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="服务账号签发未启用")
    if x_service_account_issuer_secret != settings.SERVICE_ACCOUNT_ISSUER_SECRET:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="服务账号签发密钥无效")

    access_token = create_service_account_token(
        service_account_id=req.service_account_id,
        tenant_id=req.tenant_id,
        component_type=req.component_type,
        scopes=req.scopes,
        metadata=req.metadata,
    )
    return ApiResponse.ok(
        ServiceAccountTokenResponse(
            access_token=access_token,
            expires_in_seconds=settings.SERVICE_ACCOUNT_TOKEN_EXPIRE_MINUTES * 60,
            tenant_id=req.tenant_id,
            service_account_id=req.service_account_id,
            component_type=req.component_type,
            scopes=req.scopes,
        )
    )
