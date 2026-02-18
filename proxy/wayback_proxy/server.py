"""HTTP proxy server that fetches from Wayback Machine."""

import asyncio
import base64
import os
from pathlib import Path
from string import Template
from typing import Optional
from urllib.parse import urlparse

from .config import Config
from .cache import Cache, CachedResponse
from .throttle import SPEED_TIERS, write_throttled
from .wayback import ContentTransformer, build_backend
from .admin import AdminHandler
from .crawler import Crawler


# Default error descriptions per code
ERROR_DESCRIPTIONS = {
    400: "The request could not be understood by the proxy.",
    403: "This URL is not in the allowlist.",
    404: "This page was not found in the Wayback Machine's archive.",
    500: "An unexpected error occurred in the proxy.",
    501: "This feature is not yet implemented.",
    502: "The Wayback Machine could not be reached.",
    504: "The request to the Wayback Machine timed out.",
}

# Minimal fallback template when no template files are available
FALLBACK_TEMPLATE = Template(
    "<html><body><h1>$code $message</h1><p>$description</p>"
    "<hr><small>$url &middot; $date</small></body></html>"
)

# Minimal fallback landing page
FALLBACK_LANDING_TEMPLATE = Template(
    "<html><body><h1>Wayback Proxy</h1>"
    "<p>Target date: $date</p>$most_viewed</body></html>"
)


