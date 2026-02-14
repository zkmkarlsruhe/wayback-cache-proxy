"""Bandwidth throttling to simulate period-accurate connection speeds."""

import asyncio

SPEED_TIERS = {
    "14.4k": 1800,     # 14.4 kbps modem  -> ~1800 bytes/sec
    "28.8k": 3600,     # 28.8 kbps modem  -> ~3600 bytes/sec
    "56k":   7000,     # 56 kbps modem    -> ~7000 bytes/sec
    "isdn":  16000,    # 128 kbps ISDN    -> ~16000 bytes/sec
    "dsl":   125000,   # 1 Mbps early DSL -> ~125000 bytes/sec
    "none":  0,        # unlimited
}


async def write_throttled(
    writer: asyncio.StreamWriter,
    data: bytes,
    speed: str,
) -> None:
    """Write data to stream, throttled to the given speed tier.

    Args:
        writer: The asyncio stream writer.
        data: Bytes to send.
        speed: Key from SPEED_TIERS (e.g. "56k", "none").
    """
    bytes_per_sec = SPEED_TIERS.get(speed, 0)

    if bytes_per_sec == 0 or not data:
        # Unlimited or nothing to send
        writer.write(data)
        await writer.drain()
        return

    # Send in 100ms chunks
    chunk_size = max(1, bytes_per_sec // 10)
    offset = 0

    while offset < len(data):
        end = min(offset + chunk_size, len(data))
        writer.write(data[offset:end])
        await writer.drain()
        offset = end
        if offset < len(data):
            await asyncio.sleep(0.1)
