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
API routes: OpenCode Plugins CRUD
"""

from fastapi import APIRouter, HTTPException, Request

from database import (
    add_opencode_plugin,
    delete_opencode_plugin,
    get_opencode_plugins,
    update_opencode_plugin,
)

router = APIRouter()


@router.get("/api/plugins")
def api_get_plugins():
    return get_opencode_plugins()


@router.post("/api/plugins")
async def api_add_plugin(req: Request):
    data = await req.json()
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    name = add_opencode_plugin(data)
    return {"ok": True, "name": name}


@router.patch("/api/plugins/{name}")
async def api_update_plugin(name: str, req: Request):
    data = await req.json()
    update_opencode_plugin(name, data)
    return {"ok": True}


@router.delete("/api/plugins/{name}")
def api_delete_plugin(name: str):
    delete_opencode_plugin(name)
    return {"ok": True}