"""
根据租户列表生成 Mosquitto passwordfile / aclfile。

用法：
  env PYTHONPATH="$PWD/backend" backend/.venv/bin/python \
    backend/scripts/render_mosquitto_auth.py \
    --passwordfile deploy/mosquitto/passwordfile \
    --aclfile deploy/mosquitto/aclfile
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.services.mosquitto_auth_sync import MosquittoAuthSyncService


async def main() -> int:
    parser = argparse.ArgumentParser(description="Render tenant-scoped Mosquitto auth files.")
    parser.add_argument("--passwordfile", required=True, help="Output path for Mosquitto password_file")
    parser.add_argument("--aclfile", required=True, help="Output path for Mosquitto acl_file")
    args = parser.parse_args()

    service = MosquittoAuthSyncService(
        passwordfile=args.passwordfile,
        aclfile=args.aclfile,
        topic_base=settings.MQTT_BASE_TOPIC,
    )
    engine = create_async_engine(settings.DATABASE_URL, future=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as db:
            tenant_ids = await service.sync_active_tenants(db)
    finally:
        await engine.dispose()
    print(f"rendered mosquitto auth for {len(tenant_ids)} tenants")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
