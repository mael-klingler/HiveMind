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
API routes: Tickets
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse

from database import (
    add_ticket_comment,
    create_ticket,
    get_all_steps,
    get_ticket,
    get_ticket_comments,
    get_tickets,
    get_tickets_with_queue,
    reopen_ticket,
    set_ticket_mr_url,
    stop_ticket,
    update_ticket_status,
)
from logging_setup import log, metrics
from background.sse import broadcast_event
from config import AGENT_MAX_RETRIES

router = APIRouter()


@router.get("/api/tickets", response_model=List[Dict])
def api_tickets(status: Optional[str] = None):
    return get_tickets(status)


@router.get("/api/tickets/{ticket_id}")
def api_ticket(ticket_id: str):
    t = get_ticket(ticket_id)
    if not t:
        return {"error": "Not found"}, 404
    return t


@router.get("/api/tickets/{ticket_id}/steps")
def api_ticket_steps(ticket_id: str):
    return get_all_steps(ticket_id=ticket_id)


@router.patch("/api/tickets/{ticket_id}")
async def api_update_ticket(ticket_id: str, req: Request):
    data = await req.json()
    status = data.get("status")
    if status:
        update_ticket_status(ticket_id, status)
        from database import get_queue
        await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": status}


@router.post("/api/tickets/{ticket_id}/reopen")
async def api_reopen_ticket(ticket_id: str):
    from database import set_agent_status
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    old_agent_id = ticket.get("agent_id", "")
    success = reopen_ticket(ticket_id)
    if not success:
        raise HTTPException(status_code=400, detail="Ticket could not be reopened")
    if old_agent_id:
        set_agent_status(old_agent_id, "idle")
        log.info(f"Agent {old_agent_id} → idle (ticket {ticket_id} reopened)", extra={"agent_id": old_agent_id, "ticket_id": ticket_id})
    add_ticket_comment(ticket_id, author="user", comment_type="system", content="Ticket manually reopened.")
    await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "Manually reopened"})
    from database import get_queue
    await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": "queued"}


@router.post("/api/tickets/{ticket_id}/stop")
async def api_stop_ticket(ticket_id: str):
    from database import set_agent_status
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.get("status") in ("completed", "stopped"):
        raise HTTPException(status_code=400, detail=f"Ticket is already {ticket.get('status')}")

    agent_id = ticket.get("agent_id", "")
    stopped = stop_ticket(ticket_id)
    if not stopped:
        raise HTTPException(status_code=400, detail="Ticket could not be stopped")

    if agent_id:
        try:
            from k8s_client import delete_pod
            namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
            delete_pod(f"agent-{agent_id}", namespace)
        except Exception:
            pass

    from k8s_client import cleanup_agent_resources
    cleanup_agent_resources(ticket_id)

    add_ticket_comment(ticket_id, author="user", comment_type="system", content="Ticket manually stopped.")
    await broadcast_event("ticket_stopped", {"ticket_id": ticket_id})
    from database import get_queue
    await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": "stopped"}


@router.post("/api/tickets")
async def api_create_ticket(req: Request, background_tasks: BackgroundTasks):
    data = await req.json()
    ticket_id = create_ticket(data)
    metrics.inc("hivemind_tickets_created_total")

    await broadcast_event("ticket_created", {"ticket_id": ticket_id, "title": data.get("title", "")})
    from database import get_queue
    await broadcast_event("queue_updated", get_queue())

    return {"id": ticket_id, "status": "queued"}


@router.post("/api/tickets/preview")
async def api_preview_ticket(req: Request):
    data = await req.json()
    from background.queue_processor import _get_worker
    w = _get_worker()
    await w._aensure_init()
    from git_manager import Ticket
    _ticket = Ticket(
        id=data.get("id", "PREVIEW"),
        title=data.get("title", ""),
        description=data.get("description", ""),
        labels=data.get("labels", []),
        issue_type=data.get("issue_type", "Task"),
        priority=data.get("priority", "Medium"),
    )
    analysis = None
    if w.llm.is_available():
        try:
            loop = asyncio.get_event_loop()
            analysis = await loop.run_in_executor(None, w.llm.analyze_repos_for_ticket, _ticket, w._statuses, w.leankg)
        except Exception as e:
            analysis = {"error": str(e)}
    if not analysis:
        analysis = {"error": "LLM not available for preview"}
    selected_names = set(analysis.get("selected_repos", []))
    selected_configs = [r for r in w.repositories if r.name in selected_names]
    from workspace_utils import generate_assignment_prompt
    prompt = generate_assignment_prompt(_ticket, analysis, selected_configs) if selected_configs else ""
    return {
        "analysis": analysis,
        "selected_repos": [r.name for r in selected_configs],
        "prompt": prompt,
        "complexity": analysis.get("complexity", "Unknown"),
        "estimated_hours": analysis.get("estimated_hours", "?"),
    }


@router.post("/api/tickets/{ticket_id}/mr")
async def api_ticket_mr(ticket_id: str, req: Request):
    data = await req.json()
    mr_url = data.get("mr_url", "")
    set_ticket_mr_url(ticket_id, mr_url)
    if mr_url:
        add_ticket_comment(ticket_id, author="system", comment_type="mr_created", content=f"Merge Request created: {mr_url}")
    await broadcast_event("ticket_mr", {"ticket_id": ticket_id, "mr_url": mr_url})
    return {"ok": True}


@router.get("/api/tickets/{ticket_id}/comments")
def api_ticket_comments(ticket_id: str):
    return get_ticket_comments(ticket_id)


@router.post("/api/tickets/{ticket_id}/comments")
def api_add_ticket_comment(ticket_id: str, data: dict):
    author = data.get("author", "user")
    comment_type = data.get("comment_type", "comment")
    content = data.get("content", "")
    if not content:
        return {"ok": False, "error": "Content is required"}
    row_id = add_ticket_comment(ticket_id, author=author, comment_type=comment_type, content=content)
    return {"ok": True, "id": row_id}


@router.get("/api/tickets/status/{status}")
def api_tickets_by_status(status: str):
    return get_tickets_with_queue()


@router.get("/tickets", response_class=HTMLResponse)
def tickets_page():
    with open("static/tickets.html", "r", encoding="utf-8") as f:
        return f.read()


@router.get("/api/tickets/{ticket_id}/logs")
async def api_ticket_logs(ticket_id: str):
    from k8s_client import kubectl_exec
    ns = os.getenv("AGENT_NAMESPACE", "hivemind")
    pod_name = f"agent-worker-{ticket_id.lower()}"
    rc, out, err = kubectl_exec(f"logs -n {ns} {pod_name} --tail=100")
    if rc != 0:
        if "NotFound" in err or "not found" in err.lower():
            return {"logs": "", "pod": pod_name, "status": "not_found"}
        return {"logs": f"Error: {err}", "pod": pod_name, "status": "error"}
    return {"logs": out, "pod": pod_name, "status": "ok"}