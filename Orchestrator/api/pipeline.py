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
API routes: Pipeline phases and team collaboration.
"""

import json
from fastapi import APIRouter, Request, HTTPException

from database import (
    get_ticket, update_ticket_status, add_ticket_comment,
    add_team_message, get_team_messages, get_queue,
    set_agent_status, complete_queue_item,
)
from database.phases import (
    PHASE_ORDER, PHASE_LABELS, PHASE_DESCRIPTIONS,
    VALID_PHASE_TRANSITIONS, ROLE_REQUIRED_PHASES, PHASE_PREFERRED_ROLES,
    ROLE_DISPLAY_NAMES, DEFAULT_ROLE_INSTRUCTIONS,
    validate_phase_transition, get_next_phase, get_initial_phase,
    can_role_handle_phase, select_agent_for_phase,
    create_pipeline_step, complete_pipeline_step, fail_pipeline_step,
    get_pipeline_steps,
)
from background.pipeline_engine import (
    advance_ticket_phase, fail_ticket_phase, build_phase_context,
    get_role_instruction, send_team_message, get_team_context,
    ensure_pipeline_group,
)
from background.sse import broadcast_event
from logging_setup import log, metrics

router = APIRouter()


@router.get("/api/phases")
def api_list_phases():
    return {
        "phases": PHASE_ORDER,
        "labels": PHASE_LABELS,
        "descriptions": PHASE_DESCRIPTIONS,
        "transitions": VALID_PHASE_TRANSITIONS,
        "role_preferred_phases": PHASE_PREFERRED_ROLES,
    }


@router.get("/api/roles")
def api_list_roles():
    return {
        "roles": list(ROLE_DISPLAY_NAMES.keys()),
        "display_names": ROLE_DISPLAY_NAMES,
        "role_phases": ROLE_REQUIRED_PHASES,
    }


@router.get("/api/roles/{role}/instruction")
def api_role_instruction(role: str):
    instruction = get_role_instruction(role)
    if not instruction:
        raise HTTPException(status_code=404, detail=f"Unknown role: {role}")
    return {"role": role, "instruction": instruction}


@router.get("/api/tickets/{ticket_id}/pipeline")
def api_ticket_pipeline(ticket_id: str):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    steps = get_pipeline_steps(ticket_id)
    current_phase = ticket.get("current_phase", "work") or "work"
    return {
        "ticket_id": ticket_id,
        "current_phase": current_phase,
        "steps": steps,
        "available_transitions": VALID_PHASE_TRANSITIONS.get(current_phase, []),
    }


@router.post("/api/agents/{agent_id}/phase_complete")
async def api_phase_complete(agent_id: str, req: Request):
    data = await req.json()
    ticket_id = data.get("ticket_id")
    phase = data.get("phase", "work")
    result = data.get("result", "")
    queue_id = data.get("queue_id")

    if not ticket_id:
        raise HTTPException(status_code=400, detail="ticket_id required")

    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    current_phase = ticket.get("current_phase", "work") or "work"
    if current_phase != phase:
        log.warning(f"Agent {agent_id} reports phase_complete for '{phase}' but ticket is in '{current_phase}'", extra={
            "agent_id": agent_id, "ticket_id": ticket_id,
        })

    steps = get_pipeline_steps(ticket_id)
    active_step = next((s for s in steps if s["phase"] == phase and s["status"] == "running"), None)
    if active_step:
        complete_pipeline_step(active_step["id"], result=result)

    next_phase = advance_ticket_phase(ticket_id, phase, result)

    group_id = ensure_pipeline_group(ticket_id, ticket)
    send_team_message(group_id, agent_id, "phase_complete", f"Phase '{phase}' completed. {result[:200]}")

    if next_phase:
        from database import update_ticket_status

        update_ticket_status(ticket_id, "queued")
        log.info(f"Ticket {ticket_id} advanced to phase '{next_phase}'", extra={
            "ticket_id": ticket_id, "agent_id": agent_id,
            "event": "phase_advanced", "phase": next_phase,
        })

        metrics.inc("hivemind_pipeline_phase_completed_total", labels={"phase": phase})

        set_agent_status(agent_id, "idle")
        await broadcast_event("agent_updated", {"agent_id": agent_id, "status": "idle"})
        await broadcast_event("queue_updated", get_queue())

        return {"ok": True, "next_phase": next_phase, "message": f"Advanced to {next_phase} phase"}
    else:
        if queue_id:
            complete_queue_item(queue_id)
        update_ticket_status(ticket_id, "completed")
        set_agent_status(agent_id, "idle", progress=100)
        metrics.inc("hivemind_pipeline_completed_total")
        log.info(f"Ticket {ticket_id} pipeline completed", extra={
            "ticket_id": ticket_id, "event": "pipeline_completed",
        })

        await broadcast_event("agent_updated", {"agent_id": agent_id, "status": "idle"})
        await broadcast_event("queue_updated", get_queue())

        return {"ok": True, "next_phase": None, "message": "Pipeline completed"}


@router.post("/api/agents/{agent_id}/phase_fail")
async def api_phase_fail(agent_id: str, req: Request):
    data = await req.json()
    ticket_id = data.get("ticket_id")
    phase = data.get("phase", "work")
    error = data.get("error", "Unknown error")

    if not ticket_id:
        raise HTTPException(status_code=400, detail="ticket_id required")

    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    steps = get_pipeline_steps(ticket_id)
    active_step = next((s for s in steps if s["phase"] == phase and s["status"] == "running"), None)
    if active_step:
        fail_pipeline_step(active_step["id"], result=error)

    retry_phase = fail_ticket_phase(ticket_id, phase, error)

    group_id = ensure_pipeline_group(ticket_id, ticket)
    send_team_message(group_id, agent_id, "phase_fail", f"Phase '{phase}' failed: {error[:200]}")

    from database import requeue_ticket
    update_ticket_status(ticket_id, "queued")
    requeue_ticket(ticket_id, max_retries=3)

    set_agent_status(agent_id, "idle")
    metrics.inc("hivemind_pipeline_phase_failed_total", labels={"phase": phase})

    log.warning(f"Ticket {ticket_id} phase '{phase}' failed, reverting to '{retry_phase}'", extra={
        "ticket_id": ticket_id, "agent_id": agent_id,
        "event": "phase_failed", "phase": phase, "retry_phase": retry_phase,
    })

    await broadcast_event("agent_updated", {"agent_id": agent_id, "status": "idle"})
    await broadcast_event("queue_updated", get_queue())

    return {"ok": True, "retry_phase": retry_phase, "message": f"Reverted to {retry_phase} phase"}


# ── Team Messages ────────────────────────────────────

@router.get("/api/groups/{group_id}/messages")
def api_get_messages(group_id: str, limit: int = 50):
    messages = get_team_messages(group_id, limit=limit)
    return {"group_id": group_id, "messages": messages}


@router.post("/api/groups/{group_id}/messages")
async def api_send_message(group_id: str, req: Request):
    data = await req.json()
    sender_agent_id = data.get("sender_agent_id", "system")
    message_type = data.get("message_type", "info")
    content = data.get("content", "")

    if not content:
        raise HTTPException(status_code=400, detail="content required")

    message_id = add_team_message(group_id, sender_agent_id, content, message_type=message_type)

    await broadcast_event("team_message", {
        "group_id": group_id,
        "sender_agent_id": sender_agent_id,
        "message_type": message_type,
        "content": content,
    })

    return {"ok": True, "message_id": message_id}