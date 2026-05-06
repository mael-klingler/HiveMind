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