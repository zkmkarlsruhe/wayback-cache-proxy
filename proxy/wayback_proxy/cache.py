"""Redis cache layer with curated and hot tiers."""

import hashlib
import json
from typing import List, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from urllib.parse import urlparse

import redis.asyncio as redis


@dataclass
class CachedResponse:
    """Cached response data."""
    status_code: int
    headers: dict
    content: bytes
    content_type: str
    archived_url: str
    timestamp: str

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict (content as base64)."""
        import base64
        return {
            "status_code": self.status_code,
            "headers": self.headers,
            "content": base64.b64encode(self.content).decode("ascii"),
            "content_type": self.content_type,
            "archived_url": self.archived_url,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CachedResponse":
        """Create from dict."""
        import base64
        return cls(
            status_code=data["status_code"],
            headers=data["headers"],
            content=base64.b64decode(data["content"]),
            content_type=data["content_type"],
            archived_url=data["archived_url"],
            timestamp=data["timestamp"],
        )


class Cache:
    """Redis cache with curated and hot tiers."""

    VIEWS_KEY = "views:urls"

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        hot_ttl_seconds: int = 604800,  # 7 days
        curated_prefix: str = "curated:",
        hot_prefix: str = "hot:",
        allowlist_key: str = "allowlist:urls",
    ):
        self.redis_url = redis_url
        self.hot_ttl = hot_ttl_seconds
        self.curated_prefix = curated_prefix
        self.hot_prefix = hot_prefix
        self.allowlist_key = allowlist_key
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        """Connect to Redis."""
        self._client = redis.from_url(self.redis_url, decode_responses=False)
        await self._client.ping()
        print(f"[CACHE] Connected to Redis: {self.redis_url}")

    async def close(self):
        """Close Redis connection."""
        if self._client:
            await self._client.close()

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize URL for consistent cache keys (lowercase host, strip trailing slash)."""
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        normalized = f"{parsed.scheme}://{host}{path}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        return normalized

    @classmethod
    def url_hash(cls, url: str) -> str:
        """Generate hash for URL (normalizes first for consistency)."""
        return hashlib.sha256(cls.normalize_url(url).encode()).hexdigest()[:16]

    async def get(self, url: str) -> Optional[CachedResponse]:
        """
        Get cached response for URL.
        Checks curated first, then hot.
        """
        url_key = self.url_hash(url)

        # Check curated first (permanent)
        curated_key = f"{self.curated_prefix}{url_key}"
        data = await self._client.get(curated_key)
        if data:
            print(f"[CACHE] HIT (curated): {url}")
            return CachedResponse.from_dict(json.loads(data))

        # Check hot cache
        hot_key = f"{self.hot_prefix}{url_key}"
        data = await self._client.get(hot_key)
        if data:
            print(f"[CACHE] HIT (hot): {url}")
            return CachedResponse.from_dict(json.loads(data))

        print(f"[CACHE] MISS: {url}")
        return None

    async def set_hot(self, url: str, response: CachedResponse):
        """Store response in hot cache with TTL."""
        url_key = self.url_hash(url)
        hot_key = f"{self.hot_prefix}{url_key}"
        data = json.dumps(response.to_dict())
        await self._client.setex(hot_key, self.hot_ttl, data)
        print(f"[CACHE] SET (hot, TTL={self.hot_ttl}s): {url}")

    async def set_curated(self, url: str, response: CachedResponse):
        """Store response in curated cache (permanent)."""
        url_key = self.url_hash(url)
        curated_key = f"{self.curated_prefix}{url_key}"
        data = json.dumps(response.to_dict())
        await self._client.set(curated_key, data)
        print(f"[CACHE] SET (curated): {url}")

    async def delete(self, url: str, tier: str = "both"):
        """Delete URL from cache."""
        url_key = self.url_hash(url)

        if tier in ("hot", "both"):
            await self._client.delete(f"{self.hot_prefix}{url_key}")

        if tier in ("curated", "both"):
            await self._client.delete(f"{self.curated_prefix}{url_key}")

    async def clear_hot(self):
        """Clear all hot cache entries."""
        pattern = f"{self.hot_prefix}*"
        cursor = 0
        deleted = 0
        while True:
            cursor, keys = await self._client.scan(cursor, match=pattern, count=100)
            if keys:
                await self._client.delete(*keys)
                deleted += len(keys)
            if cursor == 0:
                break
        print(f"[CACHE] Cleared {deleted} hot entries")
        return deleted

    # Allowlist management
    async def is_allowed(self, url: str) -> bool:
        """Check if URL is in allowlist."""
        return await self._client.sismember(self.allowlist_key, url)

    async def add_to_allowlist(self, url: str):
        """Add URL to allowlist."""
        await self._client.sadd(self.allowlist_key, url)

    async def remove_from_allowlist(self, url: str):
        """Remove URL from allowlist."""
        await self._client.srem(self.allowlist_key, url)

    async def get_allowlist(self) -> Set[str]:
        """Get all allowed URLs."""
        members = await self._client.smembers(self.allowlist_key)
        return {m.decode() if isinstance(m, bytes) else m for m in members}

    async def clear_allowlist(self):
        """Clear allowlist."""
        await self._client.delete(self.allowlist_key)

    # View tracking
    async def track_view(self, url: str) -> None:
        """Increment view count for a URL (fire-and-forget)."""
        await self._client.zincrby(self.VIEWS_KEY, 1, url)

    async def get_most_viewed(self, count: int = 10) -> List[Tuple[str, int]]:
        """Return top-N most viewed URLs with their counts."""
        results = await self._client.zrevrange(
            self.VIEWS_KEY, 0, count - 1, withscores=True
        )
        return [
            (url.decode() if isinstance(url, bytes) else url, int(score))
            for url, score in results
        ]

    # Crawl seed CRUD
    CRAWL_SEEDS_KEY = "crawl:seeds"
    CRAWL_STATUS_KEY = "crawl:status"
    CRAWL_LOG_KEY = "crawl:log"
    CRAWL_LOG_MAX = 200

    async def add_seed(self, url: str, depth: int) -> None:
        """Add a crawl seed URL with depth."""
        await self._client.hset(self.CRAWL_SEEDS_KEY, url, str(depth))

    async def remove_seed(self, url: str) -> None:
        """Remove a crawl seed URL."""
        await self._client.hdel(self.CRAWL_SEEDS_KEY, url)

    async def get_seeds(self) -> List[Tuple[str, int]]:
        """Get all crawl seeds as (url, depth) pairs."""
        data = await self._client.hgetall(self.CRAWL_SEEDS_KEY)
        return [
            (k.decode() if isinstance(k, bytes) else k,
             int(v.decode() if isinstance(v, bytes) else v))
            for k, v in data.items()
        ]

    async def clear_seeds(self) -> None:
        """Clear all crawl seeds."""
        await self._client.delete(self.CRAWL_SEEDS_KEY)

    # Crawl status
    async def set_crawl_status(self, state: str, progress: dict) -> None:
        """Set crawl status (state + progress JSON)."""
        await self._client.hset(self.CRAWL_STATUS_KEY, mapping={
            "state": state,
            "progress": json.dumps(progress),
        })

    async def set_crawl_progress(self, progress: dict) -> None:
        """Update only the progress field, leaving state untouched."""
        await self._client.hset(
            self.CRAWL_STATUS_KEY, "progress", json.dumps(progress),
        )

    async def get_crawl_status(self) -> dict:
        """Get crawl status. Returns {state, progress}."""
        data = await self._client.hgetall(self.CRAWL_STATUS_KEY)
        if not data:
            return {"state": "idle", "progress": {}}
        state = data.get(b"state", data.get("state", b"idle"))
        if isinstance(state, bytes):
            state = state.decode()
        progress_raw = data.get(b"progress", data.get("progress", b"{}"))
        if isinstance(progress_raw, bytes):
            progress_raw = progress_raw.decode()
        return {"state": state, "progress": json.loads(progress_raw)}

    # Crawl log
    async def append_crawl_log(self, message: str) -> None:
        """Append a log line (newest first, capped at CRAWL_LOG_MAX)."""
        await self._client.lpush(self.CRAWL_LOG_KEY, message)
        await self._client.ltrim(self.CRAWL_LOG_KEY, 0, self.CRAWL_LOG_MAX - 1)

    async def get_crawl_log(self, count: int = 50) -> List[str]:
        """Get recent crawl log lines."""
        items = await self._client.lrange(self.CRAWL_LOG_KEY, 0, count - 1)
        return [i.decode() if isinstance(i, bytes) else i for i in items]

    async def clear_crawl_log(self) -> None:
        """Clear crawl log."""
        await self._client.delete(self.CRAWL_LOG_KEY)

    # Stats
    async def get_stats(self) -> dict:
        """Get cache statistics."""
        curated_count = 0
        hot_count = 0

        cursor = 0
        while True:
            cursor, keys = await self._client.scan(cursor, match=f"{self.curated_prefix}*", count=100)
            curated_count += len(keys)
            if cursor == 0:
                break

        cursor = 0
        while True:
            cursor, keys = await self._client.scan(cursor, match=f"{self.hot_prefix}*", count=100)
            hot_count += len(keys)
            if cursor == 0:
                break

        allowlist_count = await self._client.scard(self.allowlist_key)

        return {
            "curated_count": curated_count,
            "hot_count": hot_count,
            "allowlist_count": allowlist_count,
        }
