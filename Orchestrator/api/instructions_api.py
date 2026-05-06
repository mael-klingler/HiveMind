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