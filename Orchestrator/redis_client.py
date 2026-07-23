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
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Redis client – connection pooling, Pub/Sub, distributed locks, rate limiting,
and caching with graceful fallback when Redis is unavailable.
"""

import json
import logging
import time
import threading
from typing import Dict, List, Optional, Tuple

from config import REDIS_URL, REDIS_ENABLED, REDIS_LOCK_TIMEOUT, REDIS_SETTINGS_TTL

log = logging.getLogger("hivemind.redis")

_redis = None
_redis_available = False
_pubsub_thread = None
_subscriptions: Dict[str, list] = {}
_lock = threading.Lock()


def _get_redis():
    """Lazy-init Redis connection. Returns None if Redis is unavailable."""
    global _redis, _redis_available
    if not REDIS_ENABLED:
        return None
    if _redis is not None and _redis_available:
        try:
            _redis.ping()
            return _redis
        except Exception:
            _redis_available = False
            _redis = None
    try:
        import redis as rlib
        _redis = rlib.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
        _redis.ping()
        _redis_available = True
        log.info(f"Redis connected: {REDIS_URL.split('@')[-1] if '@' in REDIS_URL else REDIS_URL}")
        return _redis
    except ImportError:
        log.warning("redis package not installed, falling back to in-memory")
        _redis_available = False
        return None
    except Exception as e:
        log.warning(f"Redis unavailable: {e}, falling back to in-memory")
        _redis_available = False
        _redis = None
        return None


def is_available() -> bool:
    """Check if Redis is connected and responsive."""
    r = _get_redis()
    if r is None:
        return False
    try:
        r.ping()
        return True
    except Exception:
        return False


# ── Pub/Sub ────────────────────────────────────────────

def publish(channel: str, data: dict) -> int:
    """Publish a message to a Redis channel. Returns number of receivers or 0."""
    r = _get_redis()
    if r is None:
        return 0
    try:
        return r.publish(channel, json.dumps(data))
    except Exception as e:
        log.warning(f"Redis publish failed: {e}")
        return 0


def subscribe(channel: str, callback):
    """Subscribe to a Redis channel. Callback receives dict data."""
    r = _get_redis()
    if r is None:
        return
    try:
        pubsub = r.pubsub()
        pubsub.subscribe(channel)
        for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                    callback(data)
                except (json.JSONDecodeError, TypeError):
                    pass
    except Exception as e:
        log.warning(f"Redis subscribe failed: {e}")


# ── Distributed Lock ────────────────────────────────────

def acquire_lock(name: str, timeout: int = None) -> bool:
    """Acquire a distributed lock. Returns True if lock was obtained."""
    if timeout is None:
        timeout = REDIS_LOCK_TIMEOUT
    r = _get_redis()
    if r is None:
        return True  # Fallback: always acquire if no Redis
    try:
        result = r.set(name, "locked", nx=True, ex=timeout)
        return result is True or result == 1
    except Exception as e:
        log.warning(f"Redis acquire_lock failed: {e}")
        return True  # Fallback: allow operation


def release_lock(name: str):
    """Release a distributed lock."""
    r = _get_redis()
    if r is None:
        return
    try:
        r.delete(name)
    except Exception as e:
        log.warning(f"Redis release_lock failed: {e}")


def renew_lock(name: str, timeout: int = None) -> bool:
    """Renew an existing lock. Returns True if lock still held."""
    if timeout is None:
        timeout = REDIS_LOCK_TIMEOUT
    r = _get_redis()
    if r is None:
        return True
    try:
        result = r.expire(name, timeout)
        return bool(result)
    except Exception as e:
        log.warning(f"Redis renew_lock failed: {e}")
        return False


# ── Caching ─────────────────────────────────────────────

_cache: Dict[str, Tuple[float, str]] = {}
_cache_lock = threading.Lock()


def get_cached(key: str) -> Optional[str]:
    """Get a cached value. Checks Redis first, then in-memory cache."""
    r = _get_redis()
    if r is not None:
        try:
            val = r.get(f"hivemind:cache:{key}")
            if val is not None:
                return val
        except Exception as e:
            log.warning(f"Redis get_cached failed: {e}")

    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None:
            ts, val = entry
            if time.time() - ts < REDIS_SETTINGS_TTL:
                return val
            del _cache[key]
    return None


def set_cached(key: str, value: str, ttl: int = None):
    """Set a cached value in both Redis and in-memory cache."""
    if ttl is None:
        ttl = REDIS_SETTINGS_TTL
    r = _get_redis()
    if r is not None:
        try:
            r.setex(f"hivemind:cache:{key}", ttl, value)
        except Exception as e:
            log.warning(f"Redis set_cached failed: {e}")

    with _cache_lock:
        _cache[key] = (time.time(), value)


def invalidate_cached(key: str):
    """Invalidate a cached value in both Redis and in-memory cache."""
    r = _get_redis()
    if r is not None:
        try:
            r.delete(f"hivemind:cache:{key}")
        except Exception as e:
            log.warning(f"Redis invalidate_cached failed: {e}")

    with _cache_lock:
        _cache.pop(key, None)


# ── Rate Limiting ───────────────────────────────────────

_rate_windows: Dict[str, List[float]] = {}
_rate_lock = threading.Lock()
_RATE_LIMIT_MAX_ENTRIES = 50000


def is_rate_limited(key: str, max_requests: int, window_seconds: int = 60) -> Tuple[bool, int]:
    """Check if a key is rate limited. Returns (is_limited, remaining_requests).

    Uses Redis sliding window when available, in-memory fallback otherwise.
    """
    r = _get_redis()
    if r is not None:
        try:
            now = time.time()
            window_key = f"hivemind:ratelimit:{key}"
            pipe = r.pipeline()
            pipe.zremrangebyscore(window_key, 0, now - window_seconds)
            pipe.zadd(window_key, {str(now): now})
            pipe.zcard(window_key)
            pipe.expire(window_key, window_seconds + 1)
            results = pipe.execute()
            count = results[2]
            remaining = max(0, max_requests - count)
            return count > max_requests, remaining
        except Exception as e:
            log.warning(f"Redis rate_limit failed: {e}")

    now = time.time()
    with _rate_lock:
        if key not in _rate_windows:
            _rate_windows[key] = []
        _rate_windows[key] = [ts for ts in _rate_windows[key] if now - ts < window_seconds]
        if len(_rate_windows) > _RATE_LIMIT_MAX_ENTRIES:
            _rate_windows.clear()
        _rate_windows[key].append(now)
        count = len(_rate_windows[key])
        remaining = max(0, max_requests - count)
        return count > max_requests, remaining