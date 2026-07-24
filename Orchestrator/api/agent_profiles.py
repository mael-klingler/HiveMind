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
API routes: Agent Profiles CRUD
"""

from fastapi import APIRouter, Request

from database import (
    create_agent,
    delete_agent,
    get_agent_with_profile,
    get_all_agents_with_profiles,
    seed_default_memory_blocks,
    set_agent_instruction_assignments,
    set_agent_repo_affinities,
    set_agent_skills,
    update_agent_profile,
)
from database.phases import ROLE_DISPLAY_NAMES, DEFAULT_ROLE_INSTRUCTIONS

router = APIRouter()


@router.get("/api/agent-profiles")
def api_get_agent_profiles():
    return get_all_agents_with_profiles()


@router.get("/api/roles")
def api_get_roles():
    return {
        "roles": list(ROLE_DISPLAY_NAMES.keys()),
        "display_names": ROLE_DISPLAY_NAMES,
        "descriptions": {k: v.split(".")[0] + "." for k, v in DEFAULT_ROLE_INSTRUCTIONS.items()},
    }


@router.post("/api/agent-profiles")
async def api_create_agent_profile(req: Request):
    data = await req.json()
    agent_id = data.get("id", "").strip()
    name = data.get("name", "").strip()
    model_name = data.get("model_name", "").strip()
    role = data.get("role", "general").strip()
    skills = data.get("skills", [])
    instruction_ids = data.get("instruction_ids", [])
    repo_affinities = data.get("repo_affinities", [])
    if not agent_id:
        return {"ok": False, "error": "Agent ID is required"}
    existing = get_agent_with_profile(agent_id)
    if existing:
        return {"ok": False, "error": f"Agent '{agent_id}' already exists"}
    agent = create_agent(agent_id, name or agent_id, model_name=model_name,
                         skill_names=skills, instruction_ids=instruction_ids,
                         repo_affinities=repo_affinities)
    from database import set_agent_role
    set_agent_role(agent_id, role)
    seed_default_memory_blocks(agent_id)
    return {"ok": True, **agent}


@router.patch("/api/agent-profiles/{agent_id}")
async def api_update_agent_profile(agent_id: str, req: Request):
    data = await req.json()
    name = data.get("name")
    model_name = data.get("model_name")
    role = data.get("role")
    if data.get("skills") is not None:
        set_agent_skills(agent_id, data["skills"])
    if data.get("instruction_ids") is not None:
        set_agent_instruction_assignments(agent_id, data["instruction_ids"])
    if data.get("repo_affinities") is not None:
        set_agent_repo_affinities(agent_id, data["repo_affinities"])
    if role:
        from database import set_agent_role
        set_agent_role(agent_id, role)
    agent = update_agent_profile(agent_id, name=name, model_name=model_name)
    if not agent:
        return {"ok": False, "error": f"Agent '{agent_id}' not found"}
    return {"ok": True, **agent}


@router.delete("/api/agent-profiles/{agent_id}")
def api_delete_agent_profile(agent_id: str):
    if agent_id in ("agent-1", "agent-2", "agent-3"):
        return {"ok": False, "error": "Default agents cannot be deleted"}
    agent = get_agent_with_profile(agent_id)
    if not agent:
        return {"ok": False, "error": f"Agent '{agent_id}' not found"}
    if agent.get("status") == "running":
        return {"ok": False, "error": "Running agents cannot be deleted"}
    delete_agent(agent_id)
    return {"ok": True}