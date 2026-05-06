"""
API routes: Agent Instructions CRUD
"""

from fastapi import APIRouter, HTTPException, Request

from database import (
    add_agent_instruction,
    delete_agent_instruction,
    get_agent_instructions,
    update_agent_instruction,
)

router = APIRouter()


@router.get("/api/agent-instructions")
def api_get_agent_instructions():
    return get_agent_instructions()


@router.post("/api/agent-instructions")
async def api_add_agent_instruction(req: Request):
    data = await req.json()
    if not data.get("name") or not data.get("content"):
        raise HTTPException(status_code=400, detail="name and content are required")
    id = add_agent_instruction(data)
    return {"ok": True, "id": id}


@router.patch("/api/agent-instructions/{id}")
async def api_update_agent_instruction(id: int, req: Request):
    data = await req.json()
    update_agent_instruction(id, data)
    return {"ok": True}


@router.delete("/api/agent-instructions/{id}")
def api_delete_agent_instruction(id: int):
    delete_agent_instruction(id)
    return {"ok": True}