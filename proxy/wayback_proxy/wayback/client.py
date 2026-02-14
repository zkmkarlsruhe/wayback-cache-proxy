"""Wayback Machine HTTP client."""

import re
import httpx
from typing import Optional
from dataclasses import dataclass


@dataclass
class WaybackResponse:
    """Response from Wayback Machine."""
    status_code: int
    headers: dict
    content: bytes
    content_type: str
    archived_url: str
    timestamp: str


# Patterns for detecting Wayback Machine special pages
_RE_PLAYBACK_IFRAME = re.compile(
    rb'<iframe id="playback" src="((?:(?:https?:)?//web\.archive\.org)?/web/[^"]+)"',
)
_RE_REDIRECT_IMPATIENT = re.compile(
    rb'<p class="impatient"><a href="(?:(?:https?:)?//web\.archive\.org)?/web/([^/]+)/([^"]+)">Impatient\?</a></p>',
)
_RE_REDIRECT_CODE = re.compile(
    rb'<p class="code shift red">Got an HTTP ([0-9]+)',
)
_RE_WAYBACK_REDIRECT = re.compile(
    r'(?:(?:https?:)?//web\.archive\.org)?/web/([^/]+/)(.+)',
)

# GeoCities → OoCities mapping
GEOCITIES_HOSTS = ("www.geocities.com", "geocities.com")
OOCITIES_HOST = "www.oocities.org"


