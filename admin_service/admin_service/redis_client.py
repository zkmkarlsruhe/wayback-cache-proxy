"""Shared async Redis connection for the admin service."""

import redis.asyncio as aioredis
from typing import Optional


_client: Optional[aioredis.Redis] = None


async def get_redis(redis_url: str = "redis://localhost:6379/0") -> aioredis.Redis:
    """Get or create the shared Redis client."""
    global _client
    if _client is None:
        _client = aioredis.from_url(redis_url, decode_responses=False)
    return _client


async def close_redis():
    """Close the shared Redis client."""
    global _client
    if _client:
        await _client.close()
        _client = None


async def publish_reload(redis_url: str) -> None:
    """Publish a config reload signal to the proxy."""
    client = await get_redis(redis_url)
    await client.publish("wayback:config_reload", "reload")
