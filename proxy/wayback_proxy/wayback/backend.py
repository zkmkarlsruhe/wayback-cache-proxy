"""Backend abstraction — ABC, chain, cache backend, and factory."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..cache import Cache


@dataclass
class WaybackResponse:
    """Response from any backend."""
    status_code: int
    headers: dict
    content: bytes
    content_type: str
    archived_url: str
    timestamp: str
    needs_transform: bool = True   # False for pywb and cache (already clean)
    cacheable: bool = True         # False for pywb and cache (no need to re-cache)


class Backend(abc.ABC):
    """A single source of archived content."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        ...

    @property
    @abc.abstractmethod
    def is_live(self) -> bool:
        """True for backends that hit the live internet (Wayback Machine)."""
        ...

    @abc.abstractmethod
    async def fetch(self, url: str) -> Optional[WaybackResponse]:
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...

    def update_date_config(self, target_date: str, date_tolerance_days: int) -> None:
        """Update target date — default no-op."""


class BackendChain(Backend):
    """Tries backends in order; first non-None response wins."""

    def __init__(self, backends: list[Backend]) -> None:
        self._backends = backends

    @property
    def name(self) -> str:
        return " -> ".join(b.name for b in self._backends)

    @property
    def is_live(self) -> bool:
        return any(b.is_live for b in self._backends)

    async def fetch(self, url: str) -> Optional[WaybackResponse]:
        for backend in self._backends:
            response = await backend.fetch(url)
            if response is not None:
                print(f"[CHAIN] HIT {backend.name}: {url}")
                return response
            print(f"[CHAIN] MISS {backend.name}: {url}")
        return None

    async def close(self) -> None:
        for backend in self._backends:
            await backend.close()

    def update_date_config(self, target_date: str, date_tolerance_days: int) -> None:
        for backend in self._backends:
            backend.update_date_config(target_date, date_tolerance_days)

    def live_only(self) -> BackendChain:
        """Return a new chain containing only is_live backends (for crawler)."""
        return BackendChain([b for b in self._backends if b.is_live])


class CacheBackend(Backend):
    """Read-only cache lookup as a backend in the chain."""

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    @property
    def name(self) -> str:
        return "cache"

    @property
    def is_live(self) -> bool:
        return False

    async def fetch(self, url: str) -> Optional[WaybackResponse]:
        cached = await self._cache.get(url)
        if cached is None:
            return None
        return WaybackResponse(
            status_code=cached.status_code,
            headers=cached.headers,
            content=cached.content,
            content_type=cached.content_type,
            archived_url=cached.archived_url,
            timestamp=cached.timestamp,
            needs_transform=False,
            cacheable=False,
        )

    async def close(self) -> None:
        pass  # cache lifecycle managed by server


def build_backend(config, cache: Cache) -> BackendChain:
    """Build a BackendChain from config.

    If config.backends.chain is empty, returns the default: cache -> wayback.
    """
    from .client import WaybackClient
    from .pywb_client import PywbClient

    chain_cfg = config.backends.chain

    if not chain_cfg:
        # Default: cache -> wayback
        return BackendChain([
            CacheBackend(cache),
            WaybackClient(
                target_date=config.wayback.target_date,
                date_tolerance_days=config.wayback.date_tolerance_days,
                base_url=config.wayback.base_url,
                geocities_fix=config.wayback.geocities_fix,
            ),
        ])

    backends: list[Backend] = []
    for entry in chain_cfg:
        btype = entry.get("type", "")
        if btype == "cache":
            backends.append(CacheBackend(cache))
        elif btype == "wayback":
            backends.append(WaybackClient(
                target_date=config.wayback.target_date,
                date_tolerance_days=config.wayback.date_tolerance_days,
                base_url=entry.get("base_url", config.wayback.base_url),
                geocities_fix=config.wayback.geocities_fix,
            ))
        elif btype == "pywb":
            backends.append(PywbClient(
                base_url=entry.get("base_url", "http://localhost:8080"),
                collection=entry.get("collection", "web"),
                target_date=config.wayback.target_date,
                date_tolerance_days=config.wayback.date_tolerance_days,
            ))
        else:
            print(f"[CHAIN] Unknown backend type: {btype!r}, skipping")

    if not backends:
        print("[CHAIN] Empty chain after config, using default (cache -> wayback)")
        return BackendChain([
            CacheBackend(cache),
            WaybackClient(
                target_date=config.wayback.target_date,
                date_tolerance_days=config.wayback.date_tolerance_days,
                base_url=config.wayback.base_url,
                geocities_fix=config.wayback.geocities_fix,
            ),
        ])

    return BackendChain(backends)
