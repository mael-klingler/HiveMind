#!/usr/bin/env python3
"""
Orchestrator Web UI + REST API
FastAPI + SQLite + SSE fuer Live-Updates
GitLab Webhooks + MR/Review Lifecycle
"""

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import traceback
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

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
    update_repo as db_update_repo, import_repos_from_config,
    stop_ticket,
)

# Lazy-Import von main.py um Zyklus zu vermeiden
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


# ── Workspace Builder ──────────────────────────────────────────────

class WorkspaceBuilder:
    """Baut Workspaces und spawnt K8s-Pods wenn ein Ticket anfaengt."""

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
        self.git = self._main.RepoManager(self.config.pvc_mount_path, self.config.track_branch)
        self.leankg = self._main.LeanKGManager(self.config)
        self.llm = self._main.OllamaClient(self.config.ollama_host, self.config.ollama_model)
        self._statuses = []
        self._init_done = False

    def _ensure_init(self):
        if not self._init_done:
            self._main.configure_git_credentials()
            self._statuses = self.git.update_all(self.config.repositories)
            if self.config.leankg_enabled:
                self.leankg.index_all(self._statuses)
            self._init_done = True

    async def build_and_spawn(self, ticket):
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._build_and_spawn_sync, ticket)
        except Exception as e:
            print(f"Workspace-Builder Fehler: {e}")
            traceback.print_exc()
            return f"failed: {e}", None, None

    def _build_and_spawn_sync(self, ticket):
        self._ensure_init()

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
                        print(f"  ⚠️  LLM-Analyse fehlgeschlagen (Versuch {attempt+1}/{max_llm_retries}): {e}")
                        print(f"  ⏳ Retry in {delay}s...")
                        import time
                        time.sleep(delay)
                    else:
                        print(f"  ❌ LLM-Analyse nach {max_llm_retries} Versuchen fehlgeschlagen für Ticket {ticket.id}: {e}")
        else:
            print(f"  ❌ Ollama nicht erreichbar ({self.llm.host}) – Ticket {ticket.id} kann nicht analysiert werden")

        if not analysis:
            print(f"  ❌ Keine KI-Analyse verfügbar – Ticket {ticket.id} wird als 'failed' markiert")
            return "failed: no llm analysis", None, None

        selected_names = set(analysis.get("selected_repos", []))
        selected_configs = [r for r in self.config.repositories if r.name in selected_names]
        if not selected_configs:
            print(f"  ❌ KI-Analyse hat keine passenden Repositories ausgewählt – Ticket {ticket.id}")
            return "failed: no matching repositories", None, None

        prompt = self._main.generate_assignment_prompt(ticket, analysis, selected_configs)

        # Retry-Kontext aus Ticket-Daten in Analyse mergen (falls vorhanden)
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
    """Verifiziert GitLab Webhook HMAC-Signature."""
    if not GITLAB_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    expected = hmac.new(
        GITLAB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


async def handle_gitlab_issue(payload: Dict):
    """Verarbeitet GitLab Issue Webhook: erstellt Ticket + Queue-Eintrag."""
    issue = payload.get("object_attributes", {})
    if not issue:
        return {"ok": False, "error": "No issue data"}

    action = issue.get("action", "")
    if action not in ("open", "update", "reopen"):
        return {"ok": True, "ignored": f"action={action}"}

    ticket_id = f"GL-{issue.get('iid', issue.get('id', 'UNKNOWN'))}"

    # Pruefe ob Ticket schon existiert
    existing = get_ticket(ticket_id)
    if existing:
        return {"ok": True, "id": ticket_id, "status": "already_exists"}

    # Labels aus GitLab extrahieren
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
    print(f"📥 GitLab Issue → Ticket {ticket_id} erstellt")

    await broadcast_event("ticket_created", {"ticket_id": ticket_id, "title": data["title"]})
    await broadcast_event("queue_updated", get_queue())

    return {"ok": True, "id": ticket_id, "status": "queued"}


async def handle_gitlab_mr(payload: Dict):
    """Verarbeitet GitLab Merge Request Webhook fuer Review Lifecycle."""
    mr = payload.get("object_attributes", {})
    if not mr:
        return {"ok": False, "error": "No MR data"}

    action = mr.get("action", "")
    state = mr.get("state", "")
    mr_url = mr.get("url", "")
    source_branch = mr.get("source_branch", "")

    # Finde Ticket aus Branch-Namen (feature/PROJ-123 oder feature/TICKET-123)
    ticket_id = None
    cleaned = source_branch.replace("feature/", "")
    for prefix in ("PROJ-", "BUG-", "TASK-", "GL-"):
        if cleaned.startswith(prefix):
            ticket_id = cleaned.split("/")[0]
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

    # MR geschlossen/merged
    if action in ("merge", "close") or state == "merged":
        update_ticket_review(ticket_id, "approved", f"MR {action}: {mr_url}", mr_url)
        update_ticket_status(ticket_id, "merged")
        print(f"✅ MR merged → Ticket {ticket_id} auf 'merged' gesetzt")
        await broadcast_event("ticket_merged", {"ticket_id": ticket_id, "mr_url": mr_url})

        # Cleanup Agent-Pod
        agent_pod = os.environ.get(f"AGENT_POD_{ticket_id}")
        if agent_pod:
            _kubectl = _get_main()._kubectl
            _kubectl(f"delete pod {agent_pod} -n {os.getenv('AGENT_NAMESPACE', 'hivemind')} --grace-period=0 --force")
            print(f"🗑️  Agent-Pod {agent_pod} geloescht")

    # MR updated (neue Commits, Review Changes)
    elif action == "update" and state == "opened":
        # Pruefe ob Review-Changes-Requested vorliegt
        # In der Praxis wuerde das ueber separate Note/Comment Webhooks laufen
        pass

    # MR reopened (nach Reject → retry)
    elif action == "reopen":
        # Ticket erneut in Queue setzen
        update_ticket_review(ticket_id, "changes_requested", f"MR reopened: {mr_url}", mr_url)
        update_ticket_status(ticket_id, "queued")
        print(f"🔄 MR reopened → Ticket {ticket_id} zur Re-Queue hinzugefuegt")
        await broadcast_event("ticket_queued", {"ticket_id": ticket_id, "mr_url": mr_url})

    return {"ok": True, "ticket_id": ticket_id, "action": action}


async def review_lifecycle_monitor():
    """Background-Task: Ueberwacht MR-Status, Pipeline-Fehler und neue Kommentare via GitLab API."""
    print("🔍 Review Lifecycle Monitor gestartet")
    await asyncio.sleep(15)  # Warte bis Server ready

    while True:
        try:
            gitlab_token = os.getenv("GITLAB_TOKEN", "")
            gitlab_host = os.getenv("GITLAB_HOST", "gitlab.example.com")
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

                    # MR gemerged → Ticket abschliessen
                    if mr_state == "merged":
                        update_ticket_status(ticket_id, "completed")
                        update_ticket_review(ticket_id, "approved", "", mr_url)
                        add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Ticket abgeschlossen: MR wurde gemerged.")
                        print(f"✅ Ticket {ticket_id}: MR merged")
                        await broadcast_event("ticket_merged", {"ticket_id": ticket_id})
                        continue

                    # MR geschlossen → als failed markieren oder re-queue
                    if mr_state == "closed":
                        update_ticket_review(ticket_id, "changes_requested", "MR closed without merge", mr_url)
                        if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                            print(f"🔄 Ticket {ticket_id}: MR closed → re-queued")
                            await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "MR closed"})
                        else:
                            update_ticket_status(ticket_id, "failed")
                            print(f"❌ Ticket {ticket_id}: MR closed → failed (max retries)")
                        continue

                    # MR ist offen → Pipeline + Konflikte + Kommentare pruefen
                    if mr_state == "opened":
                        project_path, mr_iid = _parse_mr_url(mr_url)
                        if not project_path:
                            continue

                        update_ticket_mr_tracking(
                            ticket_id,
                            project_path=project_path,
                            mr_iid=int(mr_iid) if mr_iid else None,
                        )

                        # Merge-Konflikt pruefen (merge_status vom GitLab)
                        merge_status = mr_data.get("merge_status", "")
                        has_conflicts = mr_data.get("has_conflicts", False)
                        if merge_status == "cannot_be_merged" or has_conflicts:
                            last_conflict = t.get("mr_conflict_status", "none")
                            if last_conflict != "conflict_detected":
                                print(f"⚠️  Ticket {ticket_id}: Merge-Konflikt erkannt → re-queue zur Konfiktaufloesung")
                                update_ticket_mr_tracking(ticket_id, conflict_status="conflict_detected")
                                update_ticket_review(ticket_id, "changes_requested",
                                    f"Merge-Konflikt: Branch {mr_data.get('source_branch', '?')} hat Konflikte mit {mr_data.get('target_branch', 'development')}",
                                    mr_url)
                                add_ticket_comment(ticket_id, author="system", comment_type="system",
                                    content=f"⚠️ Merge-Konflikt erkannt. Agent wird den Branch automatisch rebasen.")
                                if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                                    await broadcast_event("ticket_requeued", {
                                        "ticket_id": ticket_id,
                                        "reason": "Merge conflict detected",
                                        "conflict": True,
                                    })
                                    await broadcast_event("queue_updated", get_queue())
                                else:
                                    update_ticket_status(ticket_id, "failed")
                                    print(f"❌ Ticket {ticket_id}: Merge-Konflikt → failed (max retries)")
                                continue
                        elif merge_status == "can_be_merged":
                            update_ticket_mr_tracking(ticket_id, conflict_status="none")

                        # Pipeline-Status pruefen
                        pipeline = mr_data.get("head_pipeline") or mr_data.get("pipeline")
                        pipeline_status = pipeline.get("status", "unknown") if pipeline else "unknown"
                        last_pipeline = t.get("mr_pipeline_status", "unknown")

                        if pipeline_status == "failed" and last_pipeline != "failed":
                            print(f"🔴 Ticket {ticket_id}: Pipeline fehlgeschlagen → re-queue")
                            update_ticket_mr_tracking(ticket_id, pipeline_status="failed")
                            update_ticket_review(ticket_id, "changes_requested", "Pipeline failed", mr_url)
                            if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                                await broadcast_event("ticket_requeued", {
                                    "ticket_id": ticket_id,
                                    "reason": "Pipeline failed",
                                })
                        elif pipeline_status != "failed":
                            update_ticket_mr_tracking(ticket_id, pipeline_status=pipeline_status)

                        # Neue Kommentare pruefen
                        await _check_mr_comments(
                            ticket_id, t, mr_url, project_path, mr_iid,
                            gitlab_host, gitlab_token
                        )

                except Exception as e:
                    print(f"  MR check Fehler fuer {ticket_id}: {e}")
                    traceback.print_exc()

            await asyncio.sleep(60)
        except Exception as e:
            print(f"Review Monitor Error: {e}")
            traceback.print_exc()
            await asyncio.sleep(60)


def _fetch_mr(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    """Holt MR-Daten von GitLab API."""
    project_path, mr_iid = _parse_mr_url(mr_url)
    if not project_path or not mr_iid:
        return None

    encoded_path = project_path.replace("/", "%2F")
    url = f"https://{gitlab_host}/api/v4/projects/{encoded_path}/merge_requests/{mr_iid}"
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": gitlab_token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _gitlab_api_get(path: str, gitlab_host: str = None, gitlab_token: str = None, params: dict = None) -> Optional[List[Dict]]:
    """GitLab API GET request with pagination."""
    gitlab_host = gitlab_host or os.getenv("GITLAB_HOST", os.getenv("GIT_HOST", ""))
    gitlab_token = gitlab_token or os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        return None
    base = f"https://{gitlab_host}/api/v4{path}"
    query = []
    if params:
        for k, v in params.items():
            query.append(f"{k}={urllib.parse.quote(str(v), safe='')}")
    page = 1
    all_items = []
    while True:
        q = "&".join(query + [f"page={page}", "per_page=100"])
        url = f"{base}?{q}" if q else base
        req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": gitlab_token})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                items = json.loads(resp.read())
                if not items:
                    break
                all_items.extend(items)
                total_pages = int(resp.headers.get("X-Total-Pages", "1"))
                if page >= total_pages:
                    break
                page += 1
        except urllib.error.HTTPError:
            break
        except Exception:
            break
    return all_items


def _ai_enrich_repo(repo_info: Dict) -> Dict:
    """Use Ollama to generate description and tags for a repo."""
    ollama_host = os.getenv("OLLAMA_HOST", "").rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", os.getenv("OPENCODE_MODEL", ""))
    if not ollama_host or not ollama_model:
        return repo_info

    name = repo_info.get("name", "")
    url = repo_info.get("url", "")
    existing_desc = repo_info.get("description", "")
    topics = repo_info.get("topics", [])
    default_branch = repo_info.get("default_branch", "main")

    context_parts = [f"Repository: {name}", f"URL: {url}", f"Branch: {default_branch}"]
    if existing_desc:
        context_parts.append(f"GitLab description: {existing_desc}")
    if topics:
        context_parts.append(f"GitLab topics: {', '.join(topics)}")

    prompt = "\n".join(context_parts) + """

Analyze this repository and provide:
1. A short German description (1-2 sentences) explaining what this service/project does
2. 3-6 relevant tags categorizing this repo (e.g. backend, frontend, api, service, infra, database, auth, etc.)

Reply ONLY as JSON: {"description": "...", "tags": ["tag1", "tag2", ...]}"""

    try:
        body = json.dumps({
            "model": ollama_model,
            "messages": [
                {"role": "system", "content": "Du bist ein Software-Architekt. Beschreibe Repositories praezise auf Deutsch. Antworte NUR als JSON."},
                {"role": "user", "content": prompt}
            ],
            "stream": False,
            "format": "json"
        }).encode("utf-8")

        api_key = os.getenv("OLLAMA_CLOUD_API_KEY", "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        base_url = os.getenv("OLLAMA_BASE_URL", ollama_host + "/v1")
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=body,
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content and "message" in result:
                content = result["message"].get("content", "")
            if content:
                parsed = json.loads(content)
                if parsed.get("description"):
                    repo_info["description"] = parsed["description"]
                if parsed.get("tags"):
                    repo_info["tags"] = parsed["tags"]
    except Exception:
        pass

    return repo_info


def _parse_mr_url(mr_url: str) -> tuple:
    """Extrahiert (project_path, mr_iid) aus einer GitLab MR URL."""
    if not mr_url:
        return None, None
    try:
        parts = mr_url.split("/-/merge_requests/")
        if len(parts) < 2:
            return None, None
        project_path = parts[0].replace("https://", "").replace("http://", "")
        project_path = project_path.split("/", 1)[1] if "/" in project_path else project_path
        mr_iid = parts[1].split("/")[0]
        return project_path, mr_iid
    except (ValueError, IndexError):
        return None, None


async def _check_mr_comments(ticket_id, ticket, mr_url, project_path, mr_iid, gitlab_host, gitlab_token):
    """Prueft neue Kommentare auf einem MR und re-queued bei Review-Feedback."""
    last_note_id = ticket.get("mr_last_note_id", 0) or 0
    encoded_path = project_path.replace("/", "%2F")
    notes_url = f"https://{gitlab_host}/api/v4/projects/{encoded_path}/merge_requests/{mr_iid}/notes?sort=asc&per_page=50"

    req = urllib.request.Request(notes_url, headers={"PRIVATE-TOKEN": gitlab_token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            notes = json.loads(resp.read())
    except Exception:
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
        print(f"💬 Ticket {ticket_id}: Neuer Kommentar von {author}: {body[:80]}...")

        lower_body = body.lower()
        is_changes_requested = any(kw in lower_body for kw in
            ["changes requested", "rework", "fix", "fehler", "please fix", "bitte korrigier",
             "nicht ok", "failing", "typecheck", "pipeline failed", "broken"])

        if is_changes_requested:
            update_ticket_review(ticket_id, "changes_requested", body[:500], mr_url)
            if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                print(f"🔄 Ticket {ticket_id}: Changes requested → re-queued")
                await broadcast_event("ticket_requeued", {
                    "ticket_id": ticket_id,
                    "reason": f"Review feedback from {author}",
                    "comment": body[:200],
                })
                await broadcast_event("queue_updated", get_queue())
            else:
                update_ticket_status(ticket_id, "failed")
                print(f"❌ Ticket {ticket_id}: Changes requested → failed (max retries)")
            return


AGENT_RETRY_DELAY = int(os.getenv("AGENT_RETRY_DELAY", "120"))
AGENT_MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", "3"))


async def agent_pod_monitor():
    """Background-Task: Prueft Agent-Pod-Status, re-queued fehlgeschlagene Tickets, markiert abgeschlossene und aktualisiert MR-URLs."""
    print(f"🔄 Agent Pod Monitor gestartet (Retry-Delay: {AGENT_RETRY_DELAY}s, Max-Retries: {AGENT_MAX_RETRIES})")
    while True:
        try:
            # Abgeschlossene Pods erkennen (Succeeded/Completed)
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

            # Running Tickets pruefen: Pod abgeschlossen → Ticket completed
            for t in [t for t in get_tickets(status=None) if t.get("status") == "running"]:
                ticket_id = t["id"]
                pod_name = f"agent-worker-{ticket_id.lower()}"
                namespace = os.getenv("AGENT_NAMESPACE", "hivemind")

                rc, out, err = _get_main()._kubectl(f"get pod {pod_name} -n {namespace} -o jsonpath='{{.status.phase}}' 2>/dev/null")
                if rc != 0:
                    if ticket_id.lower() in completed_pod_ids or ticket_id in completed_pod_ids:
                        update_ticket_status(ticket_id, "completed")
                        set_agent_status(t.get("agent_id", "") or ticket_id.lower(), "idle")
                        add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Agent-Pod abgeschlossen. Ticket wurde als completed markiert.")
                        print(f"✅ Ticket {ticket_id}: Pod abgeschlossen → 'completed'")
                        await broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                        await broadcast_event("queue_updated", get_queue())
                    else:
                        # Pod not found and not completed — was force-deleted or crashed
                        # Re-queue the ticket so it gets reassigned
                        agent_id = t.get("agent_id", "") or ticket_id.lower()
                        retry_count = t.get("retry_count", 0)
                        if retry_count < AGENT_MAX_RETRIES:
                            print(f"🔄 Ticket {ticket_id}: Pod nicht gefunden → re-queued")
                            requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                            set_agent_status(agent_id, "idle")
                            await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "Pod not found"})
                            await broadcast_event("queue_updated", get_queue())
                        else:
                            print(f"❌ Ticket {ticket_id}: Pod nicht gefunden, max retries erreicht → failed")
                            update_ticket_status(ticket_id, "failed")
                            set_agent_status(agent_id, "idle")
                            add_ticket_comment(ticket_id, author="system", comment_type="system", content="Agent-Pod nicht gefunden und max Retries erreicht.")
                            await broadcast_event("ticket_failed", {"ticket_id": ticket_id})
                    continue

                phase = out.strip().strip("'\"")

                if phase in ("Succeeded", "Completed"):
                    update_ticket_status(ticket_id, "completed")
                    set_agent_status(t.get("agent_id", "") or ticket_id.lower(), "idle")
                    add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Agent-Pod Status: {phase}. Ticket wurde als completed markiert.")
                    print(f"✅ Ticket {ticket_id}: Pod {phase} → 'completed'")
                    await broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                    await broadcast_event("queue_updated", get_queue())
                    # Cleanup: Pod loeschen nach Abschluss
                    try:
                        _get_main()._kubectl(f"delete pod {pod_name} -n {namespace} --grace-period=0 --force 2>/dev/null")
                        print(f"🗑️  Pod {pod_name} geloescht (completed)")
                    except Exception:
                        pass
                    continue

                # Pod nicht gefunden und nicht in completed_pod_ids → pruefe ob Pod existiert
                if phase in ("Failed", "Error"):
                    retry_count = t.get("retry_count", 0)
                    updated_at = t.get("updated_at")
                    if updated_at:
                        from datetime import timezone
                        try:
                            failed_at = datetime.fromisoformat(updated_at)
                            if failed_at.tzinfo is None:
                                failed_at = failed_at.replace(tzinfo=timezone.utc)
                            elapsed = (datetime.now(timezone.utc) - failed_at).total_seconds()
                            if elapsed < AGENT_RETRY_DELAY:
                                remaining = int(AGENT_RETRY_DELAY - elapsed)
                                print(f"⏳ Ticket {ticket_id}: Retry in {remaining}s (delay={AGENT_RETRY_DELAY}s)")
                                continue
                        except (ValueError, TypeError):
                            pass

                    if retry_count >= AGENT_MAX_RETRIES:
                        print(f"❌ Ticket {ticket_id}: Max Retries ({AGENT_MAX_RETRIES}) erreicht – bleibt 'failed'")
                        continue

                    success = requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                    if success:
                        new_retry = retry_count + 1
                        print(f"🔄 Ticket {ticket_id}: Re-queued (Retry #{new_retry}/{AGENT_MAX_RETRIES})")
                        await broadcast_event("ticket_requeued", {
                            "ticket_id": ticket_id,
                            "retry_count": new_retry,
                            "max_retries": AGENT_MAX_RETRIES,
                            "reason": f"Pod {phase}",
                        })
                        await broadcast_event("queue_updated", get_queue())
                    else:
                        print(f"❌ Ticket {ticket_id}: Re-queue fehlgeschlagen (max retries?)")
                    # Cleanup failed pod
                    try:
                        _get_main()._kubectl(f"delete pod {pod_name} -n {namespace} --grace-period=0 --force 2>/dev/null")
                        print(f"🗑️  Pod {pod_name} geloescht (failed)")
                    except Exception:
                        pass

            # MR-URL Discovery: Tickets ohne MR-URL aber mit Branch auf GitLab suchen
            gitlab_token = os.getenv("GITLAB_TOKEN", "")
            gitlab_host = os.getenv("GITLAB_HOST", "gitlab.example.com")
            if gitlab_token:
                all_tickets = get_tickets(status=None)
                for t in all_tickets:
                    if t.get("mr_url") or t.get("status") not in ("completed", "running"):
                        continue
                    ticket_id = t["id"]
                    branch_name = f"feature/{ticket_id.lower()}"

                    for repo in _get_worker().config.repositories:
                        encoded_path = repo.url.split("://")[-1].replace(".git", "").replace(":", "/") if "://" in repo.url else ""
                        if not encoded_path:
                            continue
                        encoded_path = encoded_path.replace("/", "%2F")
                        try:
                            req = urllib.request.Request(
                                f"https://{gitlab_host}/api/v4/projects/{encoded_path}/merge_requests?state=opened&source_branch={branch_name}",
                                headers={"PRIVATE-TOKEN": gitlab_token},
                            )
                            with urllib.request.urlopen(req, timeout=10) as resp:
                                mrs = json.loads(resp.read())
                                if mrs:
                                    mr_url = mrs[0].get("web_url", "")
                                    if mr_url and not t.get("mr_url"):
                                        set_ticket_mr_url(ticket_id, mr_url)
                                        add_ticket_comment(ticket_id, author="system", comment_type="mr_created", content=f"Merge Request erstellt: {mr_url}")
                                        update_ticket_mr_tracking(ticket_id, pipeline_status=mrs[0].get("head_pipeline", {}).get("status", "unknown") if mrs[0].get("head_pipeline") else "unknown")
                                        # mr_status auf 'open' setzen
                                        conn = get_db()
                                        conn.execute("UPDATE tickets SET mr_status = 'open' WHERE id = ?", (ticket_id,))
                                        conn.commit()
                                        conn.close()
                                        print(f"🔗 Ticket {ticket_id}: MR gefunden → {mr_url}")
                                    break
                        except Exception:
                            pass

            # Stale Tickets: Running-Tickets ohne aktiven Pod, aelter als 60 Min → completed
            from datetime import timezone as _tz
            stale_threshold = 3600  # 60 Minuten (war 30min, erhoeht da lange laufende Agenten normal sind)
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
                    pod_still_active = True  # Bei API-Fehler nicht als stale markieren

                if pod_still_active:
                    continue

                updated_at = t.get("updated_at")
                if updated_at:
                    try:
                        updated_dt = datetime.fromisoformat(updated_at)
                        if updated_dt.tzinfo is None:
                            updated_dt = updated_dt.replace(tzinfo=_tz.utc)
                        elapsed = (datetime.now(_tz.utc) - updated_dt).total_seconds()
                        if elapsed > stale_threshold:
                            update_ticket_status(ticket_id, "completed")
                            agent_id = t.get("agent_id", "") or f"agent-{ticket_id.lower()}"
                            set_agent_status(agent_id, "idle")
                            add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Pod verschwunden (>30min). Ticket automatisch als completed markiert.")
                            print(f"✅ Ticket {ticket_id}: Pod verschwunden, >30min alt → 'completed', Agent {agent_id} → 'idle'")
                            await broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                            await broadcast_event("queue_updated", get_queue())
                            # Cleanup: Stale Pod loeschen falls noch vorhanden
                            try:
                                _get_main()._kubectl(f"delete pod {pod_name} -n {ns} --grace-period=0 --force 2>/dev/null")
                            except Exception:
                                pass
                    except (ValueError, TypeError):
                        pass

            await asyncio.sleep(30)
        except Exception as e:
            print(f"Agent Pod Monitor Error: {e}")
            traceback.print_exc()
            await asyncio.sleep(30)


# ── Globaler Queue-Prozessor ───────────────────────────────────────

_running = False
_worker = None


def _get_worker():
    global _worker
    if _worker is None:
        _worker = WorkspaceBuilder()
    return _worker


async def queue_processor():
    """Hintergrund-Task: Weist freie Agenten Tickets zu und spawnt K8s-Pods."""
    global _running
    _running = True
    print("🔄 Queue-Processor gestartet")

    while _running:
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

            # Smart Agent Selection: Waehle Agent mit hoechster Affinitaet zum primaren Repo
            primary_repo = ""
            if ticket_data:
                # Phase 1: KI-Analyse falls noch nicht vorhanden
                if not ticket_data.get("ai_planning"):
                    try:
                        w = _get_worker()
                        w._ensure_init()
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
                            )
                            analysis = await loop.run_in_executor(None, w.llm.analyze_repos_for_ticket, _ticket, w._statuses, w.leankg)
                            if analysis:
                                set_ticket_ai_planning(ticket_data["id"], analysis)
                                ticket_data = get_ticket(next_item["ticket_id"])
                                print(f"🧠 KI-Analyse vorab: primary_repo={analysis.get('primary_repo','?')}")
                    except Exception as e:
                        print(f"⚠️ Vorab-KI-Analyse fehlgeschlagen: {e}")

                # primary_repo aus ai_planning extrahieren
                if ticket_data.get("ai_planning"):
                    try:
                        planning = ticket_data["ai_planning"]
                        if isinstance(planning, str):
                            planning = json.loads(planning)
                        primary_repo = planning.get("primary_repo", "")
                    except (json.JSONDecodeError, TypeError):
                        pass

                # Fallback: Repos aus Beschreibung matchen
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
                step_name="Ticket zugewiesen",
                status="running",
                detail=f"Agent {agent['name']} bearbeitet Ticket"
            )
            affinity_msg = f" (Repo-Affinitaet: {primary_repo})" if best_agent_id else ""
            print(f"🎫 Ticket {next_item['ticket_id']} → Agent {agent['name']}{affinity_msg}")

            # K8s Agent-Pod spawnen
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
                )
                print(f"🚀 Spawne Agent-Pod fuer Ticket {ticket.id}...")
                w = _get_worker()

                # Retry-Kontext aus Ticket-Daten an Worker weitergeben
                w._retry_context = {
                    "review_notes": ticket_data.get("review_notes", ""),
                    "mr_url": ticket_data.get("mr_url", ""),
                    "pipeline_status": ticket_data.get("mr_pipeline_status", ""),
                    "retry_count": ticket_data.get("retry_count", 0),
                    "conflict_status": ticket_data.get("mr_conflict_status", ""),
                }

                status, ws_path, pod_name = await w.build_and_spawn(ticket)
                print(f"   Status: {status}, Pod: {pod_name}")
                if ws_path:
                    set_ticket_workspace(next_item["ticket_id"], str(ws_path), agent["id"])
                    add_step(
                        queue_id=next_item["id"],
                        ticket_id=next_item["ticket_id"],
                        agent_id=agent["id"],
                        step_name="Agent-Pod gestartet",
                        status="running",
                        detail=f"Pod: {pod_name}, Workspace: {ws_path}"
                    )
                if status.startswith("failed"):
                    fail_queue_item(next_item["id"], status)
                    set_agent_status(agent["id"], "idle")
                    update_ticket_status(next_item["ticket_id"], "failed")
                    await broadcast_event("queue_updated", get_queue())

            await broadcast_event("queue_updated", get_queue())
            await broadcast_event("agent_updated", {
                "agent_id": agent["id"],
                "status": "running",
                "ticket_id": next_item["ticket_id"],
            })

            await asyncio.sleep(2)
        except Exception as e:
            print(f"Queue Processor Error: {e}")
            traceback.print_exc()
            await asyncio.sleep(5)


# ── SSE Broadcast ─────────────────────────────────────────────────

clients: List[asyncio.Queue] = []

async def broadcast_event(event: str, data: Dict):
    """Sendet Event an alle verbundenen SSE-Clients."""
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
    return f"http://{pod_name}.agent-session.{namespace}.svc.cluster.local:4096"


async def _proxy_request(ticket_id: str, path: str, request: Request) -> Response:
    base_url = _resolve_pod_url(ticket_id)
    if not base_url:
        raise HTTPException(status_code=404, detail=f"Kein aktiver Agent fuer Ticket {ticket_id}")

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
        raise HTTPException(status_code=502, detail=f"Agent-Pod nicht erreichbar: {e}")

    response_headers = {}
    for key, value in resp.headers.items():
        if key.lower() not in ("transfer-encoding", "content-encoding", "connection"):
            response_headers[key] = value

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
    )


@app.api_route("/agent-session/{ticket_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def agent_session_proxy(ticket_id: str, path: str, request: Request):
    return await _proxy_request(ticket_id, path, request)


@app.get("/agent-session/{ticket_id}")
async def agent_session_root(ticket_id: str, request: Request):
    return await _proxy_request(ticket_id, "", request)


@app.websocket("/agent-session/{ticket_id}/ws")
async def agent_session_ws(websocket: WebSocket, ticket_id: str):
    base_url = _resolve_pod_url(ticket_id)
    if not base_url:
        await websocket.close(code=4040, reason=f"Kein aktiver Agent fuer Ticket {ticket_id}")
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
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    old_agent_id = ticket.get("agent_id", "")
    success = reopen_ticket(ticket_id)
    if not success:
        raise HTTPException(status_code=400, detail="Ticket konnte nicht wiedereroeffnet werden")
    if old_agent_id:
        set_agent_status(old_agent_id, "idle")
        print(f"🔓 Agent {old_agent_id} → idle (Ticket {ticket_id} reopened)")
    add_ticket_comment(ticket_id, author="user", comment_type="system", content="Ticket manuell wiedereroeffnet.")
    await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "Manually reopened"})
    await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": "queued"}


@app.post("/api/tickets/{ticket_id}/stop")
async def api_stop_ticket(ticket_id: str):
    ticket = get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket nicht gefunden")
    if ticket.get("status") in ("completed", "stopped"):
        raise HTTPException(status_code=400, detail=f"Ticket ist bereits {ticket.get('status')}")
    
    agent_id = ticket.get("agent_id", "")
    stopped = stop_ticket(ticket_id)
    if not stopped:
        raise HTTPException(status_code=400, detail="Ticket konnte nicht gestoppt werden")
    
    if agent_id:
        try:
            main_mod = _get_main()
            namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
            main_mod._kubectl(["delete", "pod", f"agent-{agent_id}", "-n", namespace, "--force", "--grace-period=0"])
        except Exception:
            pass

    add_ticket_comment(ticket_id, author="user", comment_type="system", content="Ticket manuell gestoppt.")
    await broadcast_event("ticket_stopped", {"ticket_id": ticket_id})
    await broadcast_event("queue_updated", get_queue())
    return {"ok": True, "id": ticket_id, "status": "stopped"}


@app.post("/api/tickets")
async def api_create_ticket(req: Request, background_tasks: BackgroundTasks):
    data = await req.json()
    ticket_id = create_ticket(data)

    await broadcast_event("ticket_created", {"ticket_id": ticket_id, "title": data.get("title", "")})
    await broadcast_event("queue_updated", get_queue())

    return {"id": ticket_id, "status": "queued"}


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
    return {"max_agents": get_max_agents()}


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
        add_ticket_comment(ticket_id, author=agent_id, comment_type="summary", content=f"Agent {agent_id} hat die Aufgabe abgeschlossen.")

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
    """Empfaengt GitLab Webhooks (Issues, MRs, Notes)."""
    body = await req.body()
    event_type = req.headers.get("X-Gitlab-Event", "").lower()
    signature = req.headers.get("X-Gitlab-Token", "")

    if not verify_gitlab_webhook(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

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

            # Extrahiere Ticket-ID aus Branch
            ticket_id = None
            cleaned = source_branch.replace("feature/", "")
            for prefix in ("PROJ-", "BUG-", "TASK-", "GL-"):
                if cleaned.startswith(prefix):
                    ticket_id = cleaned.split("/")[0]
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
                        print(f"❌ Ticket {ticket_id} nach 3 Retries auf 'failed'")
                        await broadcast_event("ticket_failed", {"ticket_id": ticket_id})
                    else:
                        print(f"📝 Review Comment → Ticket {ticket_id} re-queue (Retry #{retry_count})")
                        await broadcast_event("ticket_requeued", {
                            "ticket_id": ticket_id, "retry_count": retry_count
                        })
                elif "approved" in note_body or "lgtm" in note_body:
                    update_ticket_review(ticket_id, "approved", note_body)
                    print(f"✅ Review Approval → Ticket {ticket_id} approved")
                    await broadcast_event("ticket_reviewed", {
                        "ticket_id": ticket_id, "status": "approved"
                    })

    return {"ok": True, "event": event_type}


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
        add_ticket_comment(ticket_id, author="system", comment_type="mr_created", content=f"Merge Request erstellt: {mr_url}")
    await broadcast_event("ticket_mr", {"ticket_id": ticket_id, "mr_url": mr_url})
    return {"ok": True}


@app.get("/api/tickets/{ticket_id}/logs")
async def api_ticket_logs(ticket_id: str, tail: int = 100):
    """Live-Logs des Agent-Pods fuer ein Ticket."""
    _main = _get_main()
    ns = getattr(_main, "AGENT_NAMESPACE", "hivemind")
    pod_name = f"agent-worker-{ticket_id.lower()}"
    rc, out, err = _main._kubectl(f"logs -n {ns} {pod_name} --tail={tail}")
    if rc != 0:
        if "NotFound" in err or "not found" in err.lower():
            return {"logs": "", "pod": pod_name, "status": "not_found"}
        return {"logs": f"Fehler: {err}", "pod": pod_name, "status": "error"}
    return {"logs": out, "pod": pod_name, "status": "ok"}


@app.get("/api/tickets/{ticket_id}/comments")
def api_ticket_comments(ticket_id: str):
    return get_ticket_comments(ticket_id)


@app.post("/api/tickets/{ticket_id}/comments")
def api_add_ticket_comment(ticket_id: str, data: dict):
    author = data.get("author", "user")
    comment_type = data.get("comment_type", "comment")
    content = data.get("content", "")
    if not content:
        return {"ok": False, "error": "Inhalt ist Pflicht"}
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

@app.get("/api/stream")
def api_stream():
    return StreamingResponse(sse_generator(), media_type="text/event-stream")


# ── Web UI ────────────────────────────────────────────────────────

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
        return {"ok": False, "error": "Agent-ID ist Pflicht"}
    existing = get_agent_with_profile(agent_id)
    if existing:
        return {"ok": False, "error": f"Agent '{agent_id}' existiert bereits"}
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
        return {"ok": False, "error": f"Agent '{agent_id}' nicht gefunden"}
    return {"ok": True, **agent}


@app.delete("/api/agent-profiles/{agent_id}")
def api_delete_agent_profile(agent_id: str):
    if agent_id in ("agent-1", "agent-2", "agent-3"):
        return {"ok": False, "error": "Default-Agenten koennen nicht geloescht werden"}
    agent = get_agent_with_profile(agent_id)
    if not agent:
        return {"ok": False, "error": f"Agent '{agent_id}' nicht gefunden"}
    if agent.get("status") == "running":
        return {"ok": False, "error": "Laufende Agenten koennen nicht geloescht werden"}
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


# ── REST API: Repos ─────────────────────────────────────────────────

@app.get("/api/repos")
def api_get_repos():
    return get_all_repos()


@app.get("/api/repo-names")
def api_repo_names():
    return [r["name"] for r in get_all_repos()]


@app.post("/api/repos")
async def api_add_repo(req: Request):
    data = await req.json()
    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    if not name or not url:
        raise HTTPException(status_code=400, detail="name and url are required")

    branch = data.get("branch") or (get_setting("default_branch") or "main")
    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    description = data.get("description", "")

    if get_repo(name):
        raise HTTPException(status_code=409, detail=f"Repository '{name}' already exists")

    ok = db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to add repository")

    global _worker
    _worker = None

    return {"ok": True, "name": name}


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
    for key in ("url", "branch", "description", "tags"):
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
            "default_branch": p.get("default_branch", "main"),
            "description": p.get("description", ""),
            "topics": p.get("topics", []),
        }

        if use_ai:
            repo_info = _ai_enrich_repo(repo_info)

        branch = repo_info.get("default_branch", "main")
        description = repo_info.get("description", "")
        tags = repo_info.get("tags", repo_info.get("topics", []))

        db_add_repo(name=name, url=url, branch=branch, description=description, tags=tags)
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


@app.get("/api/tickets/{ticket_id}/logs")
async def api_ticket_logs(ticket_id: str):
    pod_name = f"agent-worker-{ticket_id.lower()}"
    namespace = AGENT_NAMESPACE

    rc, _, _ = _get_main()._kubectl(f"get pod {pod_name} -n {namespace} -o name")
    if rc != 0:
        raise HTTPException(status_code=404, detail=f"Pod {pod_name} not found")

    return StreamingResponse(
        _pod_log_generator(pod_name, namespace),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.on_event("startup")
def startup_event():
    import_repos_from_config(os.getenv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json"))
    ensure_agent_pool()
    asyncio.create_task(queue_processor())
    asyncio.create_task(review_lifecycle_monitor())
    asyncio.create_task(agent_pod_monitor())
    print(f"🔑 GitLab Webhook Secret: {'aktiviert' if GITLAB_WEBHOOK_SECRET else 'deaktiviert (unsicher)'}")


if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting Orchestrator Server on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
