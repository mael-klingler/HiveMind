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
Rate limiting uses Redis when available, falls back to in-memory.
Applies to all mutation endpoints (POST, PUT, PATCH, DELETE), not just ticket creation.
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

MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_PATHS = ("/healthz", "/readyz", "/metrics", "/static/", "/webhooks/", "/agent-session/", "/api/stream")


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting X-Forwarded-For from load balancers."""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


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
    if any(path.startswith(p) for p in EXEMPT_PATHS) or not path.startswith("/api/"):
        return await call_next(request)

    if request.method == "GET":
        return await call_next(request)

    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
    if api_key != HIVEMIND_API_KEY:
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)


async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting for mutation endpoints. Uses Redis when available."""
    if request.method not in MUTATION_METHODS:
        return await call_next(request)

    path = request.url.path
    if any(path.startswith(p) for p in EXEMPT_PATHS):
        return await call_next(request)

    client_ip = _get_client_ip(request)
    key = f"{client_ip}:{path}"

    if USE_SUPABASE:
        from database.supabase_adapter import check_rate_limit
        allowed, remaining = check_rate_limit(client_ip, RATE_LIMIT_PER_MINUTE)
        if not allowed:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    from redis_client import is_rate_limited
    is_limited, remaining = is_rate_limited(key, RATE_LIMIT_PER_MINUTE, window_seconds=60)
    if is_limited:
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    response = await call_next(request)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    return response


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