"""WARC export — write cache contents as .warc.gz, diff against existing WARCs."""

import gzip
import io
import json
import re
import uuid
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import BinaryIO, Optional, Set

from .cache import Cache, CachedResponse


def _ts_to_iso(ts: str) -> str:
    """Convert YYYYMMDD[HHmmss] timestamp to ISO 8601."""
    ts = ts.ljust(14, "0")  # pad to 14 digits
    try:
        dt = datetime(
            int(ts[0:4]), int(ts[4:6]), int(ts[6:8]),
            int(ts[8:10]), int(ts[10:12]), int(ts[12:14]),
            tzinfo=timezone.utc,
        )
    except (ValueError, IndexError):
        dt = datetime(2001, 1, 1, tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_http_response_block(resp: CachedResponse) -> bytes:
    """Build the HTTP response payload for a WARC response record."""
    status_line = f"HTTP/1.1 {resp.status_code} OK\r\n"
    headers = f"Content-Type: {resp.content_type}\r\n"
    for key, value in resp.headers.items():
        lower = key.lower()
        # Skip hop-by-hop and already-emitted headers
        if lower in ("content-type", "transfer-encoding", "connection",
                      "content-encoding", "content-length"):
            continue
        headers += f"{key}: {value}\r\n"
    headers += f"Content-Length: {len(resp.content)}\r\n"

    block = status_line.encode() + headers.encode() + b"\r\n" + resp.content
    return block


def _build_warc_record(resp: CachedResponse) -> bytes:
    """Build a single WARC/1.0 response record (uncompressed)."""
    record_id = f"<urn:uuid:{uuid.uuid4()}>"
    warc_date = _ts_to_iso(resp.timestamp)

    http_block = _build_http_response_block(resp)

    header = (
        f"WARC/1.0\r\n"
        f"WARC-Type: response\r\n"
        f"WARC-Target-URI: {resp.archived_url}\r\n"
        f"WARC-Date: {warc_date}\r\n"
        f"WARC-Record-ID: {record_id}\r\n"
        f"Content-Type: application/http; msgtype=response\r\n"
        f"Content-Length: {len(http_block)}\r\n"
        f"\r\n"
    )

    return header.encode() + http_block + b"\r\n\r\n"


async def _scan_cache_entries(
    cache: Cache, tier: str, filter_pattern: str = "",
) -> list[CachedResponse]:
    """Scan Redis for cached responses in the specified tier(s)."""
    client = cache._client
    prefixes = []
    if tier in ("curated", "both"):
        prefixes.append(cache.curated_prefix)
    if tier in ("hot", "both"):
        prefixes.append(cache.hot_prefix)

    entries: list[CachedResponse] = []
    seen_urls: set[str] = set()

    for prefix in prefixes:
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor, match=f"{prefix}*", count=100)
            if keys:
                values = await client.mget(keys)
                for val in values:
                    if val is None:
                        continue
                    try:
                        resp = CachedResponse.from_dict(json.loads(val))
                    except (json.JSONDecodeError, KeyError):
                        continue

                    # Dedup across tiers
                    if resp.archived_url in seen_urls:
                        continue
                    seen_urls.add(resp.archived_url)

                    # Apply URL filter
                    if filter_pattern and not fnmatch(resp.archived_url, filter_pattern):
                        continue

                    entries.append(resp)
            if cursor == 0:
                break

    return entries


class WarcWriter:
    """Export Redis cache contents as WARC files."""

    def __init__(self, cache: Cache):
        self.cache = cache

    async def export(
        self,
        output: BinaryIO,
        tier: str = "both",
        filter_pattern: str = "",
    ) -> int:
        """Export cache entries as a .warc.gz file.

        Each WARC record is individually gzip-compressed per the .warc.gz spec.
        Returns the number of records written.
        """
        entries = await _scan_cache_entries(self.cache, tier, filter_pattern)

        count = 0
        for resp in entries:
            record = _build_warc_record(resp)
            compressed = gzip.compress(record)
            output.write(compressed)
            count += 1

        return count

    async def diff(
        self, warc_data: bytes, tier: str = "both",
    ) -> dict:
        """Diff cache against an existing WARC file.

        Returns {only_in_cache: [...], only_in_warc: [...], in_both: int}.
        """
        warc_urls = _extract_warc_urls(warc_data)
        cache_entries = await _scan_cache_entries(self.cache, tier)
        cache_urls = {e.archived_url for e in cache_entries}

        only_in_cache = sorted(cache_urls - warc_urls)
        only_in_warc = sorted(warc_urls - cache_urls)
        in_both = len(cache_urls & warc_urls)

        return {
            "only_in_cache": only_in_cache,
            "only_in_warc": only_in_warc,
            "in_both": in_both,
        }

    async def export_diff(
        self, warc_data: bytes, output: BinaryIO, tier: str = "both",
    ) -> int:
        """Export only cache entries NOT present in the given WARC file.

        Returns the number of records written.
        """
        warc_urls = _extract_warc_urls(warc_data)
        entries = await _scan_cache_entries(self.cache, tier)

        count = 0
        for resp in entries:
            if resp.archived_url in warc_urls:
                continue
            record = _build_warc_record(resp)
            compressed = gzip.compress(record)
            output.write(compressed)
            count += 1

        return count


# WARC-Target-URI extraction pattern
_RE_TARGET_URI = re.compile(rb"WARC-Target-URI:\s*(\S+)", re.IGNORECASE)


def _extract_warc_urls(warc_data: bytes) -> Set[str]:
    """Extract all WARC-Target-URI values from a .warc or .warc.gz file."""
    # Try to decompress if gzipped
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
        return data  # not gzipped

    # .warc.gz is concatenated gzip members — decompress all
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
