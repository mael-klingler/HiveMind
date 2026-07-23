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
FastAPI middleware: CORS, API key auth, Supabase Auth, rate limiting, correlation ID.
"""

import hashlib
import hmac
import os
import time as _time
import uuid as _uuid
from collections import OrderedDict
from typing import Dict, List

from fastapi import Request
from fastapi.responses import JSONResponse

from config import (
    CORS_ORIGINS,
    GITLAB_WEBHOOK_SECRET,
    HIVEMIND_API_KEY,
    RATE_LIMIT_PER_MINUTE,
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    USE_SUPABASE,
)


async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(_uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


async def api_key_auth_middleware(request: Request, call_next):
    if USE_SUPABASE:
        from middleware_supabase import supabase_auth_middleware
        return await supabase_auth_middleware(request, call_next)

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
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


_rate_limit_store: Dict[str, list] = {}
_RATE_LIMIT_MAX_ENTRIES = 10000


def _cleanup_rate_limit_store():
    if len(_rate_limit_store) > _RATE_LIMIT_MAX_ENTRIES:
        now = _time.time()
        _rate_limit_store.clear()


async def rate_limit_middleware(request: Request, call_next):
    if USE_SUPABASE:
        from database.supabase_adapter import check_rate_limit
        if request.url.path == "/api/tickets" and request.method == "POST":
            client_ip = request.client.host if request.client else "unknown"
            allowed, remaining = check_rate_limit(client_ip, RATE_LIMIT_PER_MINUTE)
            if not allowed:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
            response = await call_next(request)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response
        return await call_next(request)

    if request.url.path != "/api/tickets" or request.method != "POST":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    _rate_limit_store[client_ip] = [t for t in _rate_limit_store[client_ip] if now - t < 60]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_PER_MINUTE:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    _rate_limit_store[client_ip].append(now)
    _cleanup_rate_limit_store()
    return await call_next(request)


def verify_gitlab_webhook(request: Request) -> bool:
    if not GITLAB_WEBHOOK_SECRET:
        return True
    token = request.headers.get("X-Gitlab-Token", "")
    if not token:
        return False
    return hmac.compare_digest(token, GITLAB_WEBHOOK_SECRET)


def verify_github_webhook(request: Request, body: bytes) -> bool:
    github_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not github_secret:
        return True
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature:
        return False
    expected = "sha256=" + hmac.new(github_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def get_cors_origins() -> List[str]:
    if CORS_ORIGINS == "*":
        return ["*"]
    return [origin.strip() for origin in CORS_ORIGINS.split(",") if origin.strip()]


__all__ = [
    "correlation_id_middleware",
    "api_key_auth_middleware",
    "rate_limit_middleware",
    "verify_gitlab_webhook",
    "verify_github_webhook",
    "get_cors_origins",
]