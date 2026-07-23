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
Supabase Auth middleware – replaces X-API-Key auth with JWT-based Supabase Auth.
Falls back to X-API-Key when SUPABASE_URL is not set.
"""

import os
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
HIVEMIND_API_KEY = os.getenv("HIVEMIND_API_KEY", "")

EXEMPT_PREFIXES = ("/healthz", "/readyz", "/metrics", "/static/", "/webhooks/", "/agent-session/", "/api/stream")

_sb_client = None


def _get_supabase_client():
    global _sb_client
    if _sb_client is None and SUPABASE_URL:
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _sb_client


async def supabase_auth_middleware(request: Request, call_next):
    if not SUPABASE_URL:
        return await call_next(request)

    path = request.url.path
    if any(path.startswith(p) for p in EXEMPT_PREFIXES) or not path.startswith("/api/"):
        return await call_next(request)

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        return JSONResponse(status_code=401, content={"detail": "Missing authorization token"})

    if token == HIVEMIND_API_KEY and HIVEMIND_API_KEY:
        request.state.user = {"role": "service_role"}
        return await call_next(request)

    sb = _get_supabase_client()
    if not sb:
        return JSONResponse(status_code=500, content={"detail": "Auth service unavailable"})

    try:
        user = sb.auth.get_user(token)
        if user and user.user:
            request.state.user = {
                "id": user.user.id,
                "email": user.user.email,
                "role": user.user.role or "authenticated",
            }
            return await call_next(request)
    except Exception:
        pass

    return JSONResponse(status_code=401, content={"detail": "Invalid or expired token"})


def get_current_user(request: Request) -> Optional[dict]:
    return getattr(request.state, "user", None)