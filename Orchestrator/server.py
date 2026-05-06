#!/usr/bin/env python3

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
Orchestrator Web UI + REST API – FastAPI application.
Thin entry point that wires together all modular components.
"""

import asyncio
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse

from config import GITLAB_WEBHOOK_SECRET, HIVEMIND_API_KEY
from logging_setup import setup_logging, log, metrics
from middleware import correlation_id_middleware, api_key_auth_middleware, rate_limit_middleware
from database import import_repos_from_config, ensure_agent_pool, get_all_agents, get_tickets, get_queue

from background.queue_processor import _get_worker, set_shutdown, set_running
from background.sse import broadcast_event as _broadcast_event

app = FastAPI(title="HiveMind Orchestrator", version="1.0.0")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(correlation_id_middleware)
app.middleware("http")(api_key_auth_middleware)
app.middleware("http")(rate_limit_middleware)

from api import register_routes
register_routes(app)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        from database import get_db
        get_db()
        w = _get_worker()
        return {"status": "ok", "repos_initialized": w._init_done}
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "not ready"})


@app.get("/metrics")
def api_metrics():
    from database import get_all_agents
    all_agents = get_all_agents()
    idle_agents = [a for a in all_agents if a.get("status") == "idle"]
    running_agents = [a for a in all_agents if a.get("status") == "running"]
    metrics.set("hivemind_agents_idle", len(idle_agents))
    metrics.set("hivemind_agents_running", len(running_agents))
    metrics.set("hivemind_agents_total", len(all_agents))
    metrics.set("hivemind_queue_length", len(get_queue()))
    all_tickets = get_tickets(status=None)
    for status in ("queued", "running", "completed", "failed", "merged", "stopped"):
        count = sum(1 for t in all_tickets if t.get("status") == status)
        metrics.set("hivemind_tickets", count, labels={"status": status})
    return PlainTextResponse(content=metrics.render(), media_type="text/plain")


@app.get("/", response_class=HTMLResponse)
def web_ui():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    with open("static/settings.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/repos", response_class=HTMLResponse)
def repos_page():
    with open("static/repos.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/agent", response_class=HTMLResponse)
def agent_page():
    with open("static/agent.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/tickets", response_class=HTMLResponse)
def tickets_page():
    with open("static/tickets.html", "r", encoding="utf-8") as f:
        return f.read()


_shutdown_requested = False


def _handle_sigterm(signum, frame):
    global _shutdown_requested
    _shutdown_requested = True
    set_shutdown(True)
    set_running(False)
    log.info("SIGTERM received, initiating graceful shutdown")


signal.signal(signal.SIGTERM, _handle_sigterm)


async def _orphan_recovery():
    await asyncio.sleep(10)
    try:
        from k8s_client import list_pods
        from database import requeue_ticket, set_agent_status, get_tickets
        namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
        pods = list_pods(namespace, label_selector="app.kubernetes.io/component=agent")
        if not pods:
            return
        running_tickets = {t["id"]: t for t in get_tickets(status=None) if t.get("status") == "running"}
        pod_ticket_ids = set()
        for pod in pods:
            pod_name = pod.metadata.name
            if pod_name.startswith("agent-worker-"):
                tid = pod_name.replace("agent-worker-", "").upper()
                pod_ticket_ids.add(tid)
        for tid, t in running_tickets.items():
            if tid.lower() not in {p.lower() for p in pod_ticket_ids}:
                log.info(f"Orphan recovery: ticket {tid} is running but no pod exists, re-queuing", extra={"ticket_id": tid, "event": "orphan_recovery"})
                requeue_ticket(tid, 3)
                agent_id = t.get("agent_id", "") or tid.lower()
                set_agent_status(agent_id, "idle")
                await _broadcast_event("ticket_requeued", {"ticket_id": tid, "reason": "Orphaned after restart"})
        await _broadcast_event("queue_updated", get_queue())
    except Exception as e:
        log.error(f"Orphan recovery failed: {e}", exc_info=True)


async def _background_repo_init():
    await asyncio.sleep(3)
    w = _get_worker()
    if w._init_done:
        return
    try:
        await w._aensure_init()
    except Exception as e:
        log.warning(f"Background repo init failed: {e}")


@app.on_event("startup")
def startup_event():
    from background.queue_processor import queue_processor
    from background.review_monitor import review_lifecycle_monitor
    from background.agent_monitor import agent_pod_monitor

    setup_logging()
    import_repos_from_config(os.getenv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json"))
    ensure_agent_pool()
    asyncio.create_task(queue_processor())
    asyncio.create_task(review_lifecycle_monitor())
    asyncio.create_task(agent_pod_monitor())
    asyncio.create_task(_background_repo_init())
    asyncio.create_task(_orphan_recovery())
    log.info(f"GitLab Webhook Secret: {'enabled' if GITLAB_WEBHOOK_SECRET else 'disabled (insecure)'}")
    log.info(f"API Authentication: {'enabled' if HIVEMIND_API_KEY else 'disabled (insecure)'}")


@app.on_event("shutdown")
async def shutdown_event():
    set_running(False)
    log.info("Graceful shutdown: stopping background tasks...")
    await _broadcast_event("server_shutdown", {"message": "Server shutting down"})
    try:
        from gitlab_client import close_async_client
        await close_async_client()
    except Exception:
        pass
    log.info("Shutdown complete")


if __name__ == "__main__":
    import uvicorn
    log.info("Starting Orchestrator Server on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)