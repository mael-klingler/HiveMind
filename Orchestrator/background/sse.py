# Copyright 2026 Mael Klingler
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS of ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
SSE broadcast and generator for live updates.
Supports Redis Pub/Sub for cross-replica event broadcasting.
Falls back to in-process only when Redis is unavailable.
"""

import asyncio
import json
import logging
import threading
from typing import AsyncGenerator, Dict, List

log = logging.getLogger("hivemind.sse")

clients: List[asyncio.Queue] = []
_redis_subscriber_started = False


async def broadcast_event(event: str, data: Dict):
    """Broadcast an SSE event to all local clients and via Redis Pub/Sub."""
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"

    # Local broadcast
    disconnected = []
    for q in clients:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            disconnected.append(q)
    for dq in disconnected:
        if dq in clients:
            clients.remove(dq)

    # Redis broadcast (for other replicas)
    try:
        from redis_client import publish
        publish("hivemind:events", {"event": event, "data": data})
    except Exception as e:
        log.debug(f"Redis publish skipped: {e}")


def _redis_message_handler(message: dict):
    """Handle messages from Redis Pub/Sub and push to local SSE clients."""
    try:
        event = message.get("event", "")
        data = message.get("data", {})
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in clients:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass
    except Exception as e:
        log.warning(f"Redis message handler error: {e}")


def _start_redis_subscriber():
    """Start Redis subscriber in a background thread."""
    global _redis_subscriber_started
    if _redis_subscriber_started:
        return

    try:
        import redis as rlib
        from config import REDIS_URL
        r = rlib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5)
        r.ping()
        pubsub = r.pubsub()
        pubsub.subscribe("hivemind:events")

        _redis_subscriber_started = True
        log.info("Redis SSE subscriber started")

        for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    _redis_message_handler(data)
                except (json.JSONDecodeError, TypeError):
                    pass
    except ImportError:
        log.debug("redis package not installed, Redis SSE subscriber not started")
    except Exception as e:
        log.warning(f"Redis SSE subscriber failed: {e}")


def start_redis_subscriber():
    """Start the Redis subscriber thread if Redis is available."""
    from config import REDIS_ENABLED
    if not REDIS_ENABLED:
        return
    t = threading.Thread(target=_start_redis_subscriber, daemon=True, name="redis-sse-subscriber")
    t.start()


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


__all__ = ["broadcast_event", "sse_generator", "clients", "start_redis_subscriber"]