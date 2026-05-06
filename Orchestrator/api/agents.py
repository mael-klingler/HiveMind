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
API routes: Agents
"""

from fastapi import APIRouter, Request

from database import (
    add_step,
    complete_queue_item,
    get_all_agents,
    get_all_steps,
    get_idle_agents,
    get_next_queue_item,
    get_or_create_agent,
    assign_queue_item,
    set_agent_status,
    update_ticket_status,
    add_ticket_comment,
    get_queue,
)
from logging_setup import log, metrics
from background.sse import broadcast_event

router = APIRouter()


@router.get("/api/agents")
def api_agents():
    return get_all_agents()


@router.get("/api/agents/{agent_id}")
def api_agent(agent_id: str):
    agent = get_or_create_agent(agent_id)
    return agent


@router.get("/api/agents/{agent_id}/steps")
def api_agent_steps(agent_id: str):
    return get_all_steps(agent_id=agent_id)


@router.post("/api/agents/{agent_id}/progress")
async def api_agent_progress(agent_id: str, req: Request):
    data = await req.json()
    progress = data.get("progress", 0)
    step = data.get("step", "")
    detail = data.get("detail", "")
    ticket_id = data.get("ticket_id", "")
    queue_id = data.get("queue_id")

    set_agent_status(agent_id, "running", ticket_id, progress)

    if queue_id and step:
        add_step(
            queue_id=queue_id,
            ticket_id=ticket_id,
            agent_id=agent_id,
            step_name=step,
            status="running",
            detail=detail,
        )
        await broadcast_event("step_added", {
            "agent_id": agent_id,
            "ticket_id": ticket_id,
            "step": step,
            "progress": progress,
            "detail": detail,
        })

    await broadcast_event("agent_updated", {
        "agent_id": agent_id,
        "progress": progress,
        "status": "running",
        "ticket_id": ticket_id,
    })

    return {"ok": True}


@router.post("/api/agents/{agent_id}/complete")
async def api_agent_complete(agent_id: str, req: Request):
    data = await req.json()
    queue_id = data.get("queue_id")
    ticket_id = data.get("ticket_id")

    set_agent_status(agent_id, "idle", progress=100)

    if queue_id:
        complete_queue_item(queue_id)

    if ticket_id:
        update_ticket_status(ticket_id, "completed")
        add_ticket_comment(ticket_id, author=agent_id, comment_type="summary", content=f"Agent {agent_id} has completed the task.")

    next_item = get_next_queue_item()
    if next_item:
        idle = get_idle_agents()
        if idle:
            agent = idle[0]
            assign_queue_item(next_item["id"], agent["id"])
            set_agent_status(agent["id"], "running", next_item["ticket_id"], 0)
            update_ticket_status(next_item["ticket_id"], "running")
            await broadcast_event("ticket_assigned", {
                "ticket_id": next_item["ticket_id"],
                "agent_id": agent["id"],
            })

    await broadcast_event("queue_updated", get_queue())
    await broadcast_event("agent_updated", {
        "agent_id": agent_id,
        "status": "idle",
    })

    return {"ok": True}