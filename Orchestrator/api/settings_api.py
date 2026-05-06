"""
API routes: Settings
"""

import json
from fastapi import APIRouter, Request

from database import get_all_settings, set_setting

router = APIRouter()


@router.get("/api/settings")
def api_get_settings():
    return get_all_settings()


@router.post("/api/settings")
async def api_set_settings(req: Request):
    data = await req.json()
    for key, value in data.items():
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        set_setting(key, str(value))
    return {"ok": True}