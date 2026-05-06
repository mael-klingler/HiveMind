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