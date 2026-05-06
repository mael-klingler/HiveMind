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
API routes: MCP Servers CRUD
"""

from fastapi import APIRouter, HTTPException, Request

from database import (
    add_mcp_server,
    delete_mcp_server,
    get_mcp_servers,
    update_mcp_server,
)

router = APIRouter()


@router.get("/api/mcp-servers")
def api_get_mcp_servers():
    return get_mcp_servers()


@router.post("/api/mcp-servers")
async def api_add_mcp_server(req: Request):
    data = await req.json()
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    name = add_mcp_server(data)
    return {"ok": True, "name": name}


@router.patch("/api/mcp-servers/{name}")
async def api_update_mcp_server(name: str, req: Request):
    data = await req.json()
    update_mcp_server(name, data)
    return {"ok": True}


@router.delete("/api/mcp-servers/{name}")
def api_delete_mcp_server(name: str):
    delete_mcp_server(name)
    return {"ok": True}