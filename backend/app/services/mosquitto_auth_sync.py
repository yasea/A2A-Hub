"""
租户列表 -> Mosquitto passwordfile / aclfile 同步。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.tenant import Tenant
from app.services.mqtt_auth import mosquitto_password_hash, tenant_mqtt_password, tenant_mqtt_username


class MosquittoAuthSyncService:
    def __init__(
        self,
        *,
        passwordfile: str | None = None,
        aclfile: str | None = None,
        reload_stamp: str | None = None,
        topic_base: str | None = None,
    ):
        self.passwordfile = Path(passwordfile) if passwordfile else None
        self.aclfile = Path(aclfile) if aclfile else None
        self.reload_stamp = Path(reload_stamp) if reload_stamp else None
        self.topic_base = str(topic_base or settings.MQTT_BASE_TOPIC or "a2a-hub")

    @property
    def configured(self) -> bool:
        return self.passwordfile is not None and self.aclfile is not None

    async def load_active_tenant_ids(self, db: AsyncSession) -> list[str]:
        result = await db.execute(
            select(Tenant.tenant_id)
            .where(Tenant.status == "ACTIVE")
            .order_by(Tenant.tenant_id.asc())
        )
        return [str(item) for item in result.scalars().all()]

    def build_acl(self) -> str:
        base = self.topic_base.strip().rstrip("/") or "a2a-hub"
        return "\n".join(
            [
                "# 租户级 ACL：Mosquitto 用户名即 tenant_id。",
                f"pattern readwrite {base}/%u/#",
                "",
            ]
        )

    def build_passwordfile(self, tenant_ids: list[str]) -> str:
        lines: list[str] = []
        for tenant_id in tenant_ids:
            username = tenant_mqtt_username(tenant_id)
            password = tenant_mqtt_password(tenant_id)
            lines.append(f"{username}:{mosquitto_password_hash(password)}")
        return "\n".join(lines) + ("\n" if lines else "")

    def _atomic_write(self, target: Path, content: str) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_name(f".{target.name}.tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(target)

    def _touch_reload_stamp(self) -> None:
        if self.reload_stamp is None:
            return
        self._atomic_write(
            self.reload_stamp,
            datetime.now(timezone.utc).isoformat() + "\n",
        )

    def write_files(self, tenant_ids: list[str]) -> None:
        if not self.configured:
            return
        assert self.passwordfile is not None
        assert self.aclfile is not None
        self._atomic_write(self.passwordfile, self.build_passwordfile(tenant_ids))
        self._atomic_write(self.aclfile, self.build_acl())
        self._touch_reload_stamp()

    async def sync_active_tenants(self, db: AsyncSession) -> list[str]:
        tenant_ids = await self.load_active_tenant_ids(db)
        self.write_files(tenant_ids)
        return tenant_ids


def build_default_mosquitto_auth_sync_service() -> MosquittoAuthSyncService:
    return MosquittoAuthSyncService(
        passwordfile=settings.MQTT_AUTH_PASSWORDFILE,
        aclfile=settings.MQTT_AUTH_ACLFILE,
        reload_stamp=settings.MQTT_AUTH_RELOAD_STAMP,
        topic_base=settings.MQTT_BASE_TOPIC,
    )
