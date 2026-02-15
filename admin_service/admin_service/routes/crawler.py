"""Crawler management routes — seeds, crawl control, live log."""

import json
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/crawler")


@router.get("/", response_class=HTMLResponse)
async def crawler_page(request: Request):
    redis = request.app.state.redis

    # Get seeds
    raw_seeds = await redis.hgetall("crawl:seeds")
    seeds = [
        (k.decode() if isinstance(k, bytes) else k,
         int(v.decode() if isinstance(v, bytes) else v))
        for k, v in raw_seeds.items()
    ]
    seeds.sort(key=lambda s: s[0])

    # Get status
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

    # Get log
    raw_log = await redis.lrange("crawl:log", 0, 99)
    log_lines = [l.decode() if isinstance(l, bytes) else l for l in raw_log]

    templates = request.app.state.templates
    return templates.TemplateResponse("crawler.html", {
        "request": request,
        "seeds": seeds,
        "state": state,
        "progress": progress,
        "log_lines": log_lines,
    })


@router.get("/log-partial", response_class=HTMLResponse)
async def crawler_log_partial(request: Request):
    """Partial HTML for htmx log polling."""
    redis = request.app.state.redis

    # Status
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

    # Log
    raw_log = await redis.lrange("crawl:log", 0, 99)
    log_lines = [l.decode() if isinstance(l, bytes) else l for l in raw_log]

    templates = request.app.state.templates
    return templates.TemplateResponse("partials/crawler_live.html", {
        "request": request,
        "state": state,
        "progress": progress,
        "log_lines": log_lines,
    })


@router.post("/add-seed")
async def add_seed(request: Request, url: str = Form(...), depth: int = Form(1)):
    redis = request.app.state.redis
    url = url.strip()
    if url:
        await redis.hset("crawl:seeds", url, str(max(0, depth)))
    return RedirectResponse("/crawler/", status_code=303)


@router.post("/remove-seed")
async def remove_seed(request: Request, url: str = Form(...)):
    redis = request.app.state.redis
    await redis.hdel("crawl:seeds", url)
    return RedirectResponse("/crawler/", status_code=303)


@router.post("/start")
async def start_crawl(request: Request):
    redis = request.app.state.redis
    crawl_data = await redis.hgetall("crawl:status")
    raw_state = crawl_data.get(b"state", b"idle") if crawl_data else b"idle"
    state = raw_state.decode() if isinstance(raw_state, bytes) else raw_state
    if state != "running":
        await redis.hset("crawl:status", mapping={
            "state": "pending_start",
            "progress": json.dumps({}),
        })
    return RedirectResponse("/crawler/", status_code=303)


@router.post("/stop")
async def stop_crawl(request: Request):
    redis = request.app.state.redis
    crawl_data = await redis.hgetall("crawl:status")
    if crawl_data:
        raw_progress = crawl_data.get(b"progress", b"{}")
        if isinstance(raw_progress, bytes):
            raw_progress = raw_progress.decode()
        await redis.hset("crawl:status", mapping={
            "state": "stopping",
            "progress": raw_progress,
        })
    return RedirectResponse("/crawler/", status_code=303)


@router.post("/recrawl")
async def recrawl(request: Request):
    """Clear hot cache and start a fresh crawl."""
    redis = request.app.state.redis
    # Clear hot cache
    cursor = b"0"
    while True:
        cursor, keys = await redis.scan(cursor, match="hot:*", count=100)
        if keys:
            await redis.delete(*keys)
        if cursor == b"0" or cursor == 0:
            break
    # Signal start
    await redis.hset("crawl:status", mapping={
        "state": "pending_start",
        "progress": json.dumps({}),
    })
    return RedirectResponse("/crawler/", status_code=303)


@router.post("/clear-log")
async def clear_log(request: Request):
    redis = request.app.state.redis
    await redis.delete("crawl:log")
    return RedirectResponse("/crawler/", status_code=303)
