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
FastAPI middleware: CORS, API key auth, rate limiting, correlation ID.
"""

import os
import time as _time
import uuid as _uuid
from typing import Dict

from fastapi import Request

from config import HIVEMIND_API_KEY, RATE_LIMIT_PER_MINUTE


async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(_uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


async def api_key_auth_middleware(request: Request, call_next):
    if not HIVEMIND_API_KEY:
        return await call_next(request)
    path = request.url.path
    exempt_prefixes = ("/healthz", "/readyz", "/metrics", "/static/", "/webhooks/", "/agent-session/", "/api/stream")
    if any(path.startswith(p) for p in exempt_prefixes) or not path.startswith("/api/"):
        return await call_next(request)
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
    if api_key != HIVEMIND_API_KEY:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


_rate_limit_store: Dict[str, list] = {}


async def rate_limit_middleware(request: Request, call_next):
    if request.url.path != "/api/tickets" or request.method != "POST":
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    _rate_limit_store[client_ip] = [t for t in _rate_limit_store[client_ip] if now - t < 60]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_PER_MINUTE:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    _rate_limit_store[client_ip].append(now)
    return await call_next(request)


__all__ = [
    "correlation_id_middleware",
    "api_key_auth_middleware",
    "rate_limit_middleware",
]