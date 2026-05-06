"""
API routes: Queue, Steps, Config, Agent Sessions
"""

import os
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request

from database import (
    ensure_agent_pool,
    get_all_steps,
    get_max_agents,
    get_queue,
    get_tickets,
    set_max_agents,
)
from background.sse import broadcast_event

router = APIRouter()


@router.get("/api/queue")
def api_queue():
    return get_queue()


@router.get("/api/steps")
def api_all_steps():
    return get_all_steps()


@router.get("/api/config")
def api_config():
    config_data = {"max_agents": get_max_agents()}
    version_path = Path(__file__).resolve().parent.parent.parent / ".version"
    try:
        config_data["version"] = version_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        config_data["version"] = "dev"
    return config_data


@router.post("/api/config")
async def api_set_config(req: Request):
    data = await req.json()
    if "max_agents" in data:
        set_max_agents(int(data["max_agents"]))
        ensure_agent_pool()
        await broadcast_event("config_updated", {"max_agents": data["max_agents"]})
    return {"ok": True}


@router.get("/api/agent-sessions")
def api_agent_sessions():
    running = [t for t in get_tickets(status=None) if t.get("status") == "running"]
    sessions = []
    for t in running:
        ticket_id = t["id"]
        pod_name = f"agent-worker-{ticket_id.lower()}"
        namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
        sessions.append({
            "ticket_id": ticket_id,
            "title": t.get("title", ""),
            "pod": pod_name,
            "session_url": f"/agent-session/{ticket_id}/",
            "status": t.get("status", "unknown"),
        })
    return sessions