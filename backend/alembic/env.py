"""
Alembic 异步迁移环境配置
"""
import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

# 导入所有模型，确保 metadata 包含全部表
from app.core.db import Base
import app.models.tenant      # noqa
import app.models.agent       # noqa
import app.models.context     # noqa
import app.models.task        # noqa
import app.models.approval    # noqa
import app.models.delivery    # noqa
import app.models.audit      # noqa
import app.models.integration  # noqa
import app.models.agent_friend  # noqa
import app.models.routing     # noqa
import app.models.service     # noqa
import app.models.agent_link_error  # noqa

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = os.getenv("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("alembic env: DATABASE_URL 环境变量和 sqlalchemy.url 均未配置")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
