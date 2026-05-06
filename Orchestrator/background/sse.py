# Copyright 2025 Mael Klingler
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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