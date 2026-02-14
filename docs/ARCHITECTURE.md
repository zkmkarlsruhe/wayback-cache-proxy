# Wayback Flow - Design Document

## Overview

**Wayback Flow** is a media art historian tool that serves archived web pages from the Internet Archive Wayback Machine, making old web art functional by providing period-accurate websites.

## Use Case

- Fetch archived pages from Wayback Machine for specific dates
- Cache results for performance and offline use
- Provide curated, historian-approved content for exhibitions/research
- Make old web art pieces work with their original web dependencies

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Compose Stack                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │   Proxy     │    │    Redis    │    │     Admin UI        │ │
│  │   :8888     │◄──►│    :6379    │◄──►│       :8080         │ │
│  │             │    │             │    │                     │ │
│  │  - HTTP/S   │    │  - Curated  │    │  - Dashboard        │ │
│  │  - MITM CA  │    │  - Hot data │    │  - Cache viewer     │ │
│  │  - Transform│    │  - Allow    │    │  - URL browser      │ │
│  │             │    │    lists    │    │  - Crawl manager    │ │
│  └─────────────┘    └─────────────┘    └─────────────────────┘ │
│         │                                        │              │
│         ▼                                        ▼              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    Wayback Machine API                      ││
│  │               (web.archive.org/web/...)                     ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Proxy Server

**Purpose:** HTTP/HTTPS proxy that intercepts requests and fetches from Wayback Machine.

**Features:**
- Listen on configurable port (default: 8888)
- MITM HTTPS with generated CA certificate
- Single target date per instance
- Future: multiple date endpoints (e.g., `/1999/`, `/2001/`)

**Request Flow:**
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
│ Allow List  │───►│   REJECT    │ (if not allowed)
│   Check     │    └─────────────┘
└─────────────┘
      │ (allowed)
      ▼
┌─────────────┐    ┌─────────────┐
│ Check Redis │───►│   RETURN    │ (cache hit)
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
│ Transform   │
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
│   RETURN    │
│  to client  │
└─────────────┘
```

### 2. Redis Cache

**Purpose:** Store cached content with two tiers.

**Data Structure:**
```
curated:{url_hash}     # Permanent, historian-approved
hot:{url_hash}         # Temporary, auto-fetched
allowlist:urls         # Set of allowed URL patterns
config:date            # Current target date
config:settings        # Other settings
```

**Tiers:**
| Tier | Purpose | TTL | Management |
|------|---------|-----|------------|
| `curated:` | Pre-fetched, approved content | None (permanent) | Admin UI |
| `hot:` | On-demand fetches | Configurable (e.g., 7 days) | Auto-expire |

### 3. Admin UI

**Purpose:** Web interface for configuration and management.

**Features:**
- **Dashboard** - Stats, status, current config
- **Cache Viewer** - Browse curated and hot cache entries
- **URL Browser** - Preview URLs as they would appear through proxy
- **Crawl Manager** - Import URLs, run crawls, manage curated content
- **Settings** - Date, allow-list, transforms, cache policies

**Tech:** FastAPI backend + Vue/React frontend (or simple Jinja templates)

---

## Configuration

### Proxy Config
```yaml
proxy:
  host: "0.0.0.0"
  port: 8888
  target_date: "20010915"      # YYYYMMDD
  date_tolerance_days: 365     # Accept snapshots within range

https:
  enabled: true
  ca_cert: "/certs/ca.crt"
  ca_key: "/certs/ca.key"
```

### Cache Config
```yaml
cache:
  redis_url: "redis://redis:6379/0"
  hot_ttl_days: 7              # Auto-expire hot data

access:
  mode: "open"                 # open | allowlist
  allowlist_key: "allowlist:urls"
```

### Transform Config
```yaml
transform:
  remove_wayback_toolbar: true
  remove_wayback_scripts: true
  fix_base_tags: true
  fix_asset_urls: true
  normalize_links: true
```

---

## Docker Compose

```yaml
version: "3.8"

