"""pywb HTTP client — fetches archived pages from a pywb instance."""

import re
import httpx
from typing import Optional
from urllib.parse import urlparse

from .backend import Backend, WaybackResponse


class PywbClient(Backend):
    """Client for fetching pages from a pywb instance.

    Supports two modes:
      - replay (default): constructs /{collection}/{timestamp}id_/{url} URLs
        for pywb instances serving local WARC files.
      - proxy: sends requests through pywb as an HTTP proxy, for pywb instances
        configured with enable_http_proxy and memento proxy sequences.
    """

    MAX_REDIRECTS = 10

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        collection: str = "web",
        target_date: str = "20010101",
        date_tolerance_days: int = 365,
        mode: str = "replay",
    ):
        self.base_url = base_url.rstrip("/")
        self.collection = collection
        self.target_date = target_date
        self.date_tolerance_days = date_tolerance_days
        self.mode = mode

        if self.mode == "proxy":
            self._client = httpx.AsyncClient(
                proxy=self.base_url,
                timeout=30.0,
                follow_redirects=False,
                headers={"User-Agent": "WaybackProxy/0.1.0"},
                verify=False,
            )
        else:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=False,
                headers={"User-Agent": "WaybackProxy/0.1.0"},
            )

    @property
    def name(self) -> str:
        if self.mode == "proxy":
            return f"pywb-proxy({self.base_url})"
        return f"pywb({self.base_url}/{self.collection})"

    @property
    def is_live(self) -> bool:
        return False

    async def close(self) -> None:
        await self._client.aclose()

    def update_date_config(self, target_date: str, date_tolerance_days: int) -> None:
        self.target_date = target_date
        self.date_tolerance_days = date_tolerance_days

    def _build_pywb_url(self, url: str) -> str:
        """Build pywb replay URL using id_ modifier for raw content."""
        return f"{self.base_url}/{self.collection}/{self.target_date}id_/{url}"

    async def fetch(self, url: str) -> Optional[WaybackResponse]:
        """Fetch URL from pywb. Returns None on miss (404)."""
        if self.mode == "proxy":
            return await self._fetch_proxy(url)
        return await self._fetch_replay(url)

    async def forward_raw(self, url: str) -> Optional[WaybackResponse]:
        """Forward a request through pywb proxy, returning any response including 404.

        Used for pywb.proxy magic hostname requests where the response from pywb
        is always the final answer (no chain fallthrough).
        """
        if self.mode != "proxy":
            return None
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as e:
            print(f"[PYWB] Proxy forward failed for {url}: {e}")
            return None

        content_type = response.headers.get("content-type", "text/html")
        return WaybackResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            content_type=content_type,
            archived_url=url,
            timestamp=self.target_date,
            needs_transform=False,
            cacheable=False,
        )

    async def _fetch_proxy(self, url: str) -> Optional[WaybackResponse]:
        """Fetch via pywb HTTP proxy mode — send request through pywb."""
        try:
            response = await self._client.get(url)
        except httpx.HTTPError as e:
            print(f"[PYWB] Proxy failed for {url}: {e}")
            return None

        if response.status_code == 404:
            return None

        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if location:
                return WaybackResponse(
                    status_code=response.status_code,
                    headers={"location": location},
                    content=b"",
                    content_type="text/html",
                    archived_url=url,
                    timestamp=self.target_date,
                    needs_transform=False,
                    cacheable=False,
                )
            return None

        if response.status_code >= 400 and not response.content:
            print(f"[PYWB] Proxy {response.status_code} for {url}")
            return None

        # Extract Memento-Datetime header if present (pywb sets this)
        timestamp = self.target_date
        memento_dt = response.headers.get("memento-datetime", "")
        if memento_dt:
            timestamp = self._parse_memento_datetime(memento_dt)

        content_type = response.headers.get("content-type", "text/html")

        return WaybackResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            content=response.content,
            content_type=content_type,
            archived_url=url,
            timestamp=timestamp,
            needs_transform=False,
            cacheable=False,
        )

    async def _fetch_replay(self, url: str) -> Optional[WaybackResponse]:
        """Fetch via pywb replay mode — construct /{collection}/{ts}id_/{url}."""
        pywb_url = self._build_pywb_url(url)
        base_host = urlparse(self.base_url).netloc
        redirect_count = 0

        while redirect_count < self.MAX_REDIRECTS:
            try:
                response = await self._client.get(pywb_url)
            except httpx.HTTPError as e:
                print(f"[PYWB] Failed to fetch {url}: {e}")
                return None

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location:
                    break

                # Resolve relative redirects
                if location.startswith("/"):
                    location = f"{self.base_url}{location}"

                # If redirect stays within the same pywb host, follow it
                redir_host = urlparse(location).netloc
                if redir_host == base_host:
                    pywb_url = location
                    redirect_count += 1
                    continue

                # Cross-site redirect — extract the original URL and pass
                # the redirect back to the client
                archived_url = self._extract_original_url(location)
                if archived_url:
                    print(f"[PYWB] Redirect {url} -> {archived_url}")
                    return WaybackResponse(
                        status_code=response.status_code,
                        headers={"location": archived_url},
                        content=b"",
                        content_type="text/html",
                        archived_url=url,
                        timestamp=self.target_date,
                        needs_transform=False,
                        cacheable=False,
                    )
                break

            if response.status_code == 404:
                return None

            # Any other error without content → miss
            if response.status_code >= 400 and not response.content:
                print(f"[PYWB] {response.status_code} for {url}")
                return None

            content_type = response.headers.get("content-type", "text/html")
            timestamp = self._extract_timestamp(str(response.url))

            return WaybackResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                content=response.content,
                content_type=content_type,
                archived_url=url,
                timestamp=timestamp,
                needs_transform=False,
                cacheable=False,
            )

        print(f"[PYWB] Too many redirects for {url}")
        return None

    def _parse_memento_datetime(self, dt_str: str) -> str:
        """Parse Memento-Datetime header to YYYYMMDDHHMMSS timestamp."""
        # Memento-Datetime: Thu, 01 Jan 2004 00:00:00 GMT
        import email.utils
        import time
        try:
            parsed = email.utils.parsedate(dt_str)
            if parsed:
                return time.strftime("%Y%m%d%H%M%S", parsed)
        except Exception:
            pass
        return self.target_date

    def _extract_timestamp(self, pywb_url: str) -> str:
        """Extract timestamp from a pywb replay URL."""
        # Pattern: /{collection}/{timestamp}{modifier}/{url}
        pattern = rf'/{re.escape(self.collection)}/(\d+)'
        match = re.search(pattern, pywb_url)
        if match:
            return match.group(1)[:14]
        return self.target_date

    def _extract_original_url(self, redirect_url: str) -> Optional[str]:
        """Extract the original archived URL from a pywb redirect URL."""
        # pywb URLs: {base_url}/{collection}/{timestamp}{modifier}/{original_url}
        # Try to strip the pywb prefix
        pattern = rf'{re.escape(self.base_url)}/{re.escape(self.collection)}/\d+[a-z_]*/(.+)'
        match = re.match(pattern, redirect_url)
        if match:
            return match.group(1)
        return None