class ProxyServer:
    """HTTP proxy server for Wayback Machine."""

    def __init__(self, config: Config):
        self.config = config
        self.cache = Cache(
            redis_url=config.cache.redis_url,
            hot_ttl_seconds=config.cache.hot_ttl_seconds,
            curated_prefix=config.cache.curated_prefix,
            hot_prefix=config.cache.hot_prefix,
            allowlist_key=config.cache.allowlist_key,
        )
        self.transformer = ContentTransformer(
            remove_toolbar=config.transform.remove_wayback_toolbar,
            remove_scripts=config.transform.remove_wayback_scripts,
            fix_base_tags=config.transform.fix_base_tags,
            fix_asset_urls=config.transform.fix_asset_urls,
            normalize_links=config.transform.normalize_links,
        )
        self.backend = build_backend(config, cache=self.cache)
        self._server: Optional[asyncio.Server] = None
        self._crawl_task: Optional[asyncio.Task] = None
        self._reload_task: Optional[asyncio.Task] = None

        # Admin + crawler (crawler uses only live backends)
        self.admin = AdminHandler(self.cache) if config.admin.enabled else None
        live_backend = self.backend.live_only()
        self.crawler = Crawler(
            self.cache, live_backend, self.transformer, config.crawler,
        ) if config.admin.enabled else None

        # Load error page templates
        self._error_templates: dict[int, Template] = {}
        self._default_error_template: Optional[Template] = None
        self._load_error_templates()

        # Load landing page template
        self._landing_template: Optional[Template] = None
        self._load_landing_template()

        # Load header bar snippet
        self._header_bar_template: Optional[Template] = None
        self._load_header_bar_template()

    def _load_error_templates(self):
        """Load error page templates from disk."""
        error_dir = self.config.proxy.error_pages_dir
        if not error_dir:
            # Try default location relative to the proxy package
            pkg_dir = Path(__file__).resolve().parent.parent
            candidate = pkg_dir / "error_pages"
            if candidate.is_dir():
                error_dir = str(candidate)

        if not error_dir or not os.path.isdir(error_dir):
            print("[PROXY] No error_pages directory found, using fallback template")
            return

        print(f"[PROXY] Loading error templates from {error_dir}")

        # Load default template (error.html)
        default_path = os.path.join(error_dir, "error.html")
        if os.path.isfile(default_path):
            with open(default_path, "r", encoding="utf-8") as f:
                self._default_error_template = Template(f.read())
            print(f"[PROXY]   Loaded default: error.html")

        # Load per-code templates (404.html, 403.html, etc.)
        for entry in os.listdir(error_dir):
            if entry == "error.html":
                continue
            name, ext = os.path.splitext(entry)
            if ext == ".html" and name.isdigit():
                code = int(name)
                path = os.path.join(error_dir, entry)
                with open(path, "r", encoding="utf-8") as f:
                    self._error_templates[code] = Template(f.read())
                print(f"[PROXY]   Loaded template: {entry}")

    def _load_landing_template(self):
        """Load landing page template from disk."""
        if not self.config.landing_page.enabled:
            return

        landing_dir = self.config.landing_page.template_dir
        if not landing_dir:
            pkg_dir = Path(__file__).resolve().parent.parent
            candidate = pkg_dir / "landing_page"
            if candidate.is_dir():
                landing_dir = str(candidate)

        if not landing_dir or not os.path.isdir(landing_dir):
            print("[PROXY] No landing_page directory found, using fallback")
            return

        index_path = os.path.join(landing_dir, "index.html")
        if os.path.isfile(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                self._landing_template = Template(f.read())
            print(f"[PROXY] Loaded landing page from {landing_dir}")

    def _load_header_bar_template(self):
        """Load header bar HTML snippet."""
        if not self.config.header_bar.enabled:
            return

        snippet_dir = Path(__file__).resolve().parent / "snippets"
        snippet_path = snippet_dir / "header_bar.html"
        if snippet_path.is_file():
            with open(snippet_path, "r", encoding="utf-8") as f:
                self._header_bar_template = Template(f.read())
            print(f"[PROXY] Loaded header bar snippet")

    def _render_error_page(
        self, code: int, message: str, url: str = "", description: str = ""
    ) -> bytes:
        """Render an error page from template."""
        if not description:
            description = ERROR_DESCRIPTIONS.get(code, message)

        template_vars = {
            "code": str(code),
            "message": message,
            "description": description,
            "url": url,
            "date": self.config.wayback.target_date,
        }

        # Try per-code template first, then default, then fallback
        template = self._error_templates.get(code)
        if not template:
            template = self._default_error_template
        if not template:
            template = FALLBACK_TEMPLATE

        return template.safe_substitute(template_vars).encode("utf-8")

    def _render_landing_page(self, most_viewed_html: str) -> bytes:
        """Render the landing page."""
        speed = self.config.throttle.default_speed
        speed_name = speed if speed != "none" else "unlimited"

        custom_text = ""
        if self.config.header_bar.custom_text:
            custom_text = (
                f'<p class="custom-text">{self.config.header_bar.custom_text}</p>'
            )

        speed_info = ""
        if speed != "none":
            speed_info = (
                f'<p class="speed-info">Connection speed: '
                f'<span>{speed_name}</span></p>'
            )

        template_vars = {
            "date": self.config.wayback.target_date,
            "most_viewed": most_viewed_html,
            "custom_text": custom_text,
            "speed_info": speed_info,
            "speed": speed_name,
        }

        template = self._landing_template or FALLBACK_LANDING_TEMPLATE
        return template.safe_substitute(template_vars).encode("utf-8")

    def _render_header_bar(
        self, wayback_url: str, wayback_date: str, speed: str
    ) -> str:
        """Render the header bar HTML for injection into content."""
        if not self._header_bar_template:
            return ""

        cfg = self.config.header_bar
        is_top = cfg.position == "top"

        # Build speed display
        speed_name = speed if speed != "none" else "unlimited"
        speed_display = f"Speed: {speed_name}"

        # Build speed selector (IE4-compatible)
        speed_selector = ""
        if cfg.show_speed_selector and self.config.throttle.allow_user_override:
            cookie_name = self.config.throttle.cookie_name
            options = ""
            for tier_name in SPEED_TIERS:
                selected = " selected" if tier_name == speed else ""
                label = tier_name if tier_name != "none" else "unlimited"
                options += (
                    f'<option value="{tier_name}"{selected}>{label}</option>'
                )

            speed_display = (
                f'Speed: <select id="wbSpeedSel" '
                f'style="font-family:Courier New,monospace;font-size:11px;'
                f'background:#12122a;color:#e0e0e0;border:1px solid #505070">'
                f'{options}</select>'
            )

            # IE4-compatible onchange via script block
            speed_selector = (
                f'var sel=document.getElementById("wbSpeedSel");\n'
                f'if(sel){{\n'
                f'  sel.onchange=function(){{\n'
                f'    var v=sel.options[sel.selectedIndex].value;\n'
                f'    document.cookie="{cookie_name}="+v+";path=/";\n'
                f'    location.reload();\n'
                f'  }};\n'
                f'}}\n'
            )

        # Custom text
        custom_text = ""
        if cfg.custom_text:
            custom_text = cfg.custom_text

        template_vars = {
            "position_css": "top:0" if is_top else "bottom:0",
            "border_edge": "bottom" if is_top else "top",
            "padding_prop": "paddingTop" if is_top else "paddingBottom",
            "custom_css": cfg.custom_css,
            "custom_text": custom_text,
            "wayback_url": wayback_url,
            "wayback_date": wayback_date,
            "speed_name": speed_name,
            "speed_display": speed_display,
            "speed_selector": speed_selector,
        }

        return self._header_bar_template.safe_substitute(template_vars)

    def _get_effective_speed(self, headers: dict) -> str:
        """Determine the effective speed from cookie or config default."""
        if self.config.throttle.allow_user_override:
            cookie_header = headers.get("cookie", "")
            cookie_name = self.config.throttle.cookie_name
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(cookie_name + "="):
                    value = part[len(cookie_name) + 1:]
                    if value in SPEED_TIERS:
                        return value
        return self.config.throttle.default_speed

    def _is_landing_page_request(self, target: str, headers: dict) -> bool:
        """Check if this request is for the proxy's own landing page.

        Handles both direct access (GET /) and explicit proxy requests.
        Matches against the proxy's own address on any port, since the
        proxy may be exposed on a different external port.
        """
        if not self.config.landing_page.enabled:
            return False

        host = headers.get("host", "")
        proxy_host = self.config.proxy.host
        proxy_port = self.config.proxy.port

        # Extract just the hostname (without port) from the Host header
        host_name = host.split(":")[0] if ":" in host else host
        local_names = {"localhost", "127.0.0.1", proxy_host}
        # Also match 0.0.0.0 since that's a common bind address
        if proxy_host == "0.0.0.0":
            local_names.add("0.0.0.0")

        # Direct access: GET / with Host pointing at proxy
        if target == "/":
            if host_name in local_names:
                return True

        # Explicit proxy: target URL points at proxy itself
        if target.startswith("http"):
            parsed = urlparse(target)
            target_host = parsed.hostname or ""
            if target_host in local_names and parsed.path in ("/", ""):
                return True

        return False

    def _check_admin_auth(self, headers: dict) -> bool:
        """Check HTTP Basic Auth for admin access."""
        password = self.config.admin.password
        if not password:
            return True  # no password = no auth required
        auth = headers.get("authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="replace")
        except Exception:
            return False
        _, _, pw = decoded.partition(":")
        return pw == password

    async def start(self):
        """Start the proxy server."""
        await self.cache.connect()

        # Reset stale crawl state from previous run
        if self.crawler:
            status = await self.cache.get_crawl_status()
            if status.get("state") in ("running", "stopping"):
                await self.cache.set_crawl_status("idle", status.get("progress", {}))
                print("[PROXY] Reset stale crawl state to idle")

        # Start config reload listener if using YAML config
        if self.config._config_path:
            self._reload_task = asyncio.create_task(self._config_reload_listener())

        self._server = await asyncio.start_server(
            self._handle_client,
            self.config.proxy.host,
            self.config.proxy.port,
        )

        addr = self._server.sockets[0].getsockname()
        print(f"[PROXY] Listening on {addr[0]}:{addr[1]}")
        print(f"[PROXY] Backend: {self.backend.name}")
        print(f"[PROXY] Target date: {self.config.wayback.target_date}")
        print(f"[PROXY] Access mode: {self.config.access.mode}")
        if self.config.throttle.default_speed != "none":
            print(f"[PROXY] Throttle: {self.config.throttle.default_speed}")
        if self.config.header_bar.enabled:
            print(f"[PROXY] Header bar: {self.config.header_bar.position}")
        if self.config.landing_page.enabled:
            print(f"[PROXY] Landing page: enabled")
        if self.config.admin.enabled:
            auth_mode = "password" if self.config.admin.password else "open"
            print(f"[PROXY] Admin: enabled (auth: {auth_mode})")
        if self.config._config_path:
            print(f"[PROXY] Config reload: listening on wayback:config_reload")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        """Stop the proxy server."""
        if self._reload_task:
            self._reload_task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await self.backend.close()
        await self.cache.close()

    async def _config_reload_listener(self):
        """Subscribe to Redis for config reload signals.

        Hot-swappable fields: wayback.target_date, throttle.*, header_bar.*,
        landing_page.*, access.mode, admin.password.
        Non-reloadable (require restart): proxy.host, proxy.port, cache.redis_url.
        """
        import redis.asyncio as aioredis

        sub_client = aioredis.from_url(
            self.config.cache.redis_url, decode_responses=True,
        )
        pubsub = sub_client.pubsub()
        await pubsub.subscribe("wayback:config_reload")
        print("[PROXY] Subscribed to wayback:config_reload")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                print("[PROXY] Config reload signal received")
                try:
                    self._apply_config_reload()
                except Exception as e:
                    print(f"[PROXY] Config reload failed: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            await pubsub.unsubscribe("wayback:config_reload")
            await sub_client.close()

    def _apply_config_reload(self):
        """Re-read YAML config and hot-swap runtime fields."""
        path = self.config._config_path
        if not path:
            return

        new_config = Config.from_yaml(path)

        # Wayback settings — propagate to all backends in the chain
        old_date = self.config.wayback.target_date
        self.config.wayback.target_date = new_config.wayback.target_date
        self.config.wayback.date_tolerance_days = new_config.wayback.date_tolerance_days
        self.backend.update_date_config(
            new_config.wayback.target_date, new_config.wayback.date_tolerance_days,
        )
        if old_date != new_config.wayback.target_date:
            print(f"[PROXY] Reloaded target_date: {old_date} -> {new_config.wayback.target_date}")

        # Throttle settings
        self.config.throttle.default_speed = new_config.throttle.default_speed
        self.config.throttle.allow_user_override = new_config.throttle.allow_user_override

        # Header bar settings
        self.config.header_bar.enabled = new_config.header_bar.enabled
        self.config.header_bar.position = new_config.header_bar.position
        self.config.header_bar.custom_text = new_config.header_bar.custom_text
        self.config.header_bar.custom_css = new_config.header_bar.custom_css
        self.config.header_bar.show_speed_selector = new_config.header_bar.show_speed_selector
        # Reload header bar template if toggled on
        if self.config.header_bar.enabled and not self._header_bar_template:
            self._load_header_bar_template()

        # Landing page
        self.config.landing_page.enabled = new_config.landing_page.enabled

        # Access mode
        self.config.access.mode = new_config.access.mode

        # Admin password
        self.config.admin.password = new_config.admin.password

        print("[PROXY] Config reloaded successfully")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """Handle incoming client connection."""
        url = ""
        try:
            # Read request line
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return

            request_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_str.split(" ")

            if len(parts) < 2:
                await self._send_error(writer, 400, "Bad Request")
                return

            method = parts[0].upper()
            target = parts[1]

            # Read headers
            headers = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                try:
                    key, value = line.decode("utf-8", errors="replace").strip().split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                except ValueError:
                    continue

            # Handle CONNECT (HTTPS tunnel) - for now just reject
            if method == "CONNECT":
                # TODO: Implement MITM
                await self._send_error(writer, 501, "CONNECT not implemented yet")
                return

            # Admin interface
            if self.admin and target.startswith("/_admin"):
                if not self._check_admin_auth(headers):
                    writer.write(b"HTTP/1.1 401 Unauthorized\r\n")
                    writer.write(b'WWW-Authenticate: Basic realm="Wayback Proxy Admin"\r\n')
                    writer.write(b"Content-Length: 0\r\n")
                    writer.write(b"Connection: close\r\n\r\n")
                    await writer.drain()
                    return

                # Read POST body if present
                body = b""
                cl = headers.get("content-length")
                if cl and cl.isdigit():
                    try:
                        body = await reader.readexactly(int(cl))
                    except asyncio.IncompleteReadError as e:
                        body = e.partial

                result = await self.admin.handle(method, target, headers, body)

                # "START_CRAWL" / "RECRAWL" are signals to launch the crawler
                if result == "START_CRAWL":
                    await self._start_crawl()
                    result = (303, "/_admin/", b"")
                elif result == "RECRAWL":
                    await self.cache.clear_hot()
                    await self._start_crawl()
                    result = (303, "/_admin/", b"")

                status_code, ct_or_loc, body_bytes = result
                if status_code == 303:
                    writer.write(f"HTTP/1.1 303 See Other\r\n".encode())
                    writer.write(f"Location: {ct_or_loc}\r\n".encode())
                    writer.write(b"Content-Length: 0\r\n")
                    writer.write(b"Connection: close\r\n\r\n")
                else:
                    reason = self.HTTP_REASONS.get(status_code, "OK")
                    writer.write(f"HTTP/1.1 {status_code} {reason}\r\n".encode())
                    writer.write(f"Content-Type: {ct_or_loc}\r\n".encode())
                    writer.write(f"Content-Length: {len(body_bytes)}\r\n".encode())
                    writer.write(b"Connection: close\r\n\r\n")
                    writer.write(body_bytes)
                await writer.drain()
                return

            # Check for landing page request (before URL normalization)
            if self._is_landing_page_request(target, headers):
                await self._send_landing_page(writer)
                return

            # Parse URL
            if target.startswith("/"):
                # Transparent proxy mode - need Host header
                host = headers.get("host")
                if not host:
                    await self._send_error(writer, 400, "Host header required")
                    return
                url = f"http://{host}{target}"
            else:
                url = target

            print(f"[PROXY] {method} {url}")

            # Determine effective speed
            speed = self._get_effective_speed(headers)

            # Check access mode
            if self.config.access.mode == "allowlist":
                if not await self.cache.is_allowed(url):
                    print(f"[PROXY] BLOCKED (not in allowlist): {url}")
                    await self._send_error(
                        writer, 403, "Forbidden", url=url,
                        description="This URL is not in the allowlist. "
                        "Contact the proxy administrator to request access.",
                    )
                    return

            # Fetch from backend chain (cache, pywb, wayback — in configured order)
            response = await self.backend.fetch(url)
            if not response:
                await self._send_error(writer, 404, "Not Found", url=url)
                return

            # Handle redirect responses (pass to client)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "")
                if location:
                    await self._send_redirect(writer, response.status_code, location)
                    return

            # Transform content (skip for cache/pywb hits — already clean)
            content = (
                self.transformer.transform(response.content, response.content_type)
                if response.needs_transform
                else response.content
            )

            # Build cached response
            cached_response = CachedResponse(
                status_code=response.status_code,
                headers=response.headers,
                content=content,
                content_type=response.content_type,
                archived_url=response.archived_url,
                timestamp=response.timestamp,
            )

            # Store in hot cache (only for live backend responses)
            if response.cacheable:
                await self.cache.set_hot(url, cached_response)

            # Send to client
            await self._send_response(writer, cached_response, speed=speed)

            # Track view for HTML pages only (skip assets)
            if "text/html" in response.content_type:
                asyncio.create_task(self._track_view_safe(url))

        except Exception as e:
            print(f"[PROXY] Error: {e}")
            try:
                await self._send_error(writer, 500, "Internal Server Error", url=url)
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _start_crawl(self):
        """Launch the crawler as a background task."""
        if self._crawl_task and not self._crawl_task.done():
            return  # already running
        self._crawl_task = asyncio.create_task(self._run_crawl_safe())

    async def _run_crawl_safe(self):
        """Run crawler, catching exceptions."""
        try:
            await self.crawler.run()
        except Exception as e:
            print(f"[CRAWLER] Unhandled error: {e}")
            try:
                await self.cache.set_crawl_status("idle", {"error": str(e)})
            except Exception:
                pass

    async def _track_view_safe(self, url: str):
        """Track a domain view, ignoring errors."""
        try:
            parsed = urlparse(url)
            domain = parsed.hostname or url
            await self.cache.track_view(domain)
        except Exception:
            pass

    # Standard HTTP reason phrases
    HTTP_REASONS = {
        200: "OK", 201: "Created", 204: "No Content",
        301: "Moved Permanently", 302: "Found", 304: "Not Modified",
        400: "Bad Request", 403: "Forbidden", 404: "Not Found",
        500: "Internal Server Error", 501: "Not Implemented",
        502: "Bad Gateway", 503: "Service Unavailable",
    }

    async def _send_response(
        self,
        writer: asyncio.StreamWriter,
        response: CachedResponse,
        speed: str = "none",
    ):
        """Send cached response to client, with optional throttle and header bar."""
        content = response.content

        # Inject header bar into HTML (post-cache)
        if (
            self.config.header_bar.enabled
            and self._header_bar_template
            and "text/html" in response.content_type
        ):
            bar_html = self._render_header_bar(
                wayback_url=response.archived_url,
                wayback_date=response.timestamp,
                speed=speed,
            )
            content = self.transformer.inject_header_bar(content, bar_html)

        # Status line
        reason = self.HTTP_REASONS.get(response.status_code, "OK")
        status_line = f"HTTP/1.1 {response.status_code} {reason}\r\n"
        writer.write(status_line.encode())

        # Headers
        writer.write(f"Content-Type: {response.content_type}\r\n".encode())
        writer.write(f"Content-Length: {len(content)}\r\n".encode())
        writer.write(b"Connection: close\r\n")
        writer.write(f"X-Wayback-Timestamp: {response.timestamp}\r\n".encode())
        writer.write(f"X-Wayback-URL: {response.archived_url}\r\n".encode())
        writer.write(b"\r\n")
        await writer.drain()

        # Body — throttled
        await write_throttled(writer, content, speed)

    async def _send_landing_page(self, writer: asyncio.StreamWriter):
        """Render and send the landing page."""
        # Fetch most viewed
        count = self.config.landing_page.most_viewed_count
        most_viewed = await self.cache.get_most_viewed(count)

        if most_viewed:
            items = ""
            for domain, views in most_viewed:
                items += (
                    f'<li>{domain} '
                    f'<span class="count">({int(views)} views)</span></li>\n'
                )
            most_viewed_html = f"<ol>\n{items}</ol>"
        else:
            most_viewed_html = '<p class="empty">No pages viewed yet.</p>'

        body_bytes = self._render_landing_page(most_viewed_html)

        reason = self.HTTP_REASONS.get(200, "OK")
        writer.write(f"HTTP/1.1 200 {reason}\r\n".encode())
        writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
        writer.write(f"Content-Length: {len(body_bytes)}\r\n".encode())
        writer.write(b"Connection: close\r\n")
        writer.write(b"\r\n")
        writer.write(body_bytes)
        await writer.drain()

    async def _send_redirect(self, writer: asyncio.StreamWriter, code: int, location: str):
        """Send redirect response to client."""
        reason = self.HTTP_REASONS.get(code, "Found")
        body = f'<html><body><p>Redirecting to <a href="{location}">{location}</a></p></body></html>'
        body_bytes = body.encode()

        writer.write(f"HTTP/1.1 {code} {reason}\r\n".encode())
        writer.write(f"Location: {location}\r\n".encode())
        writer.write(b"Content-Type: text/html\r\n")
        writer.write(f"Content-Length: {len(body_bytes)}\r\n".encode())
        writer.write(b"Connection: close\r\n")
        writer.write(b"\r\n")
        writer.write(body_bytes)
        await writer.drain()

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        code: int,
        message: str,
        url: str = "",
        description: str = "",
    ):
        """Send error response using template."""
        reason = self.HTTP_REASONS.get(code, message)
        body_bytes = self._render_error_page(code, message, url=url, description=description)

        writer.write(f"HTTP/1.1 {code} {reason}\r\n".encode())
        writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
        writer.write(f"Content-Length: {len(body_bytes)}\r\n".encode())
        writer.write(b"Connection: close\r\n")
        writer.write(b"\r\n")
        writer.write(body_bytes)
        await writer.drain()


async def run_proxy(config: Config):
    """Run the proxy server."""
    server = ProxyServer(config)
    try:
        await server.start()
    except KeyboardInterrupt:
        print("\n[PROXY] Shutting down...")
    finally:
        await server.stop()
