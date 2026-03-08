"""Local HTTP client — forwards matching requests to a self-hosted API."""

import fnmatch
import httpx
from typing import Optional
from urllib.parse import urlparse

from .backend import Backend, WaybackResponse


class LocalClient(Backend):
    """Forward requests matching specific hostnames to a local HTTP service.

    Use this to replace external APIs with self-hosted implementations.
    Non-matching requests return None so the chain continues to the next backend.

    Config example:
        - type: local
          base_url: "http://localhost:9000"
          hostnames:
            - "api.sounddogs.com"
            - "*.example.com"
    """

    def __init__(
        self,
        base_url: str = "http://localhost:9000",
        hostnames: Optional[list[str]] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.hostnames = hostnames or []
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "WaybackProxy/0.1.0"},
        )

    @property
    def name(self) -> str:
        if self.hostnames:
            hosts = ", ".join(self.hostnames[:3])
            if len(self.hostnames) > 3:
                hosts += f", +{len(self.hostnames) - 3} more"
            return f"local({self.base_url} <- {hosts})"
        return f"local({self.base_url} <- *)"

    @property
    def is_live(self) -> bool:
        return False

    async def close(self) -> None:
        await self._client.aclose()

    def _matches(self, url: str) -> bool:
        """Check if the request URL matches any configured hostname pattern."""
        if not self.hostnames:
            return True  # no filter = catch-all
        hostname = urlparse(url).hostname or ""
        return any(fnmatch.fnmatch(hostname, pattern) for pattern in self.hostnames)

    async def fetch(self, url: str) -> Optional[WaybackResponse]:
        """Forward matching requests to the local service."""
        if not self._matches(url):
            return None

        parsed = urlparse(url)
        # Reconstruct path + query for the local service
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        local_url = f"{self.base_url}{path}"

        try:
            response = await self._client.get(
                local_url,
                headers={
                    "X-Original-Host": parsed.hostname or "",
                    "X-Original-URL": url,
                },
            )
        except httpx.HTTPError as e:
            print(f"[LOCAL] Failed {url} -> {local_url}: {e}")
            return None

        content_type = response.headers.get("content-type", "application/octet-stream")

        return WaybackResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            content_type=content_type,
            archived_url=url,
            timestamp="",
            needs_transform=False,
            cacheable=False,
        )
