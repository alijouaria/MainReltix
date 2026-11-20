"""In-process pub/sub for server-sent events."""
import asyncio
import json
from typing import AsyncIterator

_subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []


def publish(event: dict) -> None:
    """Thread-safe fan-out to every live subscriber."""
    data = json.dumps(event, default=str)
    for loop, queue in list(_subscribers):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, data)
        except RuntimeError:  # loop closed
            pass


async def subscribe() -> AsyncIterator[str]:
    """Yield SSE-formatted frames until the client disconnects."""
    entry = (asyncio.get_running_loop(), asyncio.Queue())
    _subscribers.append(entry)
    try:
        yield "event: hello\ndata: {}\n\n"
        while True:
            data = await entry[1].get()
            yield f"data: {data}\n\n"
    finally:
        _subscribers.remove(entry)
