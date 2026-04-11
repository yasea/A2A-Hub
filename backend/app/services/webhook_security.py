"""
Webhook 验签与防重放。
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.integration import WebhookNonce


class WebhookSecurityService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def verify(
        self,
        source_system: str,
        secret: str,
        timestamp: str,
        nonce: str,
        signature: str,
        body: bytes,
    ) -> None:
        await self.db.execute(
            delete(WebhookNonce).where(WebhookNonce.expires_at < datetime.now(timezone.utc))
        )

        expires_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        now = datetime.now(timezone.utc)
        if abs((now - expires_at).total_seconds()) > settings.WEBHOOK_NONCE_TTL_SECONDS:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook 时间戳无效")

        existing = await self.db.execute(select(WebhookNonce).where(WebhookNonce.nonce == nonce))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook nonce 重复")

        message = b".".join([timestamp.encode("utf-8"), nonce.encode("utf-8"), body])
        expected = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook 签名无效")

        self.db.add(
            WebhookNonce(
                nonce=nonce,
                source_system=source_system,
                expires_at=now + timedelta(seconds=settings.WEBHOOK_NONCE_TTL_SECONDS),
            )
        )
