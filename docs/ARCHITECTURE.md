# Architecture

## Overview

Wayback Cache Proxy is an HTTP proxy that intercepts browser requests, fetches the corresponding archived page from the Internet Archive Wayback Machine, transforms the content to remove Wayback-specific artifacts, caches it in Redis, and serves it to the client.

It was built for exhibition use — an existing wayback proxy ([richardg867/WaybackProxy](https://github.com/richardg867/WaybackProxy)) worked well but had no caching, which meant every page load hit the Wayback Machine. This project adds caching, an admin interface, and a prefetch crawler so exhibitions can run reliably offline.

---

## Stack

```
Browser  ──HTTP Proxy──>  Wayback Cache Proxy  ──>  Redis (curated/hot)
                                │                         │
                                └──── Wayback Machine ────┘
                                      (cache miss)
```

- **Proxy**: Raw asyncio TCP server, manual HTTP parsing
- **Cache**: Redis with two tiers (curated + hot)
- **Admin**: Built-in HTML interface at `/_admin/`
- **Crawler**: Async spider that prefetches into curated cache

No external web frameworks — the server handles HTTP directly.

---

## Request Flow

```
Client Request
      │
      ▼
┌─────────────┐
│ Parse URL   │
└─────────────┘
      │
      ▼
┌─────────────┐    ┌─────────────┐
│ Allow List  │───>│   REJECT    │ (if not allowed)
│   Check     │    └─────────────┘
└─────────────┘
      │ (allowed)
      ▼
┌─────────────┐    ┌─────────────┐
│ Check Redis │───>│   RETURN    │ (cache hit)
│   Cache     │    │   cached    │
└─────────────┘    └─────────────┘
      │ (cache miss)
      ▼
┌─────────────┐
│ Fetch from  │
│  Wayback    │
└─────────────┘
      │
      ▼
┌─────────────┐
│ Transform   │ (remove toolbar, fix URLs, etc.)
│  Content    │
└─────────────┘
      │
      ▼
┌─────────────┐
│ Store in    │
│ Redis (hot) │
└─────────────┘
      │
      ▼
┌─────────────┐
│ Inject      │ (header bar, speed throttle)
│ + RETURN    │
└─────────────┘
```

The header bar is injected **post-cache** so cache entries don't need invalidation when config changes.

---

## Module Structure

```
proxy/
├── Dockerfile
├── pyproject.toml
├── poetry.lock
├── error_pages/            # Custom error page templates (403, 404, generic)
├── landing_page/           # Landing page template
└── wayback_proxy/
    ├── __init__.py
    ├── __main__.py         # CLI entry point, argument parsing
    ├── config.py           # Dataclass-based configuration (env + CLI)
    ├── server.py           # Async TCP proxy server, request routing
    ├── cache.py            # Redis two-tier cache (curated/hot)
    ├── admin.py            # Admin interface (HTML + XHR auto-refresh)
    ├── crawler.py          # Async prefetch spider
    ├── throttle.py         # Speed throttling (14.4k to DSL)
    ├── snippets/
    │   └── header_bar.html # Header bar template
    ├── https/
    │   └── __init__.py     # HTTPS placeholder (not yet implemented)
    └── wayback/
        ├── __init__.py
        ├── client.py       # Wayback Machine HTTP client, redirect handling
        └── transformer.py  # Content transforms (toolbar removal, URL fixing)
```

---

## Redis Data Model

| Key Pattern | Type | TTL | Description |
|-------------|------|-----|-------------|
| `curated:{hash}` | String (JSON) | None | Permanent cached responses (crawler/admin managed) |
| `hot:{hash}` | String (JSON) | 7 days | Auto-cached on-demand fetches |
| `allowlist:urls` | Set | None | Allowed URL patterns (allowlist mode) |
| `views:urls` | Sorted Set | None | Domain view counts for landing page |
| `crawl:seeds` | Hash | None | Seed URLs with depth |
| `crawl:status` | Hash | None | Crawl state + progress |
| `crawl:log` | List | None | Recent crawl log lines (capped at 200) |

URL hashes are SHA-256 of the normalized URL, truncated to 16 hex chars.

---

## Content Transformations

The `ContentTransformer` strips Wayback Machine artifacts from archived pages:

1. **Remove Wayback Toolbar** — the injected toolbar HTML block
2. **Remove Wayback Scripts** — `/_static/js/` script tags
3. **Fix Base Tags** — base hrefs pointing at `web.archive.org`
4. **Fix Asset URLs** — convert `/web/20010915im_/http://...` back to original URLs
5. **Normalize Links** — strip `web.archive.org/web/` prefix from all links

---

## Configuration

All settings are dataclass-based in `config.py`. Configuration sources (in priority order):

1. CLI arguments (highest)
2. Environment variables
3. Defaults (lowest)

See `python -m wayback_proxy --help` for all options.
