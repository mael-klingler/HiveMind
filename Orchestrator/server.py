#!/usr/bin/env python3

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

from config import GITLAB_WEBHOOK_SECRET, HIVEMIND_API_KEY, USE_SUPABASE, CORS_ORIGINS
from logging_setup import setup_logging, log, metrics
from middleware import correlation_id_middleware, api_key_auth_middleware, rate_limit_middleware, get_cors_origins
from database import import_repos_from_config, ensure_agent_pool, get_all_agents, get_tickets, get_queue

from background.queue_processor import _get_worker, set_shutdown, set_running
from background.sse import broadcast_event as _broadcast_event

app = FastAPI(title="HiveMind Orchestrator", version="1.0.0")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
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
    from redis_client import is_available
    redis_ok = is_available()
    return {"status": "ok", "redis": redis_ok}


@app.get("/readyz")
def readyz():
    try:
        from database import get_db
        get_db()
        from redis_client import is_available
        redis_ok = is_available()
        from config import REDIS_ENABLED
        if REDIS_ENABLED and not redis_ok:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={"status": "degraded", "redis": False})
        w = _get_worker()
        return {"status": "ok", "repos_initialized": w._init_done, "redis": redis_ok}
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "not ready"})


@app.get("/metrics")
def api_metrics():
    from database import get_all_agents, get_metrics_summary
    from datetime import datetime, timezone
    all_agents = get_all_agents()
    idle_agents = [a for a in all_agents if a.get("status") == "idle"]
    running_agents = [a for a in all_agents if a.get("status") == "running"]
    metrics.set("hivemind_agents_idle", len(idle_agents))
    metrics.set("hivemind_agents_running", len(running_agents))
    metrics.set("hivemind_agents_total", len(all_agents))
    metrics.set("hivemind_queue_length", len(get_queue()))
    all_tickets = get_tickets(status=None)
    now = datetime.now(timezone.utc)

    summary = get_metrics_summary()

    metrics.set("hivemind_tickets_created_total", summary["total_tickets"])
    metrics.set("hivemind_tickets_completed_total", summary["completed_tickets"])
    metrics.set("hivemind_tickets_failed_total", summary["failed_tickets"])
    metrics.set("hivemind_tickets_merged_total", summary["merged_tickets"])
    metrics.set("hivemind_ticket_retries_total", summary["total_retries"])
    metrics.set("hivemind_review_cycles_total", summary["avg_review_cycles"])
    metrics.set("hivemind_llm_prompt_tokens_total", summary["total_prompt_tokens"])
    metrics.set("hivemind_llm_completion_tokens_total", summary["total_completion_tokens"])
    metrics.set("hivemind_llm_cost_usd_total", summary["total_llm_cost_usd"])

    total_added = 0
    total_removed = 0
    total_files = 0
    for t in all_tickets:
        total_added += (t.get("lines_added") or 0)
        total_removed += (t.get("lines_removed") or 0)
        total_files += (t.get("files_changed") or 0)

    metrics.set("hivemind_lines_added_total", total_added)
    metrics.set("hivemind_lines_removed_total", total_removed)
    metrics.set("hivemind_files_changed_total", total_files)

    for status in ("queued", "running", "completed", "failed", "merged", "stopped"):
        count = sum(1 for t in all_tickets if t.get("status") == status)
        metrics.set("hivemind_tickets", count, labels={"status": status})

    completed_tickets = [t for t in all_tickets if t.get("status") in ("completed", "merged")]
    if completed_tickets:
        durations = []
        for t in completed_tickets:
            try:
                created = datetime.fromisoformat(t.get("created_at", ""))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                end_str = t.get("completed_at") or t.get("merged_at") or t.get("updated_at", "")
                if end_str:
                    end = datetime.fromisoformat(end_str)
                    if end.tzinfo is None:
                        end = end.replace(tzinfo=timezone.utc)
                    durations.append((end - created).total_seconds())
            except (ValueError, TypeError):
                pass
        if durations:
            d = sorted(durations)
            for q, v in [(0.5, d[len(d)//2]), (0.9, d[int(len(d)*0.9)]), (0.99, d[int(len(d)*0.99)])]:
                metrics.set("hivemind_ticket_duration_seconds", v, labels={"status": "completed", "quantile": str(q)})
            metrics.set("hivemind_ticket_duration_seconds_count", len(d), labels={"status": "completed"})
            metrics.set("hivemind_ticket_duration_seconds_sum", sum(d), labels={"status": "completed"})

    active_tickets = [t for t in all_tickets if t.get("status") in ("queued", "running")]
    if active_tickets:
        ages = []
        for t in active_tickets:
            try:
                created = datetime.fromisoformat(t.get("created_at", ""))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                ages.append((now - created).total_seconds())
            except (ValueError, TypeError):
                pass
        if ages:
            a = sorted(ages)
            for q, v in [(0.5, a[len(a)//2]), (0.9, a[int(len(a)*0.9)]), (0.99, a[int(len(a)*0.99)])]:
                metrics.set("hivemind_ticket_age_seconds", v, labels={"status": "running", "quantile": str(q)})
            metrics.set("hivemind_ticket_age_seconds_count", len(a), labels={"status": "running"})
            metrics.set("hivemind_ticket_age_seconds_sum", sum(a), labels={"status": "running"})

    metrics.set("hivemind_tickets_recovered_total", 0)
    metrics.set("hivemind_phase_transitions_total", 0)
    metrics.set("hivemind_queue_wait_seconds", 0)
    metrics.set("hivemind_proxy_requests_total", 0)

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


async def _auto_import_gitlab_projects():
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_TOKEN", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        return

    await asyncio.sleep(5)
    try:
        from gitlab_client import gitlab_get_sync
        from database import get_all_repos, add_repo as _add_repo

        existing = {r["name"] for r in get_all_repos()}
        if existing:
            log.info(f"GitLab auto-import: {len(existing)} repos already configured, skipping")
            return

        projects = gitlab_get_sync("/projects", {
            "membership": "true",
            "min_access_level": "20",
            "order_by": "name",
            "sort": "asc",
        }, gitlab_host, gitlab_token)

        if not projects:
            log.warning("GitLab auto-import: no projects found")
            return

        added = 0
        for p in projects:
            name = p.get("name", "")
            url = p.get("http_url_to_repo", "")
            if not name or name in existing or not url:
                continue
            branch = p.get("default_branch", "main") or "main"
            description = p.get("description", "") or ""
            topics = p.get("topics", []) or []
            ok = _add_repo(name=name, url=url, branch=branch, description=description, tags=topics, active=1)
            if ok:
                existing.add(name)
                added += 1

        log.info(f"GitLab auto-import: {added} repos imported from {len(projects)} projects")

        from background import queue_processor
        queue_processor._worker = None

        await _broadcast_event("repos_updated", {"added": added, "source": "gitlab_auto_import"})

        await _register_webhooks_for_repos()
    except Exception as e:
        log.error(f"GitLab auto-import failed: {e}", exc_info=True)


async def _register_webhooks_for_repos():
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        return

    orchestrator_url = os.getenv("ORCHESTRATOR_WEBHOOK_URL", "")
    if not orchestrator_url:
        log.info("GitLab webhook registration: ORCHESTRATOR_WEBHOOK_URL not set, skipping")
        return

    webhook_secret = os.getenv("GITLAB_WEBHOOK_SECRET", "")

    try:
        from gitlab_client import gitlab_get_sync
        from database import get_all_repos

        repos = get_all_repos(active_only=False)
        for repo in repos:
            url = repo.get("url", "")
            if not url:
                continue
            project_path = url.split("://")[-1].replace(".git", "")
            if "/" not in project_path:
                continue
            if ":" in project_path.split("/")[0]:
                project_path = "/".join(project_path.split("/")[1:])

            encoded_path = project_path.replace("/", "%2F")

            try:
                hooks = gitlab_get_sync(f"/projects/{encoded_path}/hooks", {}, gitlab_host, gitlab_token)
                if hooks is None:
                    continue

                already_registered = any(
                    h.get("url", "").rstrip("/") == orchestrator_url.rstrip("/")
                    for h in (hooks or [])
                )
                if already_registered:
                    continue

                from vcs.gitlab import GitLabProvider
                provider = GitLabProvider()
                body = {
                    "url": orchestrator_url,
                    "push_events": True,
                    "issues_events": True,
                    "merge_requests_events": True,
                    "note_events": True,
                    "tag_push_events": False,
                    "enable_ssl_verification": orchestrator_url.startswith("https"),
                }
                if webhook_secret:
                    body["token"] = webhook_secret

                result = await provider.create_project_hook(encoded_path, body, gitlab_host, gitlab_token)
                if result:
                    log.info(f"GitLab webhook registered for {project_path}")
                else:
                    log.warning(f"GitLab webhook registration failed for {project_path}")
            except Exception as e:
                log.warning(f"GitLab webhook registration error for {project_path}: {e}")
    except Exception as e:
        log.error(f"GitLab webhook registration failed: {e}", exc_info=True)


@app.on_event("startup")
def startup_event():
    from background.queue_processor import queue_processor
    from background.review_monitor import review_lifecycle_monitor
    from background.agent_monitor import agent_pod_monitor
    from background.workspace_cleanup import workspace_cleanup_loop
    from background.sse import start_redis_subscriber

    setup_logging()
    import_repos_from_config(os.getenv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json"))
    ensure_agent_pool()
    start_redis_subscriber()
    asyncio.create_task(queue_processor())
    asyncio.create_task(review_lifecycle_monitor())
    asyncio.create_task(agent_pod_monitor())
    asyncio.create_task(_background_repo_init())
    asyncio.create_task(_auto_import_gitlab_projects())
    asyncio.create_task(_orphan_recovery())
    asyncio.create_task(workspace_cleanup_loop())
    log.info(f"GitLab Webhook Secret: {'enabled' if GITLAB_WEBHOOK_SECRET else 'disabled (insecure)'}")
    log.info(f"API Authentication: {'enabled' if HIVEMIND_API_KEY else 'disabled (insecure)'}")
    from config import REDIS_ENABLED
    log.info(f"Redis: {'enabled' if REDIS_ENABLED else 'disabled (in-memory fallback)'}")


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