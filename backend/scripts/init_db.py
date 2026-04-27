"""
初始化 A2A Hub 数据库结构和基础数据。

全新部署：alembic upgrade head 从零建表。
已有数据库（旧版 create_all 创建）：先修补缺失的列/约束/触发器，
再 stamp head 标记版本，最后 upgrade head 执行增量迁移。
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_TENANT_ID = os.getenv("DEFAULT_TENANT_ID", "tenant_001")
DEFAULT_TENANT_NAME = os.getenv("DEFAULT_TENANT_NAME", "默认租户")

# 旧版 create_all 遗留的修补语句：添加缺失的列、约束、函数、触发器
LEGACY_PATCHES = [
    # 公共函数：set_updated_at
    """
    CREATE OR REPLACE FUNCTION set_updated_at()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    # agents.public_number（旧版 create_all 缺失）
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS public_number BIGINT",
    # 回填 public_number
    """
    UPDATE agents SET public_number = 10000000 + s.rn
    FROM (SELECT agent_id, ROW_NUMBER() OVER (ORDER BY created_at, agent_id) AS rn FROM agents WHERE public_number IS NULL) s
    WHERE agents.agent_id = s.agent_id AND agents.public_number IS NULL
    """,
    "ALTER TABLE agents ALTER COLUMN public_number SET NOT NULL",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_public_number ON agents (public_number)",
    "CREATE INDEX IF NOT EXISTS idx_agents_public_number ON agents (public_number)",
    # updated_at 触发器
    "CREATE TRIGGER IF NOT EXISTS tr_tenants_updated_at BEFORE UPDATE ON tenants FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
    "CREATE TRIGGER IF NOT EXISTS tr_agents_updated_at BEFORE UPDATE ON agents FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
    "CREATE TRIGGER IF NOT EXISTS tr_contexts_updated_at BEFORE UPDATE ON contexts FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
    "CREATE TRIGGER IF NOT EXISTS tr_deliveries_updated_at BEFORE UPDATE ON deliveries FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
    "CREATE TRIGGER IF NOT EXISTS tr_routing_rules_updated_at BEFORE UPDATE ON routing_rules FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
    "CREATE TRIGGER IF NOT EXISTS tr_service_publications_updated_at BEFORE UPDATE ON service_publications FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
    "CREATE TRIGGER IF NOT EXISTS tr_service_threads_updated_at BEFORE UPDATE ON service_threads FOR EACH ROW EXECUTE PROCEDURE set_updated_at()",
]


async def _has_alembic_version(url: str) -> bool:
    """检查 alembic_version 表是否存在且有记录。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(url)
    async with eng.connect() as conn:
        r = await conn.execute(text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='alembic_version')"
        ))
        has_table = bool(r.scalar())
        if has_table:
            r2 = await conn.execute(text("SELECT COUNT(*) FROM alembic_version"))
            return r2.scalar() > 0
    await eng.dispose()
    return False


async def _apply_legacy_patches(url: str) -> None:
    """对旧版 create_all 建立的数据库修补缺失结构。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(url)
    async with eng.begin() as conn:
        for patch in LEGACY_PATCHES:
            try:
                await conn.execute(text(patch))
            except Exception:
                pass  # 已存在则跳过
    await eng.dispose()
    print("旧版数据库修补完成")


def _ensure_alembic_version(url: str) -> None:
    """通过 alembic stamp 标记版本；若 stamp 失败则直接建表插入版本号。"""
    stamp_result = subprocess.run(
        ["alembic", "stamp", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ},
        capture_output=True,
    )
    if stamp_result.returncode == 0:
        print("alembic stamp head 成功")
        return

    # stamp 失败——直接用 SQL 创建 alembic_version 表并插入版本号
    print(f"alembic stamp 失败（rc={stamp_result.returncode}），通过 SQL 手动写入版本号...")
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _stamp():
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            await conn.execute(text(
                "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            ))
            await conn.execute(text("DELETE FROM alembic_version"))
            await conn.execute(text(
                "INSERT INTO alembic_version (version_num) VALUES ('20260425_add_agent_public_number')"
            ))
        await eng.dispose()

    asyncio.run(_stamp())
    print("手动写入 alembic_version 成功")


async def _write_alembic_version(url: str, version: str) -> None:
    """直接用 SQL 创建 alembic_version 并写入指定版本号。"""
    from sqlalchemy.ext.asyncio import create_async_engine
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(64) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
        ))
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(text(f"INSERT INTO alembic_version (version_num) VALUES ('{version}')"))
    await eng.dispose()
    print(f"已写入 alembic_version: {version}")


def run_alembic_upgrade() -> None:
    """执行 alembic upgrade head。"""
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ},
        capture_output=True,
    )
    if result.returncode == 0:
        print("alembic upgrade head 成功")
        return

    # upgrade 失败，打印详情
    print(f"alembic upgrade head 失败（rc={result.returncode}）")
    print(f"stdout: {result.stdout.decode()[-2000:]}")
    print(f"stderr: {result.stderr.decode()[-2000:]}")
    raise RuntimeError(f"alembic upgrade head 失败，返回码：{result.returncode}")


async def seed_default_tenant() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ["DATABASE_URL"]
    eng = create_async_engine(url)
    async with eng.begin() as conn:
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
    await eng.dispose()
    print(f"默认租户已就绪：{DEFAULT_TENANT_ID}")


async def main() -> None:
    url = os.environ["DATABASE_URL"]

    # 已有版本记录——直接升级
    if await _has_alembic_version(url):
        # 确保 alembic_version.version_num 列足够宽（旧 CREATE TABLE 使用了 VARCHAR(32)）
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async with eng.begin() as conn:
            await conn.execute(text(
                "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"
            ))
        await eng.dispose()
        print("数据库已有 alembic 版本记录，执行增量迁移...")
        run_alembic_upgrade()
    else:
        # 先检查是否有已存在的表（旧版 create_all 遗留）
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async with eng.connect() as conn:
            r = await conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='tenants')"
            ))
            has_tenants = bool(r.scalar())
        await eng.dispose()

        if has_tenants:
            print("检测到旧版数据库，修补缺失结构...")
            await _apply_legacy_patches(url)
            print("修补完成，写入版本记录...")
            # 旧数据库已包含 baseline 全部表结构，直接写入最新版本号跳过所有迁移
            await _write_alembic_version(url, "20260425_add_agent_public_number")
            # 再执行增量迁移（若有的话）
            print("执行增量迁移...")
        else:
            print("全新数据库，执行 alembic 创建全部表...")

        run_alembic_upgrade()

    await seed_default_tenant()
    print("数据库初始化完成")


if __name__ == "__main__":
    asyncio.run(main())