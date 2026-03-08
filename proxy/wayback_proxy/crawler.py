"""Prefetch crawler — spiders seed URLs via Wayback and stores in curated cache."""

import asyncio
import re
import time
from collections import deque
from typing import Set
from urllib.parse import urljoin, urlparse

from .cache import Cache, CachedResponse
from .config import CrawlerConfig
from .wayback import Backend, ContentTransformer


# Patterns for extracting links and assets from HTML
_RE_HREF = re.compile(r'<a\s[^>]*href="([^"]*)"', re.IGNORECASE)
_RE_ASSETS = re.compile(
    r'<(?:img|script)\s[^>]*src="([^"]*)"'
    r'|<link\s[^>]*href="([^"]*)"',
    re.IGNORECASE,
)


class Crawler:
    """BFS crawler that fetches from Wayback and stores in curated cache."""

    def __init__(
        self,
        cache: Cache,
        backend: Backend,
        transformer: ContentTransformer,
        config: CrawlerConfig,
    ):
        self.cache = cache
        self.backend = backend
        self.transformer = transformer
        self.concurrency = config.concurrency
        self.same_domain_only = config.same_domain_only
        self.max_urls = config.max_urls
        self._semaphore = asyncio.Semaphore(config.concurrency)
        self._progress_lock = asyncio.Lock()

    async def run(self) -> None:
        """Main crawl loop: read seeds, BFS, store in curated cache."""
        seeds = await self.cache.get_seeds()
        if not seeds:
            await self._log("No seeds configured, nothing to crawl.")
            return

        await self.cache.set_crawl_status("running", {
            "fetched": 0, "total": 0, "errors": 0, "current_url": "",
        })
        await self._log(f"Crawl started with {len(seeds)} seed(s)")

        fetched = 0
        errors = 0
        total_queued = 0
        visited: Set[str] = set()

        # Build a global work queue from all seeds
        # Each item: (url, current_depth, max_depth, seed_domain)
        queue: deque = deque()
        for seed_url, depth in seeds:
            seed_domain = urlparse(seed_url).netloc.lower()
            queue.append((seed_url, 0, depth, seed_domain))
            total_queued += 1

        async def update_progress(
            delta_fetched: int = 0, delta_errors: int = 0,
            current_url: str = "",
        ):
            """Thread-safe progress update — only touches progress, not state."""
            nonlocal fetched, errors
            async with self._progress_lock:
                fetched += delta_fetched
                errors += delta_errors
                await self.cache.set_crawl_progress({
                    "fetched": fetched, "total": total_queued,
                    "errors": errors, "current_url": current_url,
                })

        async def process(url: str, level: int, max_depth: int, seed_domain: str):
            async with self._semaphore:
                # Check stop signal
                if await self._should_stop():
                    return None

                await update_progress(current_url=url)

                try:
                    response = await self.backend.fetch(url)
                    if not response:
                        await update_progress(delta_errors=1)
                        await self._log(f"MISS  {url}")
                        return None

                    # Skip redirects — don't store them
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        await self._log(f"REDIR {url} -> {location}")
                        return None

                    transformed = (
                        self.transformer.transform(
                            response.content, response.content_type,
                        )
                        if response.needs_transform
                        else response.content
                    )

                    cached = CachedResponse(
                        status_code=response.status_code,
                        headers=response.headers,
                        content=transformed,
                        content_type=response.content_type,
                        archived_url=response.archived_url,
                        timestamp=response.timestamp,
                    )
                    await self.cache.set_curated(url, cached)
                    await update_progress(delta_fetched=1)
                    await self._log(f"OK    {url}")

                    # Extract child links if HTML and within depth
                    if level < max_depth and "text/html" in response.content_type:
                        return self._extract_links(
                            transformed, url, seed_domain, level, max_depth,
                        )
                except Exception as e:
                    await update_progress(delta_errors=1)
                    await self._log(f"ERR   {url}: {e}")

                return None

        # BFS — process queue, spawning tasks with concurrency limit
        while queue:
            if await self._should_stop():
                await self._log("Crawl stopped by user.")
                break

            # Drain a batch from the queue
            batch = []
            while queue and len(batch) < self.concurrency * 2:
                url, level, max_depth, seed_domain = queue.popleft()
                normalized = self._normalize_url(url)
                if normalized in visited:
                    continue
                visited.add(normalized)

                # Enforce max_urls cap
                if self.max_urls and len(visited) > self.max_urls:
                    await self._log(
                        f"Reached max_urls limit ({self.max_urls}), stopping."
                    )
                    queue.clear()
                    break

                # Skip if already in curated cache
                existing = await self.cache.get(normalized)
                if existing:
                    # Still extract links from cached HTML for spidering
                    if level < max_depth and "text/html" in existing.content_type:
                        children = self._extract_links(
                            existing.content, normalized, seed_domain,
                            level, max_depth,
                        )
                        for child in children:
                            c_url, c_level, c_max, c_domain = child
                            if self._normalize_url(c_url) not in visited:
                                queue.append(child)
                                total_queued += 1
                    continue

                batch.append((normalized, level, max_depth, seed_domain))

            if not batch:
                continue

            tasks = [
                asyncio.create_task(process(u, l, md, sd))
                for u, l, md, sd in batch
            ]
            results = await asyncio.gather(*tasks)

            # Enqueue discovered children
            for child_links in results:
                if child_links:
                    for child in child_links:
                        c_url, c_level, c_max, c_domain = child
                        if self._normalize_url(c_url) not in visited:
                            queue.append(child)
                            total_queued += 1

        await self.cache.set_crawl_status("idle", {
            "fetched": fetched, "total": total_queued,
            "errors": errors, "current_url": "",
        })
        await self._log(
            f"Crawl finished: {fetched} fetched, {errors} errors, "
            f"{total_queued} total URLs seen."
        )

    def _extract_links(
        self, content: bytes, base_url: str, seed_domain: str,
        current_level: int, max_depth: int,
    ) -> list:
        """Extract child URLs from HTML content.

        Returns list of (url, level, max_depth, seed_domain) tuples.
        """
        try:
            html = content.decode("utf-8", errors="replace")
        except Exception:
            return []

        children = []
        next_level = current_level + 1

        # Extract <a href> links (same-domain only if configured)
        for match in _RE_HREF.finditer(html):
            href = match.group(1).strip()
            link = self._resolve_url(href, base_url)
            if not link:
                continue
            if self.same_domain_only:
                if urlparse(link).netloc.lower() != seed_domain:
                    continue
            children.append((link, next_level, max_depth, seed_domain))

        # Extract asset URLs (img src, script src, link href) — any domain
        for match in _RE_ASSETS.finditer(html):
            asset_url = match.group(1) or match.group(2)
            if not asset_url:
                continue
            asset_url = asset_url.strip()
            link = self._resolve_url(asset_url, base_url)
            if not link:
                continue
            children.append((link, next_level, max_depth, seed_domain))

        return children

    @staticmethod
    def _resolve_url(href: str, base_url: str) -> str | None:
        """Resolve a potentially relative URL against a base, filtering junk."""
        if not href:
            return None
        # Skip anchors, javascript:, mailto:, data:
        lower = href.lower()
        if lower.startswith(("#", "javascript:", "mailto:", "data:")):
            return None

        resolved = urljoin(base_url, href)
        # Only keep http(s)
        if not resolved.startswith(("http://", "https://")):
            return None
        # Strip fragment
        resolved = resolved.split("#")[0]
        return resolved

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Basic URL normalization for dedup."""
        # Strip trailing slash for consistency, lowercase scheme+host
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        query = parsed.query
        normalized = f"{parsed.scheme}://{host}{path}"
        if query:
            normalized += f"?{query}"
        return normalized

    async def _should_stop(self) -> bool:
        """Check if admin requested a stop."""
        try:
            status = await self.cache.get_crawl_status()
            return status.get("state") == "stopping"
        except Exception:
            return False

    async def _log(self, message: str) -> None:
        """Append a timestamped log line."""
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        print(f"[CRAWLER] {message}")
        try:
            await self.cache.append_crawl_log(line)
        except Exception:
            pass