services:
  proxy:
    build: ./proxy
    ports:
      - "8888:8888"
    environment:
      - REDIS_URL=redis://redis:6379/0
      - TARGET_DATE=20010915
    volumes:
      - ./certs:/certs:ro
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

  admin:
    build: ./admin
    ports:
      - "8080:8080"
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis

volumes:
  redis_data:
```

---

## Module Structure

```
wayback-flow/
├── proxy/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── wayback_proxy/
│       ├── __init__.py
│       ├── __main__.py          # CLI entry
│       ├── config.py            # Configuration
│       ├── server.py            # HTTP/HTTPS proxy server
│       ├── handler.py           # Request handler
│       ├── cache.py             # Redis cache interface
│       ├── wayback/
│       │   ├── client.py        # Wayback API client
│       │   ├── availability.py  # Availability API
│       │   └── transformer.py   # Content transforms
│       └── https/
│           ├── ca.py            # CA certificate management
│           └── mitm.py          # MITM handler
│
├── admin/
│   ├── Dockerfile
│   ├── pyproject.toml
│   └── wayback_admin/
│       ├── __init__.py
│       ├── __main__.py
│       ├── app.py               # FastAPI app
│       ├── routes/
│       │   ├── dashboard.py
│       │   ├── cache.py
│       │   ├── crawl.py
│       │   └── settings.py
│       ├── services/
│       │   ├── redis.py
│       │   └── crawler.py
│       └── templates/           # Jinja2 or static Vue
│
├── docker-compose.yml
├── docs/
│   ├── DESIGN.md
│   └── USAGE.md
└── research/                    # Reference implementations
```

---

## API Endpoints (Admin)

### Dashboard
- `GET /api/stats` - Cache stats, request counts
- `GET /api/status` - Service health

### Cache
- `GET /api/cache/curated` - List curated entries
- `GET /api/cache/hot` - List hot entries
- `POST /api/cache/curated` - Add to curated
- `DELETE /api/cache/hot` - Clear hot cache
- `GET /api/cache/preview?url=...` - Preview URL content

### Crawl
- `POST /api/crawl/urls` - Import URL list
- `POST /api/crawl/start` - Start crawl from seed
- `GET /api/crawl/status` - Crawl progress

### Settings
- `GET /api/settings` - Current config
- `PUT /api/settings` - Update config
- `GET /api/allowlist` - Get allowed URLs
- `PUT /api/allowlist` - Update allow list

---

## Content Transformations

### 1. Remove Wayback Toolbar
```python
# Remove toolbar HTML
re.sub(r'<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*<!-- END WAYBACK TOOLBAR INSERT -->', '', html, flags=re.S)
```

### 2. Remove Wayback Scripts
```python
# Remove injected scripts
re.sub(r'<script src="[^"]*/_static/js/.*</script>', '', html, flags=re.S)
```

### 3. Fix Base Tags
```python
# Fix base href pointing to web.archive.org
re.sub(r'(<base\s+[^>]*href=["\']?)(?:https?:)?//web.archive.org/web/[^/]+/', r'\1http://', html)
```

### 4. Fix Asset URLs
```python
# Convert /web/20010915im_/http://... to http://...
re.sub(r'/web/\d+[a-z_]*/([^"\']+)', r'\1', html)
```

### 5. Normalize Links
```python
# Remove web.archive.org prefix from links
re.sub(r'(?:https?:)?//web.archive.org/web/\d+/', '', html)
```

---

## Next Steps

1. **Set up project structure** - Poetry projects for proxy and admin
2. **Implement core proxy** - Basic HTTP proxy with Wayback fetch
3. **Add Redis caching** - Two-tier cache system
4. **Add HTTPS MITM** - CA generation and certificate handling
5. **Build Admin API** - FastAPI backend
6. **Build Admin UI** - Dashboard and management interface
7. **Add crawling** - URL import and recursive fetch
8. **Docker Compose** - Full stack deployment
