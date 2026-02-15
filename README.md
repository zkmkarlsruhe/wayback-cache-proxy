# Wayback Cache Proxy

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.18652679-blue)](https://doi.org/10.5281/zenodo.18652679)
[![ZKM](https://img.shields.io/badge/ZKM-Karlsruhe-blue)](https://zkm.de)
[![ZKM Open Source](https://img.shields.io/badge/ZKM-Open%20Source-blue)](https://github.com/zkmkarlsruhe)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> Browse the old web through a caching Wayback Machine proxy. Redis-backed two-tier cache, admin interface, prefetch crawler, modem speed throttling, and header bar overlay. Built for museums and media art exhibitions.

---

## Why This Exists

For [*Choose Your Filter!*](https://zkm.de/en/press/choose-your-filter-browser-art-since-the-beginnings-of-the-world-wide-web) at ZKM Karlsruhe (2025), we showed 30 years of artistic web browsers -- works like [JODI](https://wrongbrowser.jodi.org/)'s *%WRONG Browser*, [I/O/D](https://bak.spc.org/iod/)'s *Web Stalker*, [Maciej Wisniewski](https://www.netomat.net/)'s *netomat*, and many others. These aren't static artworks. They need to fetch live web pages to function: *Web Stalker* strips a site down to its link structure, *netomat* dissolves pages into floating streams of text and image, *%WRONG Browser* turns any website into a JODI piece. To show them as they were meant to be experienced, you need the web pages they were built to browse -- pages from the late 1990s and early 2000s that only exist in the [Wayback Machine](https://web.archive.org/) now.

We used a non-caching Wayback proxy ([richardg867/WaybackProxy](https://github.com/richardg867/WaybackProxy)) which worked well enough -- until the Internet Archive was hit by [repeated DDoS attacks and a major data breach](https://blog.archive.org/2024/05/28/internet-archive-and-the-wayback-machine-under-ddos-cyber-attack/) that took it offline for days. The aftermath left the Wayback Machine [significantly slower and less stable](https://gizmodo.com/the-wayback-machines-snapshotting-breakdown-2000675330) well into 2025. Every artwork that depended on it was affected -- visitors saw blank screens and error pages instead of net art.

This proxy was built after that experience so it won't happen again. It fetches pages from the Wayback Machine once, stores them in Redis, and serves them locally from then on. The prefetch crawler can spider entire sites into the curated cache before an exhibition opens, so even a complete Wayback Machine outage won't take the artworks down.

---

## Features

- **Caching proxy** — fetches archived pages from the Wayback Machine and caches them in Redis for fast, offline-capable serving
- **Two-tier cache** — permanent curated tier (admin/crawler managed) and auto-expiring hot tier (on-demand fetches)
- **Admin interface** — web UI for managing crawl seeds, cache, and monitoring crawl progress with live updates
- **Prefetch crawler** — spider URLs from seed pages into curated cache before the exhibition opens
- **Speed throttling** — simulate period-accurate connection speeds (14.4k, 28.8k, 56k, ISDN, DSL) with visitor-selectable dropdown
- **Header bar overlay** — injected info bar showing current URL, archive date, and speed selector
- **Landing page** — styled start page with most-viewed domains
- **Custom error pages** — themed 403, 404, and generic error templates
- **Allowlist mode** — restrict browsing to pre-approved URLs

---

## Quick Start

### Prerequisites

- Python 3.11+
- Redis 7+
- Poetry (for dependency management)

### Installation

```bash
git clone https://github.com/zkmkarlsruhe/wayback-cache-proxy.git
cd wayback-cache-proxy/proxy

poetry install
```

### Usage

```bash
# Start Redis
redis-server &

# Run the proxy with YAML config
cp config.example.yaml config.yaml
python -m wayback_proxy --config config.yaml

# Or with CLI flags
python -m wayback_proxy --port 8888 --date 20010911 --header-bar --admin
```

Then configure your browser's HTTP proxy to `localhost:8888` and browse any URL.

### Docker

```bash
cp config.example.yaml config.yaml
docker-compose up
```

This starts three services:
- **Proxy** on port 8888 — configure your browser to use this as an HTTP proxy
- **Admin** on port 8080 — open in your browser for remote management
- **Redis** on port 6379 — shared cache and state

---

## Configuration

All settings can be managed through a YAML config file. Copy the example and edit:

```bash
cp config.example.yaml config.yaml
```

See [`config.example.yaml`](config.example.yaml) for all available options with inline documentation.

### CLI Options (Proxy)

| Flag | Default | Description |
|------|---------|-------------|
| `--config` | | Path to YAML config file |
| `--port` | 8888 | Listen port |
| `--date` | 20010101 | Wayback target date (YYYYMMDD) |
| `--redis` | redis://localhost:6379/0 | Redis URL |
| `--header-bar` | off | Show overlay header bar |
| `--header-bar-position` | top | `top` or `bottom` |
| `--header-bar-text` | | Custom branding text |
| `--speed` | unlimited | Default throttle: `14.4k`, `28.8k`, `56k`, `isdn`, `dsl` |
| `--speed-selector` | off | Let visitors pick speed via dropdown |
| `--admin` | off | Enable admin at `/_admin/` |
| `--admin-password` | | Password for admin Basic Auth |
| `--allowlist` | off | Restrict to allowlisted domains |
| `--error-pages` | | Custom error page template directory |
| `--no-landing-page` | | Disable the landing page |

### Live Config Reload

When using `--config`, the proxy subscribes to a Redis Pub/Sub channel for live reload signals. The admin service publishes to this channel when you save config changes, so most settings take effect immediately without restarting the proxy.

---

## Admin Interfaces

### FastAPI Admin Service (port 8080)

A separate web application for remote management with a modern dark-themed UI:

```bash
# Standalone
cd admin_service && python -m admin_service --config ../config.yaml

# Or via Docker
docker-compose up admin
```

Features:
- **Dashboard** — cache stats, crawl status, most viewed domains
- **Configuration** — edit all settings through a web form, with live reload to the proxy
- **Cache Browser** — paginated list with search, delete individual entries, clear tiers
- **Crawler** — seed management, start/stop/recrawl, live log with htmx auto-refresh

### Built-in Admin (/_admin/)

Access at `http://proxy-host:port/_admin/` (with Basic Auth if configured). This is an IE4-compatible interface embedded in the proxy, suitable for local/exhibition use.

- **Crawl Seeds** — add URLs with depth for prefetch crawling
- **Crawl Control** — start, stop, or force-recrawl (clears hot cache first)
- **Crawl Log** — live log of crawl progress
- **Cache Management** — view stats, delete individual URLs, clear hot cache
- **Auto-Refresh** — toggle button for live updates via XHR

---

## How It Works

```
Browser  ──HTTP Proxy──>  Proxy (port 8888)  ──>  Redis (curated/hot)
                                │                        │
                                └── Wayback Machine ─────┘
                                     (cache miss)

Browser  ──HTTP──>  Admin Service (port 8080)
                         ├── config.yaml (read/write)
                         ├── Redis (cache, crawl, seeds)
                         └── Pub/Sub reload ──> Proxy
```

The proxy is a raw asyncio TCP server that speaks HTTP. When a request comes in:

1. Check the **allowlist** (if enabled) -- reject URLs not on the list
2. Check **Redis cache** -- curated tier first (permanent, crawler-managed), then hot tier (auto-expires after 7 days)
3. On cache miss, **fetch from the Wayback Machine** for the configured target date
4. **Transform the content** -- strip the Wayback toolbar, remove injected scripts, fix asset URLs and links back to their original form
5. **Store in hot cache** for next time
6. **Inject the header bar** (if enabled) and **throttle the response** to simulate period-accurate connection speeds

The header bar is injected *after* the cache lookup, so cached pages don't need invalidation when you change header bar settings.

### Two-Tier Cache

- **Curated** -- permanent entries managed by the admin interface and prefetch crawler. These survive Redis restarts (with AOF persistence) and represent your vetted, exhibition-ready content.
- **Hot** -- auto-populated on cache miss, expires after 7 days (configurable). Acts as a working cache for pages visitors discover on their own.

### Project Structure

```
proxy/                          # The proxy server
├── wayback_proxy/
│   ├── __main__.py             # CLI entry point
│   ├── config.py               # Dataclass config (YAML + env + CLI)
│   ├── server.py               # Async TCP server, request routing
│   ├── cache.py                # Redis two-tier cache
│   ├── admin.py                # Built-in /_admin/ interface
│   ├── crawler.py              # Async prefetch spider
│   ├── throttle.py             # Modem speed throttling
│   └── wayback/
│       ├── client.py           # Wayback Machine HTTP client
│       └── transformer.py      # Content cleanup (toolbar, URLs, scripts)
├── error_pages/                # Error page templates
├── landing_page/               # Landing page template
└── Dockerfile

admin_service/                  # Remote admin UI (FastAPI + htmx)
├── admin_service/
│   ├── __main__.py             # Uvicorn entry point
│   ├── app.py                  # FastAPI app, auth middleware
│   ├── routes/                 # Dashboard, config editor, cache browser, crawler
│   ├── templates/              # Jinja2 + htmx templates
│   └── static/                 # Dark theme CSS
└── Dockerfile
```

---

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## License

MIT License — see [LICENSE](LICENSE)

---

## Developed at

This project is developed at [ZKM | Center for Art and Media Karlsruhe](https://zkm.de), a publicly funded cultural institution exploring the intersection of art, science, and technology.

<a href="https://zkm.de">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/zkmkarlsruhe/zkm-open-source/main/assets/zkm-logo-light.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/zkmkarlsruhe/zkm-open-source/main/assets/zkm-logo.svg">
    <img alt="ZKM" src="https://raw.githubusercontent.com/zkmkarlsruhe/zkm-open-source/main/assets/zkm-logo.svg" width="120">
  </picture>
</a>

Copyright (c) 2026 ZKM | Karlsruhe
