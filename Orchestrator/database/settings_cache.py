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
Settings cache – caches database settings in memory with optional Redis backing.
Reduces database load by caching frequently-accessed settings.
"""

import logging

log = logging.getLogger("hivemind.settings_cache")

_settings_cache = {}


def get_setting_cached(key: str) -> str:
    """Get a setting value, using Redis/in-memory cache first, then DB fallback."""
    from redis_client import get_cached

    cached = get_cached(f"setting:{key}")
    if cached is not None:
        return cached

    from database import get_setting
    val = get_setting(key)
    if val is not None:
        from redis_client import set_cached
        set_cached(f"setting:{key}", val)
    return val


def invalidate_setting(key: str):
    """Invalidate a cached setting after update."""
    from redis_client import invalidate_cached
    invalidate_cached(f"setting:{key}")


def get_max_agents_cached() -> int:
    """Get max_agents with caching."""
    val = get_setting_cached("max_agents")
    return int(val) if val else 3


def get_polling_interval_cached() -> int:
    """Get polling_interval_seconds with caching."""
    val = get_setting_cached("polling_interval_seconds")
    return int(val) if val else 5