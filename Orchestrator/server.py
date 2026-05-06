#!/usr/bin/env python3
"""
Orchestrator Web UI + REST API
FastAPI + SQLite + SSE for Live-Updates
GitLab Webhooks + MR/Review Lifecycle
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import traceback
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import logging
import time as _time
import threading as _threading

class Metrics:
    def __init__(self):
        self._lock = _threading.Lock()
        self._counters = {}
        self._gauges = {}
        self._histograms = {}

    def inc(self, name: str, labels: dict = None, value: float = 1):
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def set(self, name: str, value: float, labels: dict = None):
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def observe(self, name: str, value: float, labels: dict = None):
        key = self._key(name, labels)
        with self._lock:
            if key not in self._histograms:
                self._histograms[key] = []
            self._histograms[key].append(value)

    def _key(self, name, labels):
        if not labels:
            return name
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return f'{name}{{{",".join(parts)}}}'

    def render(self) -> str:
        lines = []
        with self._lock:
            seen = set()
            for key, value in sorted(self._counters.items()):
                base = key.split("{")[0] if "{" in key else key
                if base not in seen:
                    lines.append(f"# TYPE {base} counter")
                    seen.add(base)
                lines.append(f"{key} {value}")
            for key, value in sorted(self._gauges.items()):
                base = key.split("{")[0] if "{" in key else key
                if base not in seen:
                    lines.append(f"# TYPE {base} gauge")
                    seen.add(base)
                lines.append(f"{key} {value}")
            for key, values in sorted(self._histograms.items()):
                base = key.split("{")[0] if "{" in key else key
                if base not in seen:
                    lines.append(f"# TYPE {base} summary")
                    seen.add(base)
                sorted_vals = sorted(values)
                for quantile in (0.5, 0.9, 0.99):
                    idx = min(int(len(sorted_vals) * quantile), len(sorted_vals) - 1)
                    lines.append(f'{key}{{quantile="{quantile}"}} {sorted_vals[idx]}')
                lines.append(f"{key}_count {len(values)}")
                lines.append(f"{key}_sum {sum(values)}")
        return "\n".join(lines) + "\n"

metrics = Metrics()

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "timestamp": _time.strftime("%Y-%m-%dT%H:%M:%S%z", _time.localtime(record.created)),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("ticket_id", "agent_id", "pod_name", "event", "correlation_id"):
            val = getattr(record, key, None)
            if val:
                log_entry[key] = val
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)

def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("hivemind")

_webhook_dedup: Dict[str, float] = {}
_WEBHOOK_DEDUP_TTL = 300
_webhook_dedup_lock = asyncio.Lock()

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

from database import (
    add_step, create_ticket, ensure_agent_pool,
    get_all_agents, get_all_steps, get_idle_agents,
    get_max_agents, get_next_queue_item, get_or_create_agent,
    get_queue, get_ticket, get_tickets,
    set_agent_status, set_max_agents,
    assign_queue_item, complete_queue_item, fail_queue_item,
    update_ticket_status, update_ticket_review,
    get_all_settings, set_setting, get_setting,
    get_tickets_with_queue,
    set_ticket_mr_url,
    set_ticket_workspace,
    set_ticket_ai_planning,
    requeue_ticket, reopen_ticket,
    get_failed_tickets,
    get_open_mr_tickets,
    update_ticket_mr_tracking,
    get_db,
    get_mcp_servers, get_enabled_mcp_servers, add_mcp_server, update_mcp_server, delete_mcp_server,
    get_agent_instructions, get_enabled_agent_instructions, add_agent_instruction, update_agent_instruction, delete_agent_instruction,
    create_agent, update_agent_profile, delete_agent as db_delete_agent,
    set_agent_skills, set_agent_instruction_assignments,
    get_agent_with_profile, get_all_agents_with_profiles,
    get_agent_mcp_servers, get_agent_assigned_instructions,
    add_ticket_comment, get_ticket_comments,
    set_agent_repo_affinities, find_best_agent_for_repo, get_all_repo_names,
    get_agent_repo_affinities,
    get_opencode_plugins, get_enabled_opencode_plugins, get_enabled_plugin_names,
    add_opencode_plugin, update_opencode_plugin, delete_opencode_plugin,
    get_agent_memory_blocks, set_agent_memory_block, get_agent_memory_as_markdown,
    delete_agent_memory_block, seed_default_memory_blocks,
    get_all_repos, get_repo, add_repo as db_add_repo, delete_repo as db_delete_repo,
    update_repo as db_update_repo, import_repos_from_config, set_repo_active,
    stop_ticket,
)

# Lazy import of main.py to avoid circular dependency
_main_module = None

def _get_main():
    global _main_module
    if _main_module is None:
        import main as _m
        _main_module = _m
    return _main_module


app = FastAPI(title="HiveMind Orchestrator", version="1.0.0")

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(_uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response

HIVEMIND_API_KEY = os.getenv("HIVEMIND_API_KEY", "")

@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    if not HIVEMIND_API_KEY:
        return await call_next(request)
    path = request.url.path
    exempt_prefixes = ("/healthz", "/readyz", "/metrics", "/static/", "/webhooks/", "/agent-session/", "/api/stream")
    if any(path.startswith(p) for p in exempt_prefixes) or not path.startswith("/api/"):
        return await call_next(request)
    api_key = request.headers.get("X-API-Key", "")
    if not api_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
    if api_key != HIVEMIND_API_KEY:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key"})
    return await call_next(request)

_rate_limit_store: Dict[str, list] = {}
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path != "/api/tickets" or request.method != "POST":
        return await call_next(request)
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    if client_ip not in _rate_limit_store:
        _rate_limit_store[client_ip] = []
    _rate_limit_store[client_ip] = [t for t in _rate_limit_store[client_ip] if now - t < 60]
    if len(_rate_limit_store[client_ip]) >= RATE_LIMIT_PER_MINUTE:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    _rate_limit_store[client_ip].append(now)
    return await call_next(request)

# ── Health Checks ──────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.get("/readyz")
def readyz():
    try:
        get_db()
        w = _get_worker()
        return {"status": "ok", "repos_initialized": w._init_done}
    except Exception:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "not ready"})


# ── Workspace Builder ──────────────────────────────────────────────

class WorkspaceBuilder:
    """Builds workspaces and spawns K8s pods when a ticket starts."""

    def __init__(self):
        self._main = _get_main()
        self.config = self._main.OrchestratorConfig.from_file(
            self._main.ORCHESTRATOR_CONFIG
        )

        db_ollama_host = get_setting("ollama_host")
        db_ollama_model = get_setting("ollama_model")
        if db_ollama_host:
            self.config.ollama_host = db_ollama_host
        if db_ollama_model:
            self.config.ollama_model = db_ollama_model

        Path(self.config.pvc_mount_path).mkdir(parents=True, exist_ok=True)
        self.git = self._main.RepoManager(self.config.pvc_mount_path, self.config.track_branch, self.config.branch_fallback_order)
        self.leankg = self._main.LeanKGManager(self.config)
        self.llm = self._main.OllamaClient(self.config.ollama_host, self.config.ollama_model)
        self._statuses = []
        self._init_done = False
        self._init_lock = asyncio.Lock()

    @property
    def repositories(self):
        return [self._main.RepoConfig.from_dict(r) for r in get_all_repos(active_only=True)]

    def _ensure_init(self):
        if self._init_done:
            return
        import threading
        if not hasattr(self, '_init_thread_lock'):
            self._init_thread_lock = threading.Lock()
        with self._init_thread_lock:
            if self._init_done:
                return
            self._main.configure_git_credentials()
            self._statuses = self.git.update_all(self.repositories)
            if self.config.leankg_enabled:
                self.leankg.index_all(self._statuses)
            self._init_done = True

    async def _aensure_init(self):
        if self._init_done:
            return
        async with self._init_lock:
            if self._init_done:
                return
            log.info("Initializing repos (first request)...")
            self._main.configure_git_credentials()
            repos = self.repositories
            for r in repos:
                try:
                    status = await self.git.aensure_repo(r)
                    self._statuses.append(status)
                    log.info(f"{r.name}: initialized")
                except Exception as e:
                    log.error(f"{r.name}: init failed: {e}")
            if self.config.leankg_enabled:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.leankg.index_all, self._statuses)
            self._init_done = True
            log.info("Repo initialization complete")

    async def build_and_spawn(self, ticket):
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._build_and_spawn_sync, ticket)
        except Exception as e:
            log.error(f"Workspace-Builder error: {e}", exc_info=True)
            traceback.print_exc()
            return f"failed: {e}", None, None

    def _build_and_spawn_sync(self, ticket):
        self._ensure_init()

        manual_repos = getattr(ticket, 'selected_repos', None) or []

        if manual_repos:
            log.info(f"Ticket {ticket.id} has manual repo selection", extra={"ticket_id": ticket.id})
            selected_configs = [r for r in self.repositories if r.name in manual_repos]
            if not selected_configs:
                log.error(f"No matching repositories for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                return "failed: no matching repositories", None, None
            analysis = {
                "selected_repos": manual_repos,
                "primary_repo": manual_repos[0],
                "complexity": "Medium",
                "estimated_hours": 2,
                "reasoning": "Repositories manually selected by user",
            }
            selected_names = set(manual_repos)
        else:
            analysis = None
            max_llm_retries = 3
            retry_delays = [10, 30, 60]

            if self.llm.is_available():
                for attempt in range(max_llm_retries):
                    try:
                        analysis = self.llm.analyze_repos_for_ticket(
                            ticket, self._statuses, self.leankg
                        )
                        if analysis:
                            break
                    except RuntimeError as e:
                        err_str = str(e)
                        if attempt < max_llm_retries - 1:
                            delay = retry_delays[attempt]
                            log.warning(f"LLM analysis failed for ticket {ticket.id}: {e}", extra={"ticket_id": ticket.id, "attempt": attempt+1})
                            log.warning(f"LLM retry in {delay}s...", extra={"ticket_id": ticket.id, "attempt": attempt+1})
                            import time
                            time.sleep(delay)
                        else:
                            log.error(f"LLM analysis failed after {max_llm_retries} attempts for ticket {ticket.id}: {e}", extra={"ticket_id": ticket.id})
            else:
                log.error(f"Ollama not reachable ({self.llm.host}) – ticket {ticket.id}", extra={"ticket_id": ticket.id})

            if not analysis:
                log.error(f"No AI analysis for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                return "failed: no llm analysis", None, None

            selected_names = set(analysis.get("selected_repos", []))
            selected_configs = [r for r in self.repositories if r.name in selected_names]
            if not selected_configs:
                log.error(f"No matching repositories for ticket {ticket.id}", extra={"ticket_id": ticket.id})
                return "failed: no matching repositories", None, None

        prompt = self._main.generate_assignment_prompt(ticket, analysis, selected_configs)

        # Merge retry context from ticket data into analysis (if present)
        retry_ctx = getattr(self, '_retry_context', {})
        if retry_ctx:
            analysis.update(retry_ctx)
            prompt = self._main.generate_assignment_prompt(ticket, analysis, selected_configs)

        set_ticket_ai_planning(ticket.id, analysis)

        workspace_dir = Path(self.config.work_dir) / f"workspace_{ticket.id}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        self._main.create_opencode_config(workspace_dir / ".opencode", ticket, selected_configs, analysis, prompt)
        self._main.create_launch_scripts(workspace_dir)

        self._main.spawn_agent_pod(ticket, selected_configs, prompt, analysis)

        pod_name = f"agent-worker-{ticket.id.lower()}"
        os.environ[f"AGENT_POD_{ticket.id}"] = pod_name

        return "running", workspace_dir, pod_name


# ── GitLab Webhook Handler ────────────────────────────────────────

GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")

def verify_gitlab_webhook(body: bytes, signature: str) -> bool:
    """Verifies GitLab webhook HMAC signature."""
    if not GITLAB_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    expected = hmac.new(
        GITLAB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _is_duplicate_webhook(event_id: str) -> bool:
    now = _time.time()
    if event_id in _webhook_dedup:
        return True
    expired = [k for k, v in _webhook_dedup.items() if now - v > _WEBHOOK_DEDUP_TTL]
    for k in expired:
        del _webhook_dedup[k]
    _webhook_dedup[event_id] = now
    return False


async def handle_gitlab_issue(payload: Dict):
    """Processes GitLab Issue webhook: creates ticket + queue entry."""
    issue = payload.get("object_attributes", {})
    if not issue:
        return {"ok": False, "error": "No issue data"}

    action = issue.get("action", "")
    if action not in ("open", "update", "reopen"):
        return {"ok": True, "ignored": f"action={action}"}

    ticket_id = f"GL-{issue.get('iid', issue.get('id', 'UNKNOWN'))}"

    # Check if ticket already exists
    existing = get_ticket(ticket_id)
    if existing:
        return {"ok": True, "id": ticket_id, "status": "already_exists"}

    # Extract labels from GitLab
    raw_labels = issue.get("labels", [])
    labels = []
    if isinstance(raw_labels, list):
        labels = [l.get("title", l) if isinstance(l, dict) else l for l in raw_labels]
    elif isinstance(raw_labels, str):
        labels = raw_labels.split(",")

    data = {
        "id": ticket_id,
        "title": issue.get("title", ""),
        "description": issue.get("description", ""),
        "issue_type": "Task",
        "priority": "Medium",
        "labels": labels,
    }

    ticket_id = create_ticket(data)
    log.info(f"GitLab Issue → Ticket {ticket_id} created", extra={"ticket_id": ticket_id, "event": "ticket_created"})

    await broadcast_event("ticket_created", {"ticket_id": ticket_id, "title": data["title"]})
    await broadcast_event("queue_updated", get_queue())

    return {"ok": True, "id": ticket_id, "status": "queued"}


async def handle_gitlab_mr(payload: Dict):
    """Processes GitLab Merge Request webhook for Review Lifecycle."""
    mr = payload.get("object_attributes", {})
    if not mr:
        return {"ok": False, "error": "No MR data"}

    action = mr.get("action", "")
    state = mr.get("state", "")
    mr_url = mr.get("url", "")
    source_branch = mr.get("source_branch", "")

    # Find ticket from branch name (feature/PROJ-123 or feature/TASK-123-slug)
    ticket_id = None
    cleaned = source_branch.replace("feature/", "")
    for prefix in ("PROJ-", "BUG-", "TASK-", "GL-"):
        if cleaned.startswith(prefix):
            idx = len(prefix)
            while idx < len(cleaned) and cleaned[idx] != '-':
                idx += 1
            ticket_id = cleaned[:idx]
            break
    if not ticket_id:
        for part in cleaned.split("-"):
            if part.startswith(("PROJ", "BUG", "TASK", "GL")):
                ticket_id = part
                break

    if not ticket_id:
        return {"ok": False, "error": "Could not extract ticket from branch"}

    ticket = get_ticket(ticket_id)
    if not ticket:
        return {"ok": False, "error": f"Ticket {ticket_id} not found"}

    # MR closed/merged
    if action in ("merge", "close") or state == "merged":
        update_ticket_review(ticket_id, "approved", f"MR {action}: {mr_url}", mr_url)
        update_ticket_status(ticket_id, "merged")
        log.info(f"MR merged → Ticket {ticket_id} set to 'merged'", extra={"ticket_id": ticket_id, "event": "ticket_merged"})
        await broadcast_event("ticket_merged", {"ticket_id": ticket_id, "mr_url": mr_url})

        # Cleanup agent pod
        agent_pod = os.environ.get(f"AGENT_POD_{ticket_id}")
        if agent_pod:
            _kubectl = _get_main()._kubectl
            _kubectl(f"delete pod {agent_pod} -n {os.getenv('AGENT_NAMESPACE', 'hivemind')} --grace-period=0 --force")
            log.info(f"Agent pod {agent_pod} deleted", extra={"ticket_id": ticket_id, "pod_name": agent_pod})
        _cleanup_agent_resources(ticket_id)

    # MR updated (new commits, review changes)
    elif action == "update" and state == "opened":
        # Check if review changes were requested
        # In practice this would come through separate note/comment webhooks
        pass

    # MR reopened (after reject → retry)
    elif action == "reopen":
        # Re-queue ticket
        update_ticket_review(ticket_id, "changes_requested", f"MR reopened: {mr_url}", mr_url)
        update_ticket_status(ticket_id, "queued")
        log.info(f"MR reopened → Ticket {ticket_id} added to re-queue", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
        await broadcast_event("ticket_queued", {"ticket_id": ticket_id, "mr_url": mr_url})

    return {"ok": True, "ticket_id": ticket_id, "action": action}


def _cleanup_agent_resources(ticket_id: str):
    """Delete ConfigMaps created for an agent pod."""
    from k8s_client import delete_configmap
    namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
    pod_name = f"agent-worker-{ticket_id.lower()}"
    for suffix in ("repos", "assignment", "opencode", "memory"):
        delete_configmap(f"{pod_name}-{suffix}", namespace)
    log.info(f"ConfigMaps cleaned up for {pod_name}", extra={"ticket_id": ticket_id, "event": "configmap_cleanup"})


async def review_lifecycle_monitor():
    """Background task: Monitors MR status, pipeline failures and new comments via GitLab API."""
    log.info("Review Lifecycle Monitor started")
    await asyncio.sleep(15)  # Wait until server is ready

    while not _shutdown_requested:
        try:
            gitlab_token = os.getenv("GITLAB_TOKEN", "")
            gitlab_host = os.getenv("GITLAB_HOST") or ""
            if not gitlab_token:
                await asyncio.sleep(60)
                continue

            tickets = get_open_mr_tickets()
            for t in tickets:
                ticket_id = t["id"]
                mr_url = t.get("mr_url", "")
                mr_status = t.get("mr_status", "none")
                status = t.get("status", "")

                if not mr_url or "gitlab" not in mr_url:
                    continue

                try:
                    mr_data = _fetch_mr(gitlab_host, gitlab_token, mr_url)
                    if not mr_data:
                        continue

                    mr_state = mr_data.get("state", "")

                    # MR merged → complete ticket
                    if mr_state == "merged":
                        update_ticket_status(ticket_id, "completed")
                        _cleanup_agent_resources(ticket_id)
                        update_ticket_review(ticket_id, "approved", "", mr_url)
                        add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Ticket completed: MR was merged.")
                        log.info(f"Ticket {ticket_id}: MR merged", extra={"ticket_id": ticket_id, "event": "ticket_merged"})
                        await broadcast_event("ticket_merged", {"ticket_id": ticket_id})
                        continue

                    # MR closed → mark as failed or re-queue
                    if mr_state == "closed":
                        update_ticket_review(ticket_id, "changes_requested", "MR closed without merge", mr_url)
                        if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                            log.info(f"Ticket {ticket_id}: MR closed → re-queued", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                            await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "MR closed"})
                        else:
                            update_ticket_status(ticket_id, "failed")
                            metrics.inc("hivemind_tickets_failed_total")
                            _cleanup_agent_resources(ticket_id)
                            log.error(f"Ticket {ticket_id}: MR closed → failed (max retries)", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                        continue

                    # MR is open → check pipeline + conflicts + comments
                    if mr_state == "opened":
                        project_path, mr_iid = _parse_mr_url(mr_url)
                        if not project_path:
                            continue

                        update_ticket_mr_tracking(
                            ticket_id,
                            project_path=project_path,
                            mr_iid=int(mr_iid) if mr_iid else None,
                        )

                        # Check merge conflict (merge_status from GitLab)
                        merge_status = mr_data.get("merge_status", "")
                        has_conflicts = mr_data.get("has_conflicts", False)
                        if merge_status == "cannot_be_merged" or has_conflicts:
                            last_conflict = t.get("mr_conflict_status", "none")
                            if last_conflict != "conflict_detected":
                                log.warning(f"Ticket {ticket_id}: Merge conflict detected → re-queue for conflict resolution", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                                update_ticket_mr_tracking(ticket_id, conflict_status="conflict_detected")
                                update_ticket_review(ticket_id, "changes_requested",
                                    f"Merge conflict: Branch {mr_data.get('source_branch', '?')} has conflicts with {mr_data.get('target_branch', 'development')}",
                                    mr_url)
                                add_ticket_comment(ticket_id, author="system", comment_type="system",
                                    content=f"⚠️ Merge conflict detected. Agent will automatically rebase the branch.")
                                if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                                    await broadcast_event("ticket_requeued", {
                                        "ticket_id": ticket_id,
                                        "reason": "Merge conflict detected",
                                        "conflict": True,
                                    })
                                    await broadcast_event("queue_updated", get_queue())
                                else:
                                    update_ticket_status(ticket_id, "failed")
                                    metrics.inc("hivemind_tickets_failed_total")
                                    _cleanup_agent_resources(ticket_id)
                                    log.error(f"Ticket {ticket_id}: Merge conflict → failed (max retries)", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                                continue
                        elif merge_status == "can_be_merged":
                            update_ticket_mr_tracking(ticket_id, conflict_status="none")

                        # Check pipeline status
                        pipeline = mr_data.get("head_pipeline") or mr_data.get("pipeline")
                        pipeline_status = pipeline.get("status", "unknown") if pipeline else "unknown"
                        last_pipeline = t.get("mr_pipeline_status", "unknown")

                        if pipeline_status == "failed" and last_pipeline != "failed":
                            log.info(f"Ticket {ticket_id}: Pipeline failed → re-queue", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                            update_ticket_mr_tracking(ticket_id, pipeline_status="failed")
                            update_ticket_review(ticket_id, "changes_requested", "Pipeline failed", mr_url)
                            if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                                await broadcast_event("ticket_requeued", {
                                    "ticket_id": ticket_id,
                                    "reason": "Pipeline failed",
                                })
                        elif pipeline_status != "failed":
                            update_ticket_mr_tracking(ticket_id, pipeline_status=pipeline_status)

                        # Check for new comments
                        await _check_mr_comments(
                            ticket_id, t, mr_url, project_path, mr_iid,
                            gitlab_host, gitlab_token
                        )

                except Exception as e:
                    log.error(f"MR check error for {ticket_id}: {e}", extra={"ticket_id": ticket_id})
                    traceback.print_exc()

            await asyncio.sleep(60)
        except Exception as e:
            log.error(f"Review monitor error: {e}", exc_info=True)
            await asyncio.sleep(60)


def _fetch_mr(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    """Fetches MR data from GitLab API using httpx-based client."""
    from gitlab_client import fetch_mr_sync
    return fetch_mr_sync(gitlab_host, gitlab_token, mr_url)


def _gitlab_api_get(path: str, gitlab_host: str = None, gitlab_token: str = None, params: dict = None) -> Optional[List[Dict]]:
    """GitLab API GET request with pagination using httpx."""
    from gitlab_client import gitlab_get_sync
    return gitlab_get_sync(path, params, gitlab_host, gitlab_token)


def _ai_enrich_repo(repo_info: Dict) -> Dict:
    from main import _ai_enrich_repo as _main_enrich
    return _main_enrich(repo_info)


def _parse_mr_url(mr_url: str) -> tuple:
    """Extracts (project_path, mr_iid) from a GitLab MR URL."""
    from gitlab_client import parse_mr_url
    return parse_mr_url(mr_url)


async def _check_mr_comments(ticket_id, ticket, mr_url, project_path, mr_iid, gitlab_host, gitlab_token):
    """Checks new comments on an MR and re-queues on review feedback."""
    from gitlab_client import fetch_mr_comments
    last_note_id = ticket.get("mr_last_note_id", 0) or 0
    notes = await fetch_mr_comments(gitlab_host, gitlab_token, project_path, mr_iid)
    if not notes:
        return

    agent_bot_username = os.getenv("GITLAB_BOT_USERNAME", "hivemind")
    new_notes = [n for n in notes
                 if n.get("id", 0) > last_note_id
                 and not n.get("system", False)
                 and n.get("author", {}).get("username", "") != agent_bot_username]

    if not new_notes:
        return

    latest_note_id = max(n["id"] for n in notes)
    update_ticket_mr_tracking(ticket_id, last_note_id=latest_note_id)

    for note in new_notes:
        author = note.get("author", {}).get("username", "unknown")
        body = note.get("body", "").strip()
        log.info(f"Ticket {ticket_id}: New comment from {author}: {body[:80]}...", extra={"ticket_id": ticket_id})

        lower_body = body.lower()
        is_changes_requested = any(kw in lower_body for kw in
            ["changes requested", "rework", "fix", "error", "please fix", "please correct",
             "not ok", "failing", "typecheck", "pipeline failed", "broken"])

        if is_changes_requested:
            update_ticket_review(ticket_id, "changes_requested", body[:500], mr_url)
            if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                log.info(f"Ticket {ticket_id}: Changes requested → re-queued", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                await broadcast_event("ticket_requeued", {
                    "ticket_id": ticket_id,
                    "reason": f"Review feedback from {author}",
                    "comment": body[:200],
                })
                await broadcast_event("queue_updated", get_queue())
            else:
                update_ticket_status(ticket_id, "failed")
                metrics.inc("hivemind_tickets_failed_total")
                _cleanup_agent_resources(ticket_id)
                log.error(f"Ticket {ticket_id}: Changes requested → failed (max retries)", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
            return


AGENT_RETRY_DELAY = int(os.getenv("AGENT_RETRY_DELAY", "120"))
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))
AGENT_STALE_TIMEOUT = int(os.getenv("AGENT_STALE_TIMEOUT", "3600"))


async def agent_pod_monitor():
    """Background task: Checks agent pod status, re-queues failed tickets, marks completed ones and updates MR URLs."""
    log.info(f"Agent Pod Monitor started (retry-delay: {AGENT_RETRY_DELAY}s, max-retries: {AGENT_MAX_RETRIES})")
    while not _shutdown_requested:
        try:
            # Detect completed pods (Succeeded/Completed)
            completed_pod_ids = set()
            list_rc, list_out, _ = _get_main()._kubectl("get pods -n hivemind -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.status.phase}{\"\\n\"}{end}'")
            if list_rc == 0:
                for line in list_out.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and parts[1] in ("Succeeded", "Completed"):
                        pn = parts[0]
                        if pn.startswith("agent-worker-"):
                            completed_pod_ids.add(pn.replace("agent-worker-", ""))

            # Check running tickets: Pod completed → ticket completed
            for t in [t for t in get_tickets(status=None) if t.get("status") == "running"]:
                ticket_id = t["id"]
                pod_name = f"agent-worker-{ticket_id.lower()}"
                namespace = os.getenv("AGENT_NAMESPACE", "hivemind")

                # Grace period: skip pod check if ticket was just marked running (within 60s)
                updated_at = t.get("updated_at") or t.get("created_at") or ""
                if updated_at:
                    try:
                        started = datetime.fromisoformat(updated_at)
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                        if elapsed < 60:
                            continue
                    except (ValueError, TypeError):
                        pass

                rc, out, err = _get_main()._kubectl(f"get pod {pod_name} -n {namespace} -o jsonpath='{{.status.phase}}' 2>/dev/null")
                if rc != 0:
                    if ticket_id.lower() in completed_pod_ids or ticket_id in completed_pod_ids:
                        update_ticket_status(ticket_id, "completed")
                        metrics.inc("hivemind_tickets_completed_total")
                        _cleanup_agent_resources(ticket_id)
                        set_agent_status(t.get("agent_id", "") or ticket_id.lower(), "idle")
                        add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Agent pod completed. Ticket was marked as completed.")
                        log.info(f"Ticket {ticket_id}: pod completed", extra={"ticket_id": ticket_id, "event": "ticket_completed"})
                        await broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                        await broadcast_event("queue_updated", get_queue())
                    else:
                        # Pod not found and not completed — was force-deleted or crashed
                        # Re-queue the ticket so it gets reassigned
                        agent_id = t.get("agent_id", "") or ticket_id.lower()
                        retry_count = t.get("retry_count", 0)
                        if retry_count < AGENT_MAX_RETRIES:
                            log.info(f"Ticket {ticket_id}: Pod not found → re-queued", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                            requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                            set_agent_status(agent_id, "idle")
                            await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "Pod not found"})
                            await broadcast_event("queue_updated", get_queue())
                        else:
                            log.error(f"Ticket {ticket_id}: Pod not found, max retries reached → failed", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                            update_ticket_status(ticket_id, "failed")
                            metrics.inc("hivemind_tickets_failed_total")
                            _cleanup_agent_resources(ticket_id)
                            set_agent_status(agent_id, "idle")
                            add_ticket_comment(ticket_id, author="system", comment_type="system", content="Agent pod not found and max retries reached.")
                            await broadcast_event("ticket_failed", {"ticket_id": ticket_id})
                    continue

                phase = out.strip().strip("'\"")

                if phase in ("Succeeded", "Completed"):
                    update_ticket_status(ticket_id, "completed")
                    metrics.inc("hivemind_tickets_completed_total")
                    _cleanup_agent_resources(ticket_id)
                    set_agent_status(t.get("agent_id", "") or ticket_id.lower(), "idle")
                    add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Agent pod status: {phase}. Ticket was marked as completed.")
                    log.info(f"Ticket {ticket_id}: pod {phase}, marking completed", extra={"ticket_id": ticket_id, "event": "ticket_completed"})
                    await broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                    await broadcast_event("queue_updated", get_queue())
                    # Cleanup: delete pod after completion
                    try:
                        _get_main()._kubectl(f"delete pod {pod_name} -n {namespace} --grace-period=0 --force 2>/dev/null")
                        log.info(f"Pod {pod_name} deleted (completed)", extra={"ticket_id": ticket_id, "pod_name": pod_name})
                    except Exception:
                        pass
                    continue

                # Pod not found and not in completed_pod_ids → check if pod exists
                if phase in ("Failed", "Error"):
                    retry_count = t.get("retry_count", 0)
                    updated_at = t.get("updated_at")
                    if updated_at:
                        try:
                            failed_at = datetime.fromisoformat(updated_at)
                            if failed_at.tzinfo is None:
                                failed_at = failed_at.replace(tzinfo=timezone.utc)
                            elapsed = (datetime.now(timezone.utc) - failed_at).total_seconds()
                            if elapsed < AGENT_RETRY_DELAY:
                                remaining = int(AGENT_RETRY_DELAY - elapsed)
                                log.info(f"Ticket {ticket_id}: retry in {remaining}s", extra={"ticket_id": ticket_id})
                                continue
                        except (ValueError, TypeError):
                            pass

                    if retry_count >= AGENT_MAX_RETRIES:
                        log.error(f"Ticket {ticket_id}: max retries ({AGENT_MAX_RETRIES}) reached, staying failed", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                        continue

                    success = requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                    if success:
                        new_retry = retry_count + 1
                        log.info(f"Ticket {ticket_id}: re-queued (retry {new_retry}/{AGENT_MAX_RETRIES})", extra={"ticket_id": ticket_id, "event": "ticket_requeued", "agent_id": agent_id})
                        await broadcast_event("ticket_requeued", {
                            "ticket_id": ticket_id,
                            "retry_count": new_retry,
                            "max_retries": AGENT_MAX_RETRIES,
                            "reason": f"Pod {phase}",
                        })
                        await broadcast_event("queue_updated", get_queue())
                    else:
                        log.error(f"Ticket {ticket_id}: Re-queue failed (max retries?)", extra={"ticket_id": ticket_id})
                    # Cleanup failed pod
                    try:
                        _get_main()._kubectl(f"delete pod {pod_name} -n {namespace} --grace-period=0 --force 2>/dev/null")
                        log.info(f"Pod {pod_name} deleted (failed)", extra={"ticket_id": ticket_id, "pod_name": pod_name})
                    except Exception:
                        pass
                    _cleanup_agent_resources(ticket_id)

            # MR-URL Discovery: Find tickets without MR-URL but with branch on GitLab
            gitlab_token = os.getenv("GITLAB_TOKEN", "")
            gitlab_host = os.getenv("GITLAB_HOST") or ""
            if gitlab_token:
                from gitlab_client import search_open_mrs
                all_tickets = get_tickets(status=None)
                for t in all_tickets:
                    if t.get("mr_url") or t.get("status") not in ("completed", "running"):
                        continue
                    ticket_id = t["id"]
                    ticket_title = t.get("title", "")
                    slug = re.sub(r'[^a-z0-9]+', '-', ticket_title.lower()).strip('-')[:40]
                    branch_name = f"feature/{ticket_id.lower()}-{slug}" if slug else f"feature/{ticket_id.lower()}"

                    for repo in _get_worker().repositories:
                        project_path = repo.url.split("://")[-1].replace(".git", "").replace(":", "/") if "://" in repo.url else ""
                        if "/" in project_path and ":" in project_path.split("/")[0]:
                            project_path = "/".join(project_path.split("/")[1:])
                        if not project_path:
                            continue
                        try:
                            mrs = await search_open_mrs(gitlab_host, gitlab_token, project_path, branch_name)
                            if mrs:
                                mr_url = mrs[0].get("web_url", "")
                                if mr_url and not t.get("mr_url"):
                                    set_ticket_mr_url(ticket_id, mr_url)
                                    add_ticket_comment(ticket_id, author="system", comment_type="mr_created", content=f"Merge Request created: {mr_url}")
                                    update_ticket_mr_tracking(ticket_id, pipeline_status=mrs[0].get("head_pipeline", {}).get("status", "unknown") if mrs[0].get("head_pipeline") else "unknown")
                                    conn = get_db()
                                    conn.execute("UPDATE tickets SET mr_status = 'open' WHERE id = %s", (ticket_id,))
                                    conn.commit()
                                    conn.close()
                                    log.info(f"Ticket {ticket_id}: MR found → {mr_url}", extra={"ticket_id": ticket_id, "event": "mr_found"})
                                break
                        except Exception:
                            pass

            # Stale tickets: Running tickets without active pod, older than 60 min → completed
            stale_threshold = int(os.getenv("AGENT_STALE_TIMEOUT", "3600"))
            for t in [t for t in get_tickets(status=None) if t.get("status") == "running"]:
                ticket_id = t["id"]
                pod_name = f"agent-worker-{ticket_id.lower()}"
                ns = os.getenv("AGENT_NAMESPACE", "hivemind")
                pod_still_active = False
                try:
                    rc, out, err = _get_main()._kubectl(f"get pod {pod_name} -n {ns} -o jsonpath='{{.status.phase}}'")
                    if rc == 0:
                        phase = out.strip().strip("'\"")
                        if phase in ("Running", "Pending", "ContainerCreating"):
                            pod_still_active = True
                    else:
                        # kubectl failed - check broader to be safe
                        rc2, out2, _ = _get_main()._kubectl(f"get pod {pod_name} -n {ns} -o name 2>/dev/null")
                        if rc2 == 0 and pod_name in out2:
                            pod_still_active = True  # Pod exists but phase query failed, assume active
                except Exception:
                    pod_still_active = True  # On API error, don't mark as stale

                if pod_still_active:
                    continue

                updated_at = t.get("updated_at")
                if updated_at:
                    try:
                        updated_dt = datetime.fromisoformat(updated_at)
                        if updated_dt.tzinfo is None:
                            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
                        elapsed = (datetime.now(timezone.utc) - updated_dt).total_seconds()
                        if elapsed > stale_threshold:
                            update_ticket_status(ticket_id, "completed")
                            _cleanup_agent_resources(ticket_id)
                            agent_id = t.get("agent_id", "") or f"agent-{ticket_id.lower()}"
                            set_agent_status(agent_id, "idle")
                            add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Pod disappeared (>30min). Ticket automatically marked as completed.")
                            log.info(f"Ticket {ticket_id}: Pod disappeared, >30min old → 'completed', Agent {agent_id} → 'idle'", extra={"ticket_id": ticket_id, "agent_id": agent_id, "event": "ticket_completed"})
                            await broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                            await broadcast_event("queue_updated", get_queue())
                            # Cleanup: delete stale pod if still present
                            try:
                                _get_main()._kubectl(f"delete pod {pod_name} -n {ns} --grace-period=0 --force 2>/dev/null")
                            except Exception:
                                pass
                    except (ValueError, TypeError):
                        pass

            # Orphaned agent cleanup: agents marked "running" but their ticket is not "running"
            all_agents = get_all_agents()
            all_tickets = {t["id"]: t for t in get_tickets(status=None)}

            # Deduplicate: only one agent per ticket (keep the agent that matches ticket.agent_id)
            ticket_owners = {}
            for a in all_agents:
                if a["status"] != "running" or not a.get("current_ticket_id"):
                    continue
                tid = a["current_ticket_id"]
                if tid in ticket_owners:
                    ticket_data = all_tickets.get(tid)
                    owner_id = ticket_data.get("agent_id", "") if ticket_data else ""
                    if owner_id and a["id"] == owner_id:
                        set_agent_status(ticket_owners[tid], "idle")
                        log.info(f"Agent {ticket_owners[tid]}: duplicate assignment for {tid}, set idle (keeping owner {a['id']})", extra={"agent_id": ticket_owners[tid], "ticket_id": tid, "event": "agent_orphan_cleanup"})
                        ticket_owners[tid] = a["id"]
                    else:
                        set_agent_status(a["id"], "idle")
                        log.info(f"Agent {a['id']}: duplicate assignment for {tid}, set idle (keeping {ticket_owners[tid]})", extra={"agent_id": a["id"], "ticket_id": tid, "event": "agent_orphan_cleanup"})
                else:
                    ticket_owners[tid] = a["id"]

            for a in all_agents:
                if a["status"] != "running":
                    continue
                tid = a.get("current_ticket_id")
                if not tid:
                    set_agent_status(a["id"], "idle")
                    log.info(f"Agent {a['id']}: no current_ticket_id, set idle", extra={"agent_id": a["id"], "event": "agent_orphan_cleanup"})
                    continue
                ticket = all_tickets.get(tid)
                if not ticket or ticket.get("status") not in ("running", "queued"):
                    set_agent_status(a["id"], "idle")
                    log.info(f"Agent {a['id']}: ticket {tid} is '{ticket.get('status') if ticket else 'missing'}', set idle", extra={"agent_id": a["id"], "ticket_id": tid, "event": "agent_orphan_cleanup"})

            await asyncio.sleep(30)
        except Exception as e:
            log.error(f"Agent pod monitor error: {e}", exc_info=True)
            await asyncio.sleep(30)


# ── Global Queue Processor ───────────────────────────────────────

_running = False
_shutdown_requested = False
_worker = None


def _get_worker():
    global _worker
    if _worker is None:
        _worker = WorkspaceBuilder()
    return _worker


async def queue_processor():
    """Background task: Assigns tickets to free agents and spawns K8s pods."""
    global _running
    _running = True
    log.info("Queue processor started")

    while _running and not _shutdown_requested:
        try:
            ensure_agent_pool()

            idle = get_idle_agents()
            if not idle:
                await asyncio.sleep(3)
                continue

            next_item = get_next_queue_item()
            if not next_item:
                await asyncio.sleep(3)
                continue

            ticket_data = get_ticket(next_item["ticket_id"])
            if ticket_data and ticket_data.get("status") == "stopped":
                fail_queue_item(next_item["id"])
                continue

            # Smart Agent Selection: Choose agent with highest affinity to primary repo
            primary_repo = ""
            if ticket_data:
                # Phase 1: AI analysis if not yet available
                if not ticket_data.get("ai_planning") and not ticket_data.get("selected_repos"):
                    try:
                        w = _get_worker()
                        await w._aensure_init()
                        loop = asyncio.get_event_loop()
                        if w.llm.is_available():
                            _ticket = _get_main().Ticket(
                                id=ticket_data["id"],
                                title=ticket_data.get("title", ""),
                                description=ticket_data.get("description", ""),
                                labels=json.loads(ticket_data.get("labels", "[]")),
                                issue_type=ticket_data.get("issue_type", "Task"),
                                priority=ticket_data.get("priority", "Medium"),
                                agent_id=ticket_data.get("agent_id", ""),
                                selected_repos=json.loads(ticket_data.get("selected_repos", "[]")) if ticket_data.get("selected_repos") else [],
                            )
                            analysis = await loop.run_in_executor(None, w.llm.analyze_repos_for_ticket, _ticket, w._statuses, w.leankg)
                            if analysis:
                                set_ticket_ai_planning(ticket_data["id"], analysis)
                                ticket_data = get_ticket(next_item["ticket_id"])
                                log.info(f"Pre-AI analysis: primary_repo={analysis.get('primary_repo','?')}", extra={"ticket_id": ticket_data["id"]})
                    except Exception as e:
                            log.warning(f"Pre-AI analysis failed: {e}")

                # Extract primary_repo from ai_planning
                if ticket_data.get("ai_planning"):
                    try:
                        planning = ticket_data["ai_planning"]
                        if isinstance(planning, str):
                            planning = json.loads(planning)
                        primary_repo = planning.get("primary_repo", "")
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Fallback: Match repos from description
                if not primary_repo:
                    desc = (ticket_data.get("description", "") + " " + ticket_data.get("title", "")).lower()
                    for a in idle:
                        agent_repos = get_agent_repo_affinities(a["id"]) if hasattr(get_agent_repo_affinities, '__call__') else []
                        if not agent_repos:
                            continue
                        for rn in agent_repos:
                            if rn.lower() in desc:
                                primary_repo = rn
                                break
                        if primary_repo:
                            break

            best_agent_id = find_best_agent_for_repo(primary_repo, idle) if primary_repo else None
            agent = next((a for a in idle if a["id"] == best_agent_id), idle[0]) if best_agent_id else idle[0]

            # Re-verify agent is still idle (may have changed during async AI analysis)
            fresh_idle = get_idle_agents()
            if not any(a["id"] == agent["id"] for a in fresh_idle):
                log.warning(f"Agent {agent['id']} is no longer idle after analysis, skipping", extra={"agent_id": agent["id"], "event": "agent_no_longer_idle"})
                await asyncio.sleep(2)
                continue

            # Re-verify ticket is still queued (may have been assigned by another loop iteration)
            fresh_item = get_next_queue_item()
            if not fresh_item or fresh_item["ticket_id"] != next_item["ticket_id"]:
                log.warning(f"Ticket {next_item['ticket_id']} is no longer next in queue, skipping", extra={"ticket_id": next_item["ticket_id"], "event": "ticket_no_longer_next"})
                continue

            success = assign_queue_item(next_item["id"], agent["id"])
            if not success:
                await asyncio.sleep(2)
                continue

            set_agent_status(agent["id"], "running", next_item["ticket_id"], 0)
            update_ticket_status(next_item["ticket_id"], "running")
            add_step(
                queue_id=next_item["id"],
                ticket_id=next_item["ticket_id"],
                agent_id=agent["id"],
                step_name="Ticket assigned",
                status="running",
                detail=f"Agent {agent['name']} is processing ticket"
            )
            metrics.inc("hivemind_tickets_assigned_total", labels={"agent_id": agent["id"]})
            affinity_msg = f" (repo affinity: {primary_repo})" if best_agent_id else ""
            log.info(f"Ticket {next_item['ticket_id']} assigned to agent {agent['name']}{affinity_msg}", extra={"ticket_id": next_item['ticket_id'], "agent_id": agent['id'], "event": "ticket_assigned"})

            # Spawn K8s agent pod
            if ticket_data:
                main = _get_main()
                ticket = main.Ticket(
                    id=ticket_data["id"],
                    title=ticket_data.get("title", ""),
                    description=ticket_data.get("description", ""),
                    labels=json.loads(ticket_data.get("labels", "[]")),
                    issue_type=ticket_data.get("issue_type", "Task"),
                    priority=ticket_data.get("priority", "Medium"),
                    agent_id=ticket_data.get("agent_id", ""),
                    selected_repos=json.loads(ticket_data.get("selected_repos", "[]")) if ticket_data.get("selected_repos") else [],
                )
                log.info(f"Spawning agent pod for ticket {ticket.id}", extra={"ticket_id": ticket.id, "event": "pod_spawning"})
                w = _get_worker()

                # Pass retry context from ticket data to worker
                w._retry_context = {
                    "review_notes": ticket_data.get("review_notes", ""),
                    "mr_url": ticket_data.get("mr_url", ""),
                    "pipeline_status": ticket_data.get("mr_pipeline_status", ""),
                    "retry_count": ticket_data.get("retry_count", 0),
                    "conflict_status": ticket_data.get("mr_conflict_status", ""),
                }

                status, ws_path, pod_name = await w.build_and_spawn(ticket)
                log.info(f"Ticket {ticket.id}: pod spawn result status={status}, pod={pod_name}", extra={"ticket_id": ticket.id, "pod_name": pod_name})
                if ws_path:
                    set_ticket_workspace(next_item["ticket_id"], str(ws_path), agent["id"])
                    add_step(
                        queue_id=next_item["id"],
                        ticket_id=next_item["ticket_id"],
                        agent_id=agent["id"],
                        step_name="Agent pod started",
                        status="running",
                        detail=f"Pod: {pod_name}, Workspace: {ws_path}"
                    )
                if status.startswith("failed"):
                    fail_queue_item(next_item["id"], status)
                    set_agent_status(agent["id"], "idle")
                    update_ticket_status(next_item["ticket_id"], "failed")
                    metrics.inc("hivemind_tickets_failed_total")
                    await broadcast_event("queue_updated", get_queue())

            await broadcast_event("queue_updated", get_queue())
            await broadcast_event("agent_updated", {
                "agent_id": agent["id"],
                "status": "running",
                "ticket_id": next_item["ticket_id"],
            })

            await asyncio.sleep(2)
        except Exception as e:
            log.error(f"Queue processor error: {e}", exc_info=True)
            await asyncio.sleep(5)


# ── SSE Broadcast ─────────────────────────────────────────────────

clients: List[asyncio.Queue] = []

async def broadcast_event(event: str, data: Dict):
    """Sends event to all connected SSE clients."""
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    disconnected = []
    for q in clients:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            disconnected.append(q)
    for dq in disconnected:
        if dq in clients:
            clients.remove(dq)


async def sse_generator() -> AsyncGenerator[str, None]:
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    clients.append(q)
    try:
        await q.put(f"event: init\ndata: {json.dumps({'message': 'connected'})}\n\n")
        while True:
            msg = await q.get()
            yield msg
    finally:
        if q in clients:
            clients.remove(q)


# ── Agent Session Proxy ─────────────────────────────────────────────

PROXY_PASSWORD = os.getenv("OPENCODE_SERVER_PASSWORD", "")


def _verify_proxy_auth(request: Request) -> bool:
    if not PROXY_PASSWORD:
        return True
    if request.query_params.get("password") == PROXY_PASSWORD:
        return True
    if request.cookies.get("opencode_password") == PROXY_PASSWORD:
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and auth_header[7:] == PROXY_PASSWORD:
        return True
    return False


AGENT_SESSION_TIMEOUT = 30
_agent_http_client: Optional[httpx.AsyncClient] = None


def _get_agent_http_client() -> httpx.AsyncClient:
    global _agent_http_client
    if _agent_http_client is None or _agent_http_client.is_closed:
        _agent_http_client = httpx.AsyncClient(timeout=AGENT_SESSION_TIMEOUT)
    return _agent_http_client


def _resolve_pod_url(ticket_id: str) -> Optional[str]:
    ticket = get_ticket(ticket_id)
    if not ticket or ticket.get("status") not in ("running", "queued"):
        return None
    pod_name = f"agent-worker-{ticket_id.lower()}"
    namespace = os.getenv("AGENT_NAMESPACE", "hivemind")

    # Resolve pod IP via kubectl first (most reliable)
    rc, pod_ip, _ = _get_main()._kubectl(f"get pod {pod_name} -n {namespace} -o jsonpath='{{.status.podIP}}'")
    if rc == 0 and pod_ip.strip():
        return f"http://{pod_ip.strip()}:4096"

    # Check pod status before trying DNS
    rc2, phase, _ = _get_main()._kubectl(f"get pod {pod_name} -n {namespace} -o jsonpath='{{.status.phase}}'")
    if rc2 != 0 or not phase.strip().strip("'\""):
        return None

    # Pod exists but we don't have its IP yet — may still be starting
    # Return DNS URL as last resort (only works with hostname+subdomain)
    return f"http://{pod_name}.agent-session.{namespace}.svc.cluster.local:4096"


async def _proxy_request(ticket_id: str, path: str, request: Request) -> Response:
    base_url = _resolve_pod_url(ticket_id)
    if not base_url:
        raise HTTPException(status_code=404, detail=f"No active agent for ticket {ticket_id}")

    url = f"{base_url}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    client = _get_agent_http_client()

    headers = {}
    for key, value in request.headers.items():
        if key.lower() not in ("host", "transfer-encoding", "connection"):
            headers[key] = value

    body = await request.body()

    try:
        resp = await client.request(
            method=request.method,
            url=url,
            headers=headers,
            content=body,
        )
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        err_msg = str(e)
        if "Name or service not known" in err_msg or "Connection refused" in err_msg:
            raise HTTPException(status_code=503, detail=f"Agent pod not ready yet (still starting): {e}")
        raise HTTPException(status_code=502, detail=f"Agent pod not reachable: {e}")

    metrics.inc("hivemind_proxy_requests_total", labels={"method": request.method})

    response_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in ("transfer-encoding", "content-encoding", "connection"):
            response_headers[key] = value

    content = resp.content
    content_type = resp.headers.get("content-type", "")

    prefix = f"/agent-session/{ticket_id}"

    if "text/html" in content_type:
        html = content.decode("utf-8", errors="replace")
        html = html.replace('href="/', f'href="{prefix}/')
        html = html.replace('src="/', f'src="{prefix}/')
        html = html.replace("href='/", f"href='{prefix}/")
        html = html.replace("src='/", f"src='{prefix}/")
        html = html.replace('content="/', f'content="{prefix}/')
        html = html.replace('action="/', f'action="{prefix}/')
        proxy_script = (
            f'<script>(function(){{'
            f'var P="{prefix}";'
            f'var _f=window.fetch;'
            f'window.fetch=function(input,init){{'
            f'if(typeof input==="string"){{'
            f'if(input.startsWith("/")&&!input.startsWith(P))input=P+input;'
            f'}} else if(input instanceof Request){{'
            f'var nu=new URL(input.url,location.origin);'
            f'if(nu.pathname.startsWith("/")&&!nu.pathname.startsWith(P)){{'
            f'nu.pathname=P+nu.pathname;'
            f'input=new Request(nu.toString(),input);'
            f'}}}}'
            f'return _f.call(this,input,init);'
            f'}};'
            f'var _WS=window.WebSocket;'
            f'window.WebSocket=function(url,protocols){{'
            f'var a=new URL(url,location.origin);'
            f'if(a.pathname.startsWith("/")&&!a.pathname.startsWith(P))a.pathname=P+a.pathname;'
            f'return new _WS(a.toString(),protocols);'
            f'}};'
            f'window.WebSocket.prototype=_WS.prototype;'
            f'window.WebSocket.CONNECTING=_WS.CONNECTING;'
            f'window.WebSocket.OPEN=_WS.OPEN;'
            f'window.WebSocket.CLOSING=_WS.CLOSING;'
            f'window.WebSocket.CLOSED=_WS.CLOSED;'
            f'}})()</script>'
        )
        html = html.replace("</head>", f"{proxy_script}</head>")
        content = html.encode("utf-8")
        response_headers["content-length"] = str(len(content))

    return Response(
        content=content,
        status_code=resp.status_code,
        headers=response_headers,
    )


@app.api_route("/agent-session/{ticket_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def agent_session_proxy(ticket_id: str, path: str, request: Request):
    if not _verify_proxy_auth(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _proxy_request(ticket_id, path, request)


@app.get("/agent-session/{ticket_id}")
async def agent_session_root(ticket_id: str, request: Request):
    if not _verify_proxy_auth(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    return await _proxy_request(ticket_id, "", request)


@app.get("/api/agent-sessions")
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


@app.websocket("/agent-session/{ticket_id}/ws")
async def agent_session_ws(websocket: WebSocket, ticket_id: str):
    if PROXY_PASSWORD:
        pw = websocket.query_params.get("password", "")
        if pw != PROXY_PASSWORD:
            for cookie_name, cookie_value in websocket.cookies.items():
                if cookie_name == "opencode_password" and cookie_value == PROXY_PASSWORD:
                    break
            else:
                auth_header = websocket.headers.get("authorization", "")
                if not (auth_header.startswith("Bearer ") and auth_header[7:] == PROXY_PASSWORD):
                    await websocket.close(code=4001, reason="Authentication required")
                    return

    base_url = _resolve_pod_url(ticket_id)
    if not base_url:
        await websocket.close(code=4040, reason=f"No active agent for ticket {ticket_id}")
        return

    await websocket.accept()

    ws_url = base_url.replace("http://", "ws://") + "/ws"

    import websockets
    try:
        async with websockets.connect(ws_url) as agent_ws:
            async def forward_to_agent():
                try:
                    while True:
                        data = await websocket.receive_text()
                        await agent_ws.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            async def forward_from_agent():
                try:
                    async for message in agent_ws:
                        await websocket.send_text(message)
                except Exception:
                    pass

            await asyncio.gather(forward_to_agent(), forward_from_agent())
    except Exception as e:
        await websocket.close(code=1011, reason=str(e))


# ── REST API: Tickets ────────────────────────────────────────────

@app.get("/api/tickets", response_model=List[Dict])
def api_tickets(status: Optional[str] = None):
    return get_tickets(status)


@app.get("/api/tickets/{ticket_id}")
def api_ticket(ticket_id: str):
    t = get_ticket(ticket_id)
    if not t:
        return {"error": "Not found"}, 404
    return t


@app.get("/api/tickets/{ticket_id}/steps")
def api_ticket_steps(ticket_id: str):
    return get_all_steps(ticket_id=ticket_id)


@app.patch("/api/tickets/{ticket_id}")
async def api_update_ticket(ticket_id: str, req: Request):
    data = await req.json()
    status = data.get("status")
    if status:
        update_ticket_status(ticket_id, status)
        await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": status}


@app.post("/api/tickets/{ticket_id}/reopen")
async def api_reopen_ticket(ticket_id: str):
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
    await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": "queued"}


@app.post("/api/tickets/{ticket_id}/stop")
async def api_stop_ticket(ticket_id: str):
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
            main_mod = _get_main()
            namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
            main_mod._kubectl(["delete", "pod", f"agent-{agent_id}", "-n", namespace, "--force", "--grace-period=0"])
        except Exception:
            pass

    _cleanup_agent_resources(ticket_id)
    add_ticket_comment(ticket_id, author="user", comment_type="system", content="Ticket manually stopped.")
    await broadcast_event("ticket_stopped", {"ticket_id": ticket_id})
    await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": "stopped"}


@app.post("/api/tickets")
async def api_create_ticket(req: Request, background_tasks: BackgroundTasks):
    data = await req.json()
    ticket_id = create_ticket(data)
    metrics.inc("hivemind_tickets_created_total")

    await broadcast_event("ticket_created", {"ticket_id": ticket_id, "title": data.get("title", "")})
    await broadcast_event("queue_updated", get_queue())

    return {"id": ticket_id, "status": "queued"}


@app.post("/api/tickets/preview")
async def api_preview_ticket(req: Request):
    data = await req.json()
    w = _get_worker()
    await w._aensure_init()
    _ticket = _get_main().Ticket(
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
    prompt = _get_main().generate_assignment_prompt(_ticket, analysis, selected_configs) if selected_configs else ""
    return {
        "analysis": analysis,
        "selected_repos": [r.name for r in selected_configs],
        "prompt": prompt,
        "complexity": analysis.get("complexity", "Unknown"),
        "estimated_hours": analysis.get("estimated_hours", "?"),
    }


# ── REST API: Agents ─────────────────────────────────────────────

@app.get("/api/agents")
def api_agents():
    return get_all_agents()


@app.get("/api/agents/{agent_id}")
def api_agent(agent_id: str):
    agent = get_or_create_agent(agent_id)
    return agent


@app.get("/api/agents/{agent_id}/steps")
def api_agent_steps(agent_id: str):
    return get_all_steps(agent_id=agent_id)


@app.get("/api/queue")
def api_queue():
    return get_queue()


@app.get("/api/steps")
def api_all_steps():
    return get_all_steps()


@app.get("/api/config")
def api_config():
    config_data = {"max_agents": get_max_agents()}
    version_path = Path(__file__).resolve().parent.parent / ".version"
    try:
        config_data["version"] = version_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        config_data["version"] = "dev"
    return config_data


@app.post("/api/config")
async def api_set_config(req: Request):
    data = await req.json()
    if "max_agents" in data:
        set_max_agents(int(data["max_agents"]))
        ensure_agent_pool()
        await broadcast_event("config_updated", {"max_agents": data["max_agents"]})
    return {"ok": True}


@app.get("/api/settings")
def api_get_settings():
    return get_all_settings()


@app.post("/api/settings")
async def api_set_settings(req: Request):
    data = await req.json()
    for key, value in data.items():
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        set_setting(key, str(value))
    return {"ok": True}


# ── MCP Servers ─────────────────────────────────────────────

@app.get("/api/mcp-servers")
def api_get_mcp_servers():
    return get_mcp_servers()


@app.post("/api/mcp-servers")
async def api_add_mcp_server(req: Request):
    data = await req.json()
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    name = add_mcp_server(data)
    return {"ok": True, "name": name}


@app.patch("/api/mcp-servers/{name}")
async def api_update_mcp_server(name: str, req: Request):
    data = await req.json()
    update_mcp_server(name, data)
    return {"ok": True}


@app.delete("/api/mcp-servers/{name}")
def api_delete_mcp_server(name: str):
    delete_mcp_server(name)
    return {"ok": True}


# ── Agent Instructions ──────────────────────────────────────

@app.get("/api/agent-instructions")
def api_get_agent_instructions():
    return get_agent_instructions()


@app.post("/api/agent-instructions")
async def api_add_agent_instruction(req: Request):
    data = await req.json()
    if not data.get("name") or not data.get("content"):
        raise HTTPException(status_code=400, detail="name and content are required")
    id = add_agent_instruction(data)
    return {"ok": True, "id": id}


@app.patch("/api/agent-instructions/{id}")
async def api_update_agent_instruction(id: int, req: Request):
    data = await req.json()
    update_agent_instruction(id, data)
    return {"ok": True}


@app.delete("/api/agent-instructions/{id}")
def api_delete_agent_instruction(id: int):
    delete_agent_instruction(id)
    return {"ok": True}


@app.post("/api/agents/{agent_id}/progress")
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


@app.post("/api/agents/{agent_id}/complete")
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


# ── GitLab Webhook Endpoints ─────────────────────────────────────

@app.post("/webhooks/gitlab")
async def gitlab_webhook(req: Request):
    """Receives GitLab webhooks (Issues, MRs, Notes)."""
    body = await req.body()
    event_type = req.headers.get("X-Gitlab-Event", "").lower()
    signature = req.headers.get("X-Gitlab-Token", "")

    if not verify_gitlab_webhook(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    metrics.inc("hivemind_webhooks_received_total", labels={"event_type": event_type})

    event_uuid = req.headers.get("X-Gitlab-Event-UUID", "")
    if event_uuid:
        async with _webhook_dedup_lock:
            is_dup = _is_duplicate_webhook(event_uuid)
        if is_dup:
            return {"ok": True, "status": "duplicate"}

    # Issue Webhook
    if "issue" in event_type:
        return await handle_gitlab_issue(payload)

    # Merge Request Webhook
    elif "merge request" in event_type:
        return await handle_gitlab_mr(payload)

    # Comment/Note Webhook (Review Feedback)
    elif "note" in event_type or "comment" in event_type:
        note = payload.get("object_attributes", {})
        noteable_type = note.get("noteable_type", "").lower()

        if noteable_type == "mergerequest":
            mr = payload.get("merge_request", {})
            source_branch = mr.get("source_branch", "")

            # Extract ticket ID from branch
            ticket_id = None
            cleaned = source_branch.replace("feature/", "")
            for prefix in ("PROJ-", "BUG-", "TASK-", "GL-"):
                if cleaned.startswith(prefix):
                    idx = len(prefix)
                    while idx < len(cleaned) and cleaned[idx] != '-':
                        idx += 1
                    ticket_id = cleaned[:idx]
                    break
            if not ticket_id:
                for part in cleaned.split("-"):
                    if part.startswith(("PROJ", "BUG", "TASK", "GL")):
                        ticket_id = part
                        break

            if ticket_id:
                note_body = note.get("note", "").lower()
                if "changes requested" in note_body or "reject" in note_body or "rework" in note_body:
                    update_ticket_review(ticket_id, "changes_requested", note_body)
                    ticket = get_ticket(ticket_id)
                    retry_count = ticket.get("retry_count", 0) if ticket else 0
                    if retry_count >= 3:
                        update_ticket_status(ticket_id, "failed")
                        log.error(f"Ticket {ticket_id} set to 'failed' after 3 retries", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                        await broadcast_event("ticket_failed", {"ticket_id": ticket_id})
                    else:
                        log.info(f"Review Comment → Ticket {ticket_id} re-queue (retry #{retry_count})", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                        await broadcast_event("ticket_requeued", {
                            "ticket_id": ticket_id, "retry_count": retry_count
                        })
                elif "approved" in note_body or "lgtm" in note_body:
                    update_ticket_review(ticket_id, "approved", note_body)
                    log.info(f"Review Approval → Ticket {ticket_id} approved", extra={"ticket_id": ticket_id, "event": "ticket_reviewed"})
                    await broadcast_event("ticket_reviewed", {
                        "ticket_id": ticket_id, "status": "approved"
                    })

    return {"ok": True, "event": event_type}


@app.post("/webhooks/github")
async def github_webhook(req: Request):
    body = await req.body()
    event_type = req.headers.get("X-GitHub-Event", "").lower()
    signature = req.headers.get("X-Hub-Signature-256", "")
    
    github_webhook_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if github_webhook_secret and signature:
        expected = "sha256=" + hmac.new(github_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    
    metrics.inc("hivemind_webhooks_received_total", labels={"event_type": f"github_{event_type}"})
    
    delivery_id = req.headers.get("X-GitHub-Delivery", "")
    if delivery_id:
        async with _webhook_dedup_lock:
            is_dup = _is_duplicate_webhook(delivery_id)
        if is_dup:
            return {"ok": True, "status": "duplicate"}
    
    if event_type == "issues":
        action = payload.get("action", "")
        if action in ("opened", "reopened"):
            issue = payload.get("issue", {})
            repo = payload.get("repository", {})
            repo_full = repo.get("full_name", "")
            ticket_id = f"GH-{issue.get('number', 'UNKNOWN')}"
            existing = get_ticket(ticket_id)
            if existing:
                return {"ok": True, "id": ticket_id, "status": "already_exists"}
            labels = [l.get("name", l) if isinstance(l, dict) else l for l in issue.get("labels", [])]
            data = {
                "id": ticket_id,
                "title": issue.get("title", ""),
                "description": issue.get("body", ""),
                "issue_type": "Task",
                "priority": "Medium",
                "labels": labels,
            }
            ticket_id = create_ticket(data)
            log.info(f"GitHub Issue -> Ticket {ticket_id} created", extra={"ticket_id": ticket_id, "event": "ticket_created"})
            await broadcast_event("ticket_created", {"ticket_id": ticket_id, "title": data["title"]})
            await broadcast_event("queue_updated", get_queue())
            return {"ok": True, "id": ticket_id, "status": "queued"}
    
    elif event_type == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        pr_url = pr.get("html_url", "")
        source_branch = pr.get("head", {}).get("ref", "")
        
        ticket_id = None
        cleaned = source_branch.replace("feature/", "")
        for prefix in ("PROJ-", "BUG-", "TASK-", "GH-"):
            m = re.match(rf'({prefix}\d+)', cleaned, re.IGNORECASE)
            if m:
                ticket_id = m.group(1).upper()
                break
        
        if not ticket_id:
            return {"ok": False, "error": "Could not extract ticket from branch"}
        
        ticket = get_ticket(ticket_id)
        if not ticket:
            return {"ok": False, "error": f"Ticket {ticket_id} not found"}
        
        if action == "closed" and pr.get("merged"):
            update_ticket_review(ticket_id, "approved", f"PR merged: {pr_url}", pr_url)
            update_ticket_status(ticket_id, "merged")
            log.info(f"GitHub PR merged -> Ticket {ticket_id}", extra={"ticket_id": ticket_id, "event": "ticket_merged"})
            await broadcast_event("ticket_merged", {"ticket_id": ticket_id, "mr_url": pr_url})
            _cleanup_agent_resources(ticket_id)
        elif action == "closed":
            update_ticket_review(ticket_id, "changes_requested", "PR closed without merge", pr_url)
            if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "PR closed"})
            else:
                update_ticket_status(ticket_id, "failed")
                _cleanup_agent_resources(ticket_id)
    
    elif event_type == "pull_request_review":
        review = payload.get("review", {})
        pr = payload.get("pull_request", {})
        source_branch = pr.get("head", {}).get("ref", "")
        ticket_id = None
        cleaned = source_branch.replace("feature/", "")
        for prefix in ("PROJ-", "BUG-", "TASK-", "GH-"):
            m = re.match(rf'({prefix}\d+)', cleaned, re.IGNORECASE)
            if m:
                ticket_id = m.group(1).upper()
                break
        if ticket_id:
            review_state = review.get("state", "")
            if review_state == "changes_requested":
                update_ticket_review(ticket_id, "changes_requested", review.get("body", ""))
                await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "PR review: changes requested"})
            elif review_state == "approved":
                update_ticket_review(ticket_id, "approved", review.get("body", ""))
                await broadcast_event("ticket_reviewed", {"ticket_id": ticket_id, "status": "approved"})
    
    return {"ok": True, "event": f"github_{event_type}"}


# ── REST API: Review Lifecycle ─────────────────────────────────────

@app.post("/api/tickets/{ticket_id}/review")
async def api_review_ticket(ticket_id: str, req: Request):
    data = await req.json()
    review_status = data.get("status")
    notes = data.get("notes", "")
    mr_url = data.get("mr_url", "")

    update_ticket_review(ticket_id, review_status, notes, mr_url)

    ticket = get_ticket(ticket_id)
    if review_status == "changes_requested":
        retry_count = ticket.get("retry_count", 0) if ticket else 0
        if retry_count >= 3:
            update_ticket_status(ticket_id, "failed")
            await broadcast_event("ticket_failed", {"ticket_id": ticket_id})
        else:
            await broadcast_event("ticket_requeued", {
                "ticket_id": ticket_id, "retry_count": retry_count
            })
    elif review_status == "approved":
        update_ticket_status(ticket_id, "merged")
        await broadcast_event("ticket_merged", {"ticket_id": ticket_id})

    await broadcast_event("ticket_reviewed", {
        "ticket_id": ticket_id,
        "status": review_status,
        "retry_count": ticket.get("retry_count", 0) if ticket else 0,
        "notes": notes,
    })

    return {"ok": True, "status": review_status}


@app.post("/api/tickets/{ticket_id}/mr")
async def api_ticket_mr(ticket_id: str, req: Request):
    data = await req.json()
    mr_url = data.get("mr_url", "")
    set_ticket_mr_url(ticket_id, mr_url)
    if mr_url:
        add_ticket_comment(ticket_id, author="system", comment_type="mr_created", content=f"Merge Request created: {mr_url}")
    await broadcast_event("ticket_mr", {"ticket_id": ticket_id, "mr_url": mr_url})
    return {"ok": True}


@app.get("/api/tickets/{ticket_id}/comments")
def api_ticket_comments(ticket_id: str):
    return get_ticket_comments(ticket_id)


@app.post("/api/tickets/{ticket_id}/comments")
def api_add_ticket_comment(ticket_id: str, data: dict):
    author = data.get("author", "user")
    comment_type = data.get("comment_type", "comment")
    content = data.get("content", "")
    if not content:
        return {"ok": False, "error": "Content is required"}
    row_id = add_ticket_comment(ticket_id, author=author, comment_type=comment_type, content=content)
    return {"ok": True, "id": row_id}


@app.get("/api/tickets/status/{status}")
def api_tickets_by_status(status: str):
    return get_tickets_with_queue()


@app.get("/tickets", response_class=HTMLResponse)
def tickets_page():
    with open("static/tickets.html", "r", encoding="utf-8") as f:
        return f.read()


# ── SSE Live Stream ──────────────────────────────────────────────

@app.get("/metrics")
def api_metrics():
    from fastapi.responses import PlainTextResponse
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


@app.get("/api/stream")
def api_stream():
    return StreamingResponse(sse_generator(), media_type="text/event-stream")


# ── Web UI ────────────────────────────────────────────────

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


# ── REST API: Agent Profiles ──────────────────────────────────────────

@app.get("/api/agent-profiles")
def api_get_agent_profiles():
    return get_all_agents_with_profiles()


@app.post("/api/agent-profiles")
def api_create_agent_profile(data: dict):
    agent_id = data.get("id", "").strip()
    name = data.get("name", "").strip()
    model_name = data.get("model_name", "").strip()
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
    return {"ok": True, **agent}


@app.patch("/api/agent-profiles/{agent_id}")
def api_update_agent_profile(agent_id: str, data: dict):
    name = data.get("name")
    model_name = data.get("model_name")
    if data.get("skills") is not None:
        set_agent_skills(agent_id, data["skills"])
    if data.get("instruction_ids") is not None:
        set_agent_instruction_assignments(agent_id, data["instruction_ids"])
    if data.get("repo_affinities") is not None:
        set_agent_repo_affinities(agent_id, data["repo_affinities"])
    agent = update_agent_profile(agent_id, name=name, model_name=model_name)
    if not agent:
        return {"ok": False, "error": f"Agent '{agent_id}' not found"}
    return {"ok": True, **agent}


@app.delete("/api/agent-profiles/{agent_id}")
def api_delete_agent_profile(agent_id: str):
    if agent_id in ("agent-1", "agent-2", "agent-3"):
        return {"ok": False, "error": "Default agents cannot be deleted"}
    agent = get_agent_with_profile(agent_id)
    if not agent:
        return {"ok": False, "error": f"Agent '{agent_id}' not found"}
    if agent.get("status") == "running":
        return {"ok": False, "error": "Running agents cannot be deleted"}
    db_delete_agent(agent_id)
    return {"ok": True}


# ── REST API: OpenCode Plugins ──────────────────────────────────────

@app.get("/api/plugins")
def api_get_plugins():
    return get_opencode_plugins()


@app.post("/api/plugins")
async def api_add_plugin(req: Request):
    data = await req.json()
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="name is required")
    name = add_opencode_plugin(data)
    return {"ok": True, "name": name}


@app.patch("/api/plugins/{name}")
async def api_update_plugin(name: str, req: Request):
    data = await req.json()
    update_opencode_plugin(name, data)
    return {"ok": True}


@app.delete("/api/plugins/{name}")
def api_delete_plugin(name: str):
    delete_opencode_plugin(name)
    return {"ok": True}


# ── REST API: Agent Memory Blocks ───────────────────────────────────

@app.get("/api/agent-memory/{agent_id}")
def api_get_agent_memory(agent_id: str, repo_name: str = None):
    return get_agent_memory_blocks(agent_id, repo_name)


@app.post("/api/agent-memory/{agent_id}")
async def api_set_agent_memory(agent_id: str, req: Request):
    data = await req.json()
    label = data.get("label", "")
    content = data.get("content", "")
    repo_name = data.get("repo_name", "_global")
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    row_id = set_agent_memory_block(
        agent_id, repo_name, label, content,
        description=data.get("description", ""),
        block_limit=data.get("block_limit", 5000),
        read_only=data.get("read_only", False),
    )
    return {"ok": True, "id": row_id}


@app.delete("/api/agent-memory/{agent_id}/{block_id}")
def api_delete_agent_memory(agent_id: str, block_id: int):
    delete_agent_memory_block(block_id)
    return {"ok": True}


@app.post("/api/agent-memory/{agent_id}/seed-defaults")
def api_seed_agent_memory(agent_id: str):
    seed_default_memory_blocks(agent_id)
    return {"ok": True, "agent_id": agent_id}


@app.post("/api/agent-memory/{agent_id}/sync")
async def api_agent_memory_sync(agent_id: str, req: Request):
    data = await req.json()
    blocks = data.get("blocks", [])
    if not blocks:
        return {"ok": True, "synced": 0}
    synced = 0
    for block in blocks:
        label = block.get("label", "")
        content = block.get("content", "")
        if not label or not content:
            continue
        repo_name = block.get("repo_name", "_global")
        description = block.get("description", "")
        block_limit = block.get("block_limit", 5000)
        read_only = block.get("read_only", False)
        set_agent_memory_block(
            agent_id, repo_name, label, content,
            description=description, block_limit=block_limit, read_only=read_only
        )
        synced += 1
    log.info(f"Agent {agent_id}: {synced} memory blocks synced back")
    return {"ok": True, "synced": synced}


@app.post("/api/agent-memory/{agent_id}/sync-filesystem")
async def api_agent_memory_sync_filesystem(agent_id: str, req: Request):
    data = await req.json()
    memory_dir = data.get("memory_dir", "/home/hivemind/.config/opencode/memory")
    repo_name = data.get("repo_name", "_global")
    synced = 0
    import glob as _glob
    for md_file in _glob.glob(f"{memory_dir}/*.md"):
        try:
            content = Path(md_file).read_text(encoding="utf-8")
            lines = content.split("\n")
            label = Path(md_file).stem
            description = ""
            block_limit = 5000
            read_only = False
            if lines[0].strip() == "---":
                end_front = next((i for i, l in enumerate(lines[1:], 1) if l.strip() == "---"), -1)
                if end_front > 0:
                    for fl in lines[1:end_front]:
                        if ":" in fl:
                            fk, fv = fl.split(":", 1)
                            fk = fk.strip().lower()
                            fv = fv.strip()
                            if fk == "label":
                                label = fv
                            elif fk == "description":
                                description = fv
                            elif fk == "limit":
                                try:
                                    block_limit = int(fv)
                                except ValueError:
                                    pass
                            elif fk == "read_only":
                                read_only = fv.lower() in ("true", "yes", "1")
                    content = "\n".join(lines[end_front + 1:])
            set_agent_memory_block(
                agent_id, repo_name, label, content,
                description=description, block_limit=block_limit, read_only=read_only
            )
            synced += 1
        except Exception as e:
            log.warning(f"Memory sync failed for {md_file}: {e}")
    log.info(f"Agent {agent_id}: {synced} memory blocks synced from filesystem")
    return {"ok": True, "synced": synced}


# ── REST API: Repos ─────────────────────────────────────────────────

@app.get("/api/repos")
def api_get_repos():
    return get_all_repos()


@app.get("/api/repo-names")
def api_repo_names():
    return [r["name"] for r in get_all_repos(active_only=True)]


@app.get("/api/repos/{name}/branches")
async def api_repo_branches(name: str):
    repo = get_repo(name)
    if not repo:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")

    branches = set()
    default_branch = repo.get("branch", "development")

    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))

    if gitlab_token and gitlab_host:
        from gitlab_client import gitlab_get
        url = repo.get("url", "")
        project_path = ""
        if "://" in url:
            project_path = url.split("://")[-1].replace(".git", "")
            if "/" in project_path and ":" in project_path.split("/")[0]:
                project_path = "/".join(project_path.split("/")[1:])

        if project_path:
            encoded_path = project_path.replace("/", "%2F")
            try:
                branch_list = await gitlab_get(f"/projects/{encoded_path}/repository/branches", {"per_page": "100"}, gitlab_host, gitlab_token)
                if branch_list:
                    for b in branch_list:
                        branches.add(b.get("name", ""))
            except Exception:
                pass

    repo_dir = None
    try:
        w = _get_worker()
        repo_dir = Path(w.config.pvc_mount_path) / name
    except Exception:
        pass
    if repo_dir and repo_dir.exists() and (repo_dir / ".git").exists():
        try:
            result = subprocess.run(
                ["git", "branch", "-r", "--format=%(refname:strip=3)"],
                capture_output=True, text=True, cwd=str(repo_dir), timeout=15
            )
            if result.returncode == 0:
                for line in result.stdout.strip().splitlines():
                    line = line.strip()
                    if line and "HEAD" not in line:
                        branches.add(line)
        except Exception:
            pass

    if default_branch:
        branches.add(default_branch)
    for fb in ("development", "qa", "main", "master", "staging", "production"):
        branches.add(fb)

    branch_list = sorted(branches, key=lambda b: (b != default_branch, b.lower()))
    return {"branches": branch_list, "default": default_branch}


@app.post("/api/repos")
async def api_add_repo(req: Request):
    data = await req.json()
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    if not name or not url:
        raise HTTPException(status_code=400, detail="name and url are required")

    branch = data.get("branch") or (get_setting("default_branch") or "development")
    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    description = data.get("description", "")

    if get_repo(name):
        raise HTTPException(status_code=409, detail=f"Repository '{name}' already exists")

    ok = db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags, active=1)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to add repository")

    global _worker
    _worker = None

    return {"ok": True, "name": name}


@app.patch("/api/repos")
async def api_bulk_update_repos(req: Request):
    data = await req.json()
    branch = data.get("branch", "").strip()
    active = data.get("active")
    if not branch and active is None:
        raise HTTPException(status_code=400, detail="Provide 'branch' and/or 'active' to update")

    all_repos = get_all_repos()
    updated = []
    for repo in all_repos:
        fields = {}
        if branch:
            fields["branch"] = branch
        if active is not None:
            if isinstance(active, bool):
                fields["active"] = 1 if active else 0
            elif isinstance(active, int):
                fields["active"] = active
        if fields:
            db_update_repo(repo["name"], **fields)
            updated.append(repo["name"])

    global _worker
    _worker = None

    return {"ok": True, "updated": updated, "count": len(updated)}


@app.delete("/api/repos/{name}")
def api_delete_repo(name: str):
    deleted = db_delete_repo(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")

    global _worker
    _worker = None

    return {"ok": True, "deleted": name}


@app.put("/api/repos/{name}")
async def api_update_repo(name: str, req: Request):
    if not get_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    data = await req.json()
    fields = {}
    for key in ("url", "branch", "description", "tags", "active"):
        if key in data:
            if key == "tags" and isinstance(data[key], str):
                fields[key] = [t.strip() for t in data[key].split(",") if t.strip()]
            else:
                fields[key] = data[key]
    ok = db_update_repo(name, **fields)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update repository")

    global _worker
    _worker = None

    return {"ok": True, "name": name}


@app.post("/api/repos/{name}/activate")
async def api_activate_repo(name: str):
    if not get_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    set_repo_active(name, True)

    global _worker
    _worker = None

    return {"ok": True, "name": name, "active": True}


@app.post("/api/repos/{name}/deactivate")
async def api_deactivate_repo(name: str):
    if not get_repo(name):
        raise HTTPException(status_code=404, detail=f"Repository '{name}' not found")
    set_repo_active(name, False)

    global _worker
    _worker = None

    return {"ok": True, "name": name, "active": False}


@app.get("/api/repos/gitlab-projects")
async def api_gitlab_projects():
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        raise HTTPException(status_code=400, detail="GITLAB_HOST and GITLAB_TOKEN required")

    projects = _gitlab_api_get("/projects", gitlab_host, gitlab_token, {
        "membership": "true",
        "min_access_level": "20",
        "order_by": "name",
        "sort": "asc",
    })
    if projects is None:
        raise HTTPException(status_code=502, detail="Failed to fetch projects from GitLab")

    existing_names = {r["name"] for r in get_all_repos()}

    result = []
    for p in projects:
        name = p.get("name", "")
        if not name:
            continue
        result.append({
            "name": name,
            "url": p.get("http_url_to_repo", ""),
            "default_branch": p.get("default_branch", "development"),
            "description": p.get("description", ""),
            "topics": p.get("topics", []),
            "already_imported": name in existing_names,
        })

    return result


@app.post("/api/repos/import-selected")
async def api_import_selected(req: Request):
    data = await req.json()
    selected = data.get("repos", [])
    if not selected:
        raise HTTPException(status_code=400, detail="No repos selected")

    existing_names = {r["name"] for r in get_all_repos()}

    added = []
    skipped = []
    for item in selected:
        name = item.get("name", "")
        url = item.get("url", "")
        if not name or not url:
            continue
        if name in existing_names:
            skipped.append(name)
            continue

        branch = item.get("branch") or item.get("default_branch", "development")
        description = item.get("description", "")
        tags = item.get("tags", item.get("topics", []))

        ok = db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags, active=0)
        if ok:
            existing_names.add(name)
            added.append({"name": name})
        else:
            skipped.append(name)

    global _worker
    _worker = None

    await broadcast_event("repos_updated", {"added": len(added), "skipped": len(skipped)})

    return {"ok": True, "added": added, "skipped": skipped}


@app.post("/api/repos/init-from-gitlab")
async def api_init_repos_from_gitlab(req: Request):
    data = {} if req is None else await req.json()
    use_ai = data.get("use_ai", True)
    min_access_level = data.get("min_access_level", 20)

    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        raise HTTPException(status_code=400, detail="GITLAB_HOST and GITLAB_TOKEN required")

    projects = _gitlab_api_get("/projects", gitlab_host, gitlab_token, {
        "membership": "true",
        "min_access_level": str(min_access_level),
        "order_by": "name",
        "sort": "asc",
    })
    if projects is None:
        raise HTTPException(status_code=502, detail="Failed to fetch projects from GitLab")

    existing = get_all_repos()
    existing_names = {r["name"] for r in existing}

    added = []
    skipped = []
    for p in projects:
        name = p.get("name", "")
        url = p.get("http_url_to_repo", "")
        if not name or name in existing_names:
            skipped.append(name)
            continue

        repo_info = {
            "name": name,
            "url": url,
            "default_branch": p.get("default_branch", "development"),
            "description": p.get("description", ""),
            "topics": p.get("topics", []),
        }

        if use_ai:
            repo_info = _ai_enrich_repo(repo_info)

        branch = repo_info.get("default_branch", "development")
        description = repo_info.get("description", "")
        tags = repo_info.get("tags", repo_info.get("topics", []))

        db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags, active=0)
        added.append({"name": name, "description": description, "tags": tags})

    global _worker
    _worker = None

    await broadcast_event("repos_updated", {"added": len(added), "skipped": len(skipped)})

    return {"ok": True, "added": added, "skipped": skipped, "total_projects": len(projects)}


@app.post("/api/restart")
async def api_restart():
    import signal
    asyncio.get_event_loop().call_later(1, lambda: os.kill(os.getpid(), signal.SIGTERM))
    return {"ok": True, "message": "Restarting in 1 second..."}


# ── Agent Pod Log Streaming ────────────────────────────────────────────────

AGENT_NAMESPACE = os.getenv("AGENT_NAMESPACE", "hivemind")


async def _pod_log_generator(pod_name: str, namespace: str) -> AsyncGenerator[str, None]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "kubectl", "logs", "-f", pod_name, "-n", namespace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        yield f"data: {json.dumps({'event': 'connected', 'pod': pod_name})}\n\n"
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield f"data: {json.dumps({'line': line.decode('utf-8', errors='replace').rstrip()})}\n\n"
        await proc.wait()
        yield f"data: {json.dumps({'event': 'ended', 'pod': pod_name})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'event': 'error', 'error': str(e)})}\n\n"


async def _background_repo_init():
    await asyncio.sleep(3)
    w = _get_worker()
    if w._init_done:
        return
    try:
        await w._aensure_init()
    except Exception as e:
        log.warning(f"Background repo init failed: {e}")


@app.get("/api/tickets/{ticket_id}/logs")
async def api_ticket_logs(ticket_id: str):
    """Live logs of the agent pod for a ticket."""
    _main = _get_main()
    ns = getattr(_main, "AGENT_NAMESPACE", "hivemind")
    pod_name = f"agent-worker-{ticket_id.lower()}"
    rc, out, err = _main._kubectl(f"logs -n {ns} {pod_name} --tail=100")
    if rc != 0:
        if "NotFound" in err or "not found" in err.lower():
            return {"logs": "", "pod": pod_name, "status": "not_found"}
        return {"logs": f"Error: {err}", "pod": pod_name, "status": "error"}
    return {"logs": out, "pod": pod_name, "status": "ok"}


def _handle_sigterm(signum, frame):
    global _shutdown_requested, _running
    _shutdown_requested = True
    _running = False
    log.info("SIGTERM received, initiating graceful shutdown")

import signal
signal.signal(signal.SIGTERM, _handle_sigterm)

async def _orphan_recovery():
    await asyncio.sleep(10)
    try:
        from k8s_client import list_pods
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
                requeue_ticket(tid, AGENT_MAX_RETRIES)
                agent_id = t.get("agent_id", "") or tid.lower()
                set_agent_status(agent_id, "idle")
                await broadcast_event("ticket_requeued", {"ticket_id": tid, "reason": "Orphaned after restart"})
        await broadcast_event("queue_updated", get_queue())
    except Exception as e:
        log.error(f"Orphan recovery failed: {e}", exc_info=True)


@app.on_event("startup")
def startup_event():
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
    global _running
    _running = False
    log.info("Graceful shutdown: stopping background tasks...")
    await broadcast_event("server_shutdown", {"message": "Server shutting down"})
    try:
        from gitlab_client import close_async_client
        await close_async_client()
    except Exception:
        pass
    if _agent_http_client and not _agent_http_client.is_closed:
        await _agent_http_client.aclose()
    log.info("Shutdown complete")


if __name__ == "__main__":
    import uvicorn
    log.info("Starting Orchestrator Server on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)