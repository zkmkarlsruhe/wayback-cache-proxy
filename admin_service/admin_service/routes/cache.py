"""Cache browsing and management routes."""

import json
import hashlib
from urllib.parse import urlparse
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/cache")


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    normalized = f"{parsed.scheme}://{host}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return normalized


def _url_hash(url: str) -> str:
    return hashlib.sha256(_normalize_url(url).encode()).hexdigest()[:16]


async def _scan_keys(redis, pattern: str, cursor=b"0", count: int = 100) -> list:
    """Scan Redis keys matching pattern."""
    keys = []
    while True:
        cursor, batch = await redis.scan(cursor, match=pattern, count=count)
        keys.extend(batch)
        if cursor == b"0" or cursor == 0:
            break
    return keys


async def _get_cache_entries(redis, prefix: str, search: str = "", page: int = 1, per_page: int = 50):
    """Get paginated cache entries."""
    keys = await _scan_keys(redis, f"{prefix}*")
    entries = []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            raw = await redis.get(key)
            if raw:
                data = json.loads(raw)
                url = data.get("archived_url", key_str)
                content_type = data.get("content_type", "")
                if search and search.lower() not in url.lower():
                    continue
                entries.append({
                    "key": key_str,
                    "url": url,
                    "content_type": content_type,
                    "timestamp": data.get("timestamp", ""),
                    "size": len(data.get("content", "")),
                })
        except (json.JSONDecodeError, Exception):
            continue

    entries.sort(key=lambda e: e["url"])
    total = len(entries)
    start = (page - 1) * per_page
    return entries[start:start + per_page], total


@router.get("/", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    search: str = "",
    page: int = 1,
    tier: str = "curated",
):
    redis = request.app.state.redis
    prefix = "curated:" if tier == "curated" else "hot:"

    entries, total = await _get_cache_entries(redis, prefix, search, page)
    total_pages = max(1, (total + 49) // 50)

    templates = request.app.state.templates
    return templates.TemplateResponse("cache.html", {
        "request": request,
        "entries": entries,
        "search": search,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "tier": tier,
    })


@router.post("/delete")
async def cache_delete(request: Request, url: str = Form(...), tier: str = Form("both")):
    redis = request.app.state.redis
    url_key = _url_hash(url)

    if tier in ("curated", "both"):
        await redis.delete(f"curated:{url_key}")
    if tier in ("hot", "both"):
        await redis.delete(f"hot:{url_key}")

    return RedirectResponse(request.headers.get("referer", "/cache/"), status_code=303)


@router.post("/clear-hot")
async def cache_clear_hot(request: Request):
    redis = request.app.state.redis
    keys = await _scan_keys(redis, "hot:*")
    if keys:
        await redis.delete(*keys)
    return RedirectResponse("/cache/?tier=hot", status_code=303)


@router.post("/clear-curated")
async def cache_clear_curated(request: Request):
    redis = request.app.state.redis
    keys = await _scan_keys(redis, "curated:*")
    if keys:
        await redis.delete(*keys)
    return RedirectResponse("/cache/?tier=curated", status_code=303)
