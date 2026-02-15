"""Dashboard route — overview stats."""

import json
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


async def _get_stats(redis) -> dict:
    """Gather cache and crawl stats from Redis."""
    # Cache counts
    curated_count = 0
    cursor = b"0"
    while True:
        cursor, keys = await redis.scan(cursor, match="curated:*", count=100)
        curated_count += len(keys)
        if cursor == b"0" or cursor == 0:
            break

    hot_count = 0
    cursor = b"0"
    while True:
        cursor, keys = await redis.scan(cursor, match="hot:*", count=100)
        hot_count += len(keys)
        if cursor == b"0" or cursor == 0:
            break

    # Allowlist count
    allowlist_count = await redis.scard("allowlist:urls")

    # Crawl status
    crawl_data = await redis.hgetall("crawl:status")
    state = "idle"
    progress = {}
    if crawl_data:
        raw_state = crawl_data.get(b"state", b"idle")
        state = raw_state.decode() if isinstance(raw_state, bytes) else raw_state
        raw_progress = crawl_data.get(b"progress", b"{}")
        if isinstance(raw_progress, bytes):
            raw_progress = raw_progress.decode()
        progress = json.loads(raw_progress)

    # Seed count
    seed_count = await redis.hlen("crawl:seeds")

    # Most viewed
    top_domains = await redis.zrevrange("views:urls", 0, 4, withscores=True)
    most_viewed = [
        (d.decode() if isinstance(d, bytes) else d, int(s))
        for d, s in top_domains
    ]

    return {
        "curated_count": curated_count,
        "hot_count": hot_count,
        "allowlist_count": allowlist_count,
        "crawl_state": state,
        "crawl_progress": progress,
        "seed_count": seed_count,
        "most_viewed": most_viewed,
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    redis = request.app.state.redis
    stats = await _get_stats(redis)

    templates = request.app.state.templates
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
    })


@router.get("/api/stats")
async def api_stats(request: Request):
    """JSON stats endpoint for htmx partial updates."""
    redis = request.app.state.redis
    return await _get_stats(redis)
