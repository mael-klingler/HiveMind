"""
SSE broadcast and generator for live updates.
"""

import asyncio
import json
from typing import AsyncGenerator, Dict, List

clients: List[asyncio.Queue] = []


async def broadcast_event(event: str, data: Dict):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    disconnected = []
    for q in clients:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            disconnected.append(q)
    for dq in disconnected:
        if dq in clients:
            clients.remove(dq)


async def sse_generator() -> AsyncGenerator[str, None]:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    clients.append(q)
    try:
        await q.put(f"event: init\ndata: {json.dumps({'message': 'connected'})}\n\n")
        while True:
            msg = await q.get()
            yield msg
    finally:
        if q in clients:
            clients.remove(q)


__all__ = ["broadcast_event", "sse_generator", "clients"]