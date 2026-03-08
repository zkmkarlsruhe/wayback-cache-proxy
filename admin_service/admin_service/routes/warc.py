"""WARC export routes — export cache as .warc.gz, diff against existing WARCs."""

import gzip
import io
import json
import re
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Optional, Set

from fastapi import APIRouter, Request, Query, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response, StreamingResponse

router = APIRouter(prefix="/warc")


# ── WARC helpers ──────────────────────────────────────────────────────

def _ts_to_iso(ts: str) -> str:
    """Convert YYYYMMDD[HHmmss] timestamp to ISO 8601."""
    ts = ts.ljust(14, "0")
    try:
        dt = datetime(
            int(ts[0:4]), int(ts[4:6]), int(ts[6:8]),
            int(ts[8:10]), int(ts[10:12]), int(ts[12:14]),
            tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        dt = datetime(2001, 1, 1, tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_http_response_block(data: dict, content: bytes) -> bytes:
    """Build the HTTP response payload for a WARC response record."""
    status_code = data.get("status_code", 200)
    content_type = data.get("content_type", "application/octet-stream")
    headers = data.get("headers", {})

    status_line = f"HTTP/1.1 {status_code} OK\r\n"
    hdr_lines = f"Content-Type: {content_type}\r\n"
    for key, value in headers.items():
        lower = key.lower()
        if lower in ("content-type", "transfer-encoding", "connection",
                      "content-encoding", "content-length"):
            continue
        hdr_lines += f"{key}: {value}\r\n"
    hdr_lines += f"Content-Length: {len(content)}\r\n"

    return status_line.encode() + hdr_lines.encode() + b"\r\n" + content


def _build_warc_record(data: dict, content: bytes) -> bytes:
    """Build a single WARC/1.0 response record (uncompressed)."""
    record_id = f"<urn:uuid:{uuid.uuid4()}>"
    warc_date = _ts_to_iso(data.get("timestamp", "20010101"))
    target_uri = data.get("archived_url", "")

    http_block = _build_http_response_block(data, content)

    header = (
        f"WARC/1.0\r\n"
        f"WARC-Type: response\r\n"
        f"WARC-Target-URI: {target_uri}\r\n"
        f"WARC-Date: {warc_date}\r\n"
        f"WARC-Record-ID: {record_id}\r\n"
        f"Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(http_block)}\r\n"
        f"\r\n"
    )

    return header.encode() + http_block + b"\r\n\r\n"


async def _scan_keys(redis, pattern: str) -> list:
    """Scan all Redis keys matching pattern."""
    keys = []
    cursor = 0
    while True:
        cursor, batch = await redis.scan(cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    return keys


async def _scan_cache_entries(
    redis, tier: str, filter_pattern: str = "",
) -> list[dict]:
    """Scan Redis for cached response metadata + content.

    Returns list of dicts (raw JSON data with 'content' already base64-decoded).
    """
    import base64

    prefixes = []
    if tier in ("curated", "both"):
        prefixes.append("curated:")
    if tier in ("hot", "both"):
        prefixes.append("hot:")

    entries: list[dict] = []
    seen_urls: set[str] = set()

    for prefix in prefixes:
        keys = await _scan_keys(redis, f"{prefix}*")
        if not keys:
            continue
        # Batch fetch
        values = await redis.mget(keys)
        for val in values:
            if val is None:
                continue
            try:
                data = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                continue

            url = data.get("archived_url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)

            if filter_pattern and not fnmatch(url, filter_pattern):
                continue

            # Decode content from base64
            raw_content = data.get("content", "")
            try:
                data["_content_bytes"] = base64.b64decode(raw_content)
            except Exception:
                data["_content_bytes"] = raw_content.encode() if isinstance(raw_content, str) else b""

            entries.append(data)

    return entries


_RE_TARGET_URI = re.compile(rb"WARC-Target-URI:\s*(\S+)", re.IGNORECASE)


def _extract_warc_urls(warc_data: bytes) -> Set[str]:
    """Extract all WARC-Target-URI values from a .warc or .warc.gz file."""
    raw = _decompress_warc(warc_data)
    urls: Set[str] = set()
    for match in _RE_TARGET_URI.finditer(raw):
        try:
            urls.add(match.group(1).decode("utf-8", errors="replace"))
        except Exception:
            continue
    return urls


def _decompress_warc(data: bytes) -> bytes:
    """Decompress .warc.gz (concatenated gzip members) or return as-is."""
    if not data[:2] == b"\x1f\x8b":
        return data

    result = io.BytesIO()
    stream = io.BytesIO(data)
    while True:
        pos = stream.tell()
        if pos >= len(data):
            break
        try:
            with gzip.GzipFile(fileobj=stream) as gz:
                result.write(gz.read())
        except (EOFError, OSError):
            break
    return result.getvalue()


# ── Routes ────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def warc_page(request: Request):
    """WARC export landing page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("warc.html", {"request": request})


@router.get("/export")
async def warc_export(
    request: Request,
    tier: str = Query("both"),
    filter: str = Query(""),
):
    """Export cache as .warc.gz download."""
    redis = request.app.state.redis
    entries = await _scan_cache_entries(redis, tier, filter)

    buf = io.BytesIO()
    count = 0
    for data in entries:
        record = _build_warc_record(data, data["_content_bytes"])
        compressed = gzip.compress(record)
        buf.write(compressed)
        count += 1

    filename = "wayback-export.warc.gz"
    if filter:
        safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', filter)[:40]
        filename = f"wayback-export-{safe}.warc.gz"

    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-WARC-Record-Count": str(count),
        },
    )


@router.post("/diff", response_class=HTMLResponse)
async def warc_diff(
    request: Request,
    warc: UploadFile = File(...),
    tier: str = Form("both"),
):
    """Diff cache against an uploaded WARC file."""
    redis = request.app.state.redis
    warc_data = await warc.read()

    warc_urls = _extract_warc_urls(warc_data)
    entries = await _scan_cache_entries(redis, tier)
    cache_urls = {e.get("archived_url", "") for e in entries}

    only_in_cache = sorted(cache_urls - warc_urls)
    only_in_warc = sorted(warc_urls - cache_urls)
    in_both = len(cache_urls & warc_urls)

    templates = request.app.state.templates
    return templates.TemplateResponse("warc_diff.html", {
        "request": request,
        "warc_filename": warc.filename,
        "tier": tier,
        "only_in_cache": only_in_cache,
        "only_in_warc": only_in_warc,
        "in_both": in_both,
    })


@router.post("/export-diff")
async def warc_export_diff(
    request: Request,
    warc: UploadFile = File(...),
    tier: str = Form("both"),
):
    """Export only cache entries NOT in the uploaded WARC (delta export)."""
    redis = request.app.state.redis
    warc_data = await warc.read()

    warc_urls = _extract_warc_urls(warc_data)
    entries = await _scan_cache_entries(redis, tier)

    buf = io.BytesIO()
    count = 0
    for data in entries:
        url = data.get("archived_url", "")
        if url in warc_urls:
            continue
        record = _build_warc_record(data, data["_content_bytes"])
        compressed = gzip.compress(record)
        buf.write(compressed)
        count += 1

    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": 'attachment; filename="wayback-delta.warc.gz"',
            "X-WARC-Record-Count": str(count),
        },
    )