class WaybackClient:
    """Client for fetching pages from the Wayback Machine."""

    MAX_REDIRECTS = 10

    def __init__(
        self,
        target_date: str = "20010101",
        date_tolerance_days: int = 365,
        base_url: str = "https://web.archive.org",
        geocities_fix: bool = True,
    ):
        self.target_date = target_date
        self.date_tolerance_days = date_tolerance_days
        self.base_url = base_url
        self.geocities_fix = geocities_fix
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,  # We handle redirects manually
            headers={"User-Agent": "WaybackProxy/0.1.0"},
        )

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    def build_wayback_url(self, url: str, modifier: str = "if_") -> str:
        """Build Wayback Machine URL."""
        return f"{self.base_url}/web/{self.target_date}{modifier}/{url}"

    def _apply_geocities_fix(self, url: str) -> str:
        """Route GeoCities URLs through OoCities mirror."""
        if not self.geocities_fix:
            return url
        for host in GEOCITIES_HOSTS:
            if f"://{host}" in url or f"://{host}/" in url:
                url = url.replace(f"://{host}", f"://{OOCITIES_HOST}")
                print(f"[GEOCITIES] Rerouted to {url}")
                break
        return url

    @staticmethod
    def _extract_timestamp(url: str, fallback: str) -> str:
        """Extract timestamp from a Wayback URL."""
        if "/web/" in url:
            parts = url.split("/web/")
            if len(parts) > 1:
                ts_part = parts[1].split("/")[0]
                return "".join(c for c in ts_part if c.isdigit())[:14]
        return fallback

    @staticmethod
    def _extract_archived_url(wayback_url: str) -> Optional[str]:
        """Extract the original URL from a Wayback URL."""
        match = _RE_WAYBACK_REDIRECT.search(wayback_url)
        if match:
            return match.group(2)
        return None

    async def check_availability(self, url: str) -> Optional[dict]:
        """Check if URL is available in Wayback Machine."""
        api_url = f"{self.base_url}/wayback/available"
        params = {"url": url, "timestamp": self.target_date}

        try:
            response = await self._client.get(api_url, params=params)
            if response.status_code == 200:
                data = response.json()
                snapshots = data.get("archived_snapshots", {})
                closest = snapshots.get("closest")
                if closest and closest.get("available"):
                    return closest
        except Exception:
            pass
        return None

    async def fetch(self, url: str) -> Optional[WaybackResponse]:
        """
        Fetch URL from Wayback Machine with full redirect and
        special-page handling ported from richardg867's WaybackProxy.
        """
        # Apply GeoCities fix before fetching
        fetch_url = self._apply_geocities_fix(url)

        wayback_url = self.build_wayback_url(fetch_url)
        redirect_count = 0

        while redirect_count < self.MAX_REDIRECTS:
            try:
                response = await self._client.get(wayback_url)
            except httpx.HTTPError as e:
                print(f"[ERROR] Failed to fetch {url}: {e}")
                return None

            # Handle HTTP redirects manually
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if not location:
                    break

                # Check if this is a redirect to a different archived URL
                match = _RE_WAYBACK_REDIRECT.search(location)
                if match:
                    archived_dest = match.group(2)
                    # Remove :80 from URLs
                    archived_dest = re.sub(r'^([^/]*//[^/:]+):80/', r'\1/', archived_dest)

                    # If it redirects to a different site, pass the redirect
                    # to the client instead of following it ourselves
                    if archived_dest != fetch_url and archived_dest != url:
                        print(f"[REDIRECT] {url} -> {archived_dest}")
                        return WaybackResponse(
                            status_code=response.status_code,
                            headers={"location": archived_dest},
                            content=b"",
                            content_type="text/html",
                            archived_url=url,
                            timestamp=self._extract_timestamp(location, self.target_date),
                        )

                # Same-site redirect (different date/modifier), follow it
                if location.startswith("/"):
                    wayback_url = f"{self.base_url}{location}"
                else:
                    wayback_url = location
                redirect_count += 1
                continue

            # Treat 4xx/5xx as "not found", but check for memento Link
            # header first — its presence means this is a site error, not
            # a Wayback error, so pass it through
            if response.status_code >= 400:
                if "link" not in response.headers:
                    print(f"[WAYBACK] {response.status_code} for {url}")
                    return None

            content_type = response.headers.get("content-type", "text/html")
            guessed_type = response.headers.get(
                "x-archive-guessed-content-type", content_type
            )

            # JavaScript content-type bypass: Wayback injects its own JS
            # into anything it thinks is JavaScript. Re-fetch with im_
            # modifier to get clean content.
            if "javascript" in (guessed_type or ""):
                current_url = str(response.url)
                match = re.match(
                    r'(https?://web\.archive\.org/web/[0-9]+)([^/]*)(/.+)',
                    current_url,
                )
                if match and match.group(2) != "im_":
                    wayback_url = match.group(1) + "im_" + match.group(3)
                    print(f"[JS-BYPASS] Re-fetching with im_ modifier: {url}")
                    redirect_count += 1
                    continue

            # Check if response is HTML that might be a Wayback special page
            if "text/html" in (guessed_type or ""):
                result = self._handle_wayback_page(
                    response.content, url, fetch_url
                )
                if result == "excluded":
                    print(f"[WAYBACK] URL excluded: {url}")
                    return None
                elif isinstance(result, str) and result.startswith("http"):
                    # It's a new URL to fetch (iframe extraction)
                    wayback_url = result
                    redirect_count += 1
                    print(f"[IFRAME] Extracting content from iframe: {url}")
                    continue
                elif isinstance(result, WaybackResponse):
                    # It's a redirect response for the client
                    return result
                # else: result is None, meaning it's normal content

            # Extract timestamp
            timestamp = self._extract_timestamp(
                str(response.url), self.target_date
            )

            return WaybackResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                content=response.content,
                content_type=content_type,
                archived_url=url,
                timestamp=timestamp,
            )

        print(f"[ERROR] Too many redirects for {url}")
        return None

    def _handle_wayback_page(
        self, content: bytes, original_url: str, fetch_url: str
    ) -> Optional[object]:
        """
        Detect and handle Wayback Machine special pages.

        Returns:
            None - normal content, continue processing
            "excluded" - URL is excluded from Wayback
            str (URL) - re-fetch this URL (iframe extraction)
            WaybackResponse - redirect response for the client
        """
        # Quick check: is this a Wayback Machine page?
        if b"<title>Wayback Machine</title>" not in content:
            # Also check for the redirect-style page
            if b"<title></title>" not in content:
                return None
            if b"Wayback Machine" not in content:
                return None

        # Check for exclusion (robots.txt block)
        if b"This URL has been excluded from the Wayback Machine" in content:
            return "excluded"

        # Check for media playback iframe — some sites render inside
        # a playback iframe instead of directly. Extract and re-fetch.
        match = _RE_PLAYBACK_IFRAME.search(content)
        if match:
            iframe_url = match.group(1).decode("ascii", "ignore")
            if iframe_url.startswith("/"):
                iframe_url = f"{self.base_url}{iframe_url}"
            return iframe_url

        # Check for Wayback redirect page ("Impatient?" link)
        match = _RE_REDIRECT_IMPATIENT.search(content)
        if match:
            date_code = match.group(1).decode("ascii", "ignore")
            archived_url = match.group(2).decode("ascii", "ignore")

            # Sanitize: add protocol if missing, convert https to http
            if "://" not in archived_url and not archived_url.startswith("/"):
                archived_url = "http://" + archived_url
            elif archived_url.startswith("https://"):
                archived_url = "http://" + archived_url[8:]

            # Extract the original HTTP redirect code
            code_match = _RE_REDIRECT_CODE.search(content)
            try:
                redirect_code = int(code_match.group(1))
            except (AttributeError, ValueError):
                redirect_code = 302

            print(f"[REDIRECT] Wayback redirect page: {original_url} -> {archived_url}")
            return WaybackResponse(
                status_code=redirect_code,
                headers={"location": archived_url},
                content=b"",
                content_type="text/html",
                archived_url=original_url,
                timestamp=date_code.rstrip("/"),
            )

        return None

    async def fetch_raw(self, url: str) -> Optional[WaybackResponse]:
        """
        Fetch raw content (images, scripts, etc) from Wayback.
        Uses 'id_' modifier for unmodified content.
        """
        wayback_url = self.build_wayback_url(url, modifier="id_")

        try:
            response = await self._client.get(wayback_url)

            return WaybackResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                content=response.content,
                content_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                archived_url=url,
                timestamp=self.target_date,
            )

        except httpx.HTTPError as e:
            print(f"[ERROR] Failed to fetch raw {url}: {e}")
            return None
