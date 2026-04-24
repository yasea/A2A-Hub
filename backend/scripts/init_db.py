"""
初始化 A2A Hub 数据库结构和基础数据。

当前 `db-init` 面向全新部署，使用 SQLAlchemy metadata 直接建表。
仓库同时保留 Alembic versions 供后续增量升级；如果是已上线环境升级，
应优先执行 `alembic upgrade head`，不要只依赖 `create_all`。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import Base, engine
from app.models import *  # noqa: F403 - 确保所有模型注册到 Base.metadata


DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "tenant_001")
DEFAULT_TENANT_NAME = os.getenv("DEFAULT_TENANT_NAME", "默认租户")


async def main() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)
        table_check = await conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'agent_link_error_events'
                )
                """
            )
        )
        has_error_table = bool(table_check.scalar())
        if not has_error_table:
            raise RuntimeError("数据库初始化失败：缺少 agent_link_error_events 表")
        await conn.execute(
            text(
                """
                INSERT INTO tenants (tenant_id, name)
                VALUES (:tenant_id, :name)
                ON CONFLICT (tenant_id) DO NOTHING
                """
            ),
            {"tenant_id": DEFAULT_TENANT_ID, "name": DEFAULT_TENANT_NAME},
        )
    await engine.dispose()
    print(f"数据库初始化完成，默认租户：{DEFAULT_TENANT_ID}，error_events_table={has_error_table}")


if __name__ == "__main__":
    asyncio.run(main())
