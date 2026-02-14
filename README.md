# Wayback Cache Proxy

[![ZKM](https://img.shields.io/badge/ZKM-Karlsruhe-blue)](https://zkm.de)
[![ZKM Open Source](https://img.shields.io/badge/ZKM-Open%20Source-blue)](https://github.com/zkmkarlsruhe)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> Browse the old web through a caching Wayback Machine proxy. Redis-backed two-tier cache, admin interface, prefetch crawler, modem speed throttling, and header bar overlay. Built for museums and media art exhibitions.

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

# Run the proxy
python -m wayback_proxy --port 8888 --date 20010911 --header-bar --admin
```

Then configure your browser's HTTP proxy to `localhost:8888` and browse any URL.

### Docker

```bash
docker-compose up
```

---

## CLI Options

| Flag | Default | Description |
|------|---------|-------------|
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

---

## Admin Interface

Access at `http://proxy-host:port/_admin/` (with Basic Auth if configured).

- **Crawl Seeds** — add URLs with depth for prefetch crawling
- **Crawl Control** — start, stop, or force-recrawl (clears hot cache first)
- **Crawl Log** — live log of crawl progress
- **Cache Management** — view stats, delete individual URLs, clear hot cache
- **Auto-Refresh** — toggle button for live updates via XHR

---

## Architecture

```
Browser  ──HTTP Proxy──>  Wayback Cache Proxy  ──>  Redis (curated/hot)
                                │                         │
                                └──── Wayback Machine ────┘
                                      (cache miss)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed design.

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
