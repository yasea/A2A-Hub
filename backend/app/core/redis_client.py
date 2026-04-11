"""
Redis 客户端懒加载封装。
"""
from redis.asyncio import Redis, from_url

from app.core.config import settings

_redis_client: Redis | None = None


def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)
    return _redis_client
