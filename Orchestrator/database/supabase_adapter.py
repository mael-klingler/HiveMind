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
Supabase database adapter – uses the Supabase Python client and PostgREST API.
Replaces both database.py (SQLite) and database_pg.py (PostgreSQL raw SQL).
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from supabase import create_client, Client

log = logging.getLogger("hivemind")

_VALID_TRANSITIONS = {
    "idle": {"running", "stopped"},
    "running": {"idle", "error", "stopped"},
    "error": {"idle", "running", "stopped"},
    "stopped": {"idle"},
}

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

_sb: Optional[Client] = None


def _get_client() -> Client:
    global _sb
    if _sb is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set for Supabase backend")
        _sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _sb


def _now() -> str:
    return datetime.now().isoformat()


def _ensure_json(val):
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return val


def _parse_json(val):
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def init_db():
    pass


def ensure_config_defaults():
    defaults = {
        "max_agents": os.getenv("MAX_AGENTS", "3"),
        "polling_interval_seconds": os.getenv("POLLING_INTERVAL_SECONDS", "5"),
        "git_host": os.getenv("GITLAB_HOST") or "",
        "git_user": os.getenv("GIT_USER", "gitlab-ci-token"),
        "git_token": os.getenv("GITLAB_TOKEN", ""),
        "ollama_host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "glm-5.1:cloud"),
        "opencode_model": os.getenv("OPENCODE_MODEL", "glm-5.1:cloud"),
        "auto_pull_enabled": "true",
        "default_branch": "development",
        "branch_fallback_order": "development,qa,main",
    }
    sb = _get_client()
    for key, value in defaults.items():
        existing = sb.table("config").select("key").eq("key", key).execute()
        if not existing.data:
            sb.table("config").insert({"key": key, "value": value}).execute()


def import_settings_from_env():
    env_mapping = {
        "GIT_HOST": "git_host",
        "GIT_USER": "git_user",
        "GIT_TOKEN": "git_token",
        "OLLAMA_HOST": "ollama_host",
        "OLLAMA_MODEL": "ollama_model",
        "TRACK_BRANCH": "default_branch",
        "BRANCH_FALLBACK_ORDER": "branch_fallback_order",
    }
    for env_key, db_key in env_mapping.items():
        val = os.getenv(env_key)
        if val:
            set_setting(db_key, val)


# ── Tickets ──────────────────────────────────────────

def create_ticket(ticket: Dict[str, Any]) -> str:
    sb = _get_client()
    ticket_id = ticket.get("id") or f"TASK-{int(datetime.now().timestamp() * 1000)}"
    data = {
        "id": ticket_id,
        "title": ticket["title"],
        "description": ticket.get("description", ""),
        "labels": ticket.get("labels", []),
        "issue_type": ticket.get("issue_type", "Task"),
        "priority": ticket.get("priority", "Medium"),
        "status": "queued",
        "selected_repos": ticket.get("selected_repos", []),
    }
    sb.table("tickets").insert(data).execute()

    pos_result = sb.table("queue").select("position").order("position", desc=True).limit(1).execute()
    pos = (pos_result.data[0]["position"] + 1) if pos_result.data else 1
    sb.table("queue").insert({
        "ticket_id": ticket_id,
        "position": pos,
        "status": "waiting",
    }).execute()
    return ticket_id


def get_tickets(status: Optional[str] = None) -> List[Dict]:
    sb = _get_client()
    query = sb.table("tickets").select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    result = query.execute()
    return [_parse_ticket_row(r) for r in result.data]


def get_ticket(ticket_id: str) -> Optional[Dict]:
    sb = _get_client()
    result = sb.table("tickets").select("*").eq("id", ticket_id).single().execute()
    return _parse_ticket_row(result.data) if result.data else None


def update_ticket_status(ticket_id: str, status: str):
    sb = _get_client()
    sb.table("tickets").update({"status": status}).eq("id", ticket_id).execute()
    if status not in ("running", "queued"):
        ticket = get_ticket(ticket_id)
        if ticket and ticket.get("agent_id"):
            sb.table("agents").update({
                "status": "idle",
                "current_ticket_id": None,
                "progress": 0,
            }).eq("id", ticket["agent_id"]).execute()


def stop_ticket(ticket_id: str) -> bool:
    sb = _get_client()
    queue_items = sb.table("queue").select("id").eq("ticket_id", ticket_id).in_("status", ["waiting", "running", "queued"]).execute()
    if queue_items.data:
        for q in queue_items.data:
            sb.table("queue").update({"status": "completed", "completed_at": _now()}).eq("id", q["id"]).execute()
    result = sb.table("tickets").update({"status": "stopped"}).eq("id", ticket_id).neq("status", "completed").neq("status", "stopped").execute()
    sb.table("agents").update({"status": "idle", "current_ticket_id": None, "progress": 0}).eq("current_ticket_id", ticket_id).execute()
    return len(result.data) > 0


def get_tickets_with_queue() -> List[Dict]:
    sb = _get_client()
    tickets = sb.table("tickets").select("*").order("updated_at", desc=True).execute()
    queue = sb.table("queue").select("*").neq("status", "completed").execute()
    queue_map = {q["ticket_id"]: q for q in queue.data}
    result = []
    for t in tickets.data:
        row = _parse_ticket_row(t)
        if t["id"] in queue_map:
            q = queue_map[t["id"]]
            row["queue_status"] = q["status"]
            row["assigned_agent_id"] = q.get("assigned_agent_id")
        result.append(row)
    return result


def update_ticket_review(ticket_id: str, review_status: str, notes: str = "", mr_url: str = ""):
    sb = _get_client()
    if review_status == "approved":
        sb.table("tickets").update({
            "status": "merged", "mr_status": "merged",
            "review_status": "approved", "review_notes": notes, "mr_url": mr_url,
        }).eq("id", ticket_id).execute()
    elif review_status == "changes_requested":
        sb.table("tickets").update({
            "status": "queued", "review_status": "changes_requested",
            "review_notes": notes, "mr_url": mr_url,
        }).eq("id", ticket_id).execute()
        pos_result = sb.table("queue").select("position").order("position", desc=True).limit(1).execute()
        pos = (pos_result.data[0]["position"] + 1) if pos_result.data else 1
        sb.table("queue").insert({"ticket_id": ticket_id, "position": pos, "status": "waiting"}).execute()
    else:
        sb.table("tickets").update({
            "review_status": review_status, "review_notes": notes, "mr_url": mr_url,
        }).eq("id", ticket_id).execute()


def set_ticket_mr_url(ticket_id: str, mr_url: str):
    sb = _get_client()
    sb.table("tickets").update({"mr_url": mr_url, "mr_status": "open"}).eq("id", ticket_id).execute()


def set_ticket_workspace(ticket_id: str, workspace_path: str, agent_id: str):
    sb = _get_client()
    sb.table("tickets").update({"workspace_path": workspace_path, "agent_id": agent_id}).eq("id", ticket_id).execute()


def set_ticket_ai_planning(ticket_id: str, planning: Dict):
    sb = _get_client()
    sb.table("tickets").update({"ai_planning": planning}).eq("id", ticket_id).execute()


def update_ticket_description(ticket_id: str, description: str):
    sb = _get_client()
    sb.table("tickets").update({"description": description}).eq("id", ticket_id).execute()


def requeue_ticket(ticket_id: str, max_retries: int = 3) -> bool:
    sb = _get_client()
    ticket = get_ticket(ticket_id)
    if not ticket:
        return False
    if ticket.get("retry_count", 0) >= max_retries:
        return False
    sb.table("tickets").update({"status": "queued"}).eq("id", ticket_id).execute()
    pos_result = sb.table("queue").select("position").order("position", desc=True).limit(1).execute()
    pos = (pos_result.data[0]["position"] + 1) if pos_result.data else 1
    sb.table("queue").insert({"ticket_id": ticket_id, "position": pos, "status": "waiting"}).execute()
    return True


def reopen_ticket(ticket_id: str) -> bool:
    sb = _get_client()
    ticket = get_ticket(ticket_id)
    if not ticket:
        return False
    sb.table("tickets").update({
        "status": "queued", "mr_status": "none",
        "review_status": "pending", "mr_conflict_status": "none",
    }).eq("id", ticket_id).execute()
    pos_result = sb.table("queue").select("position").order("position", desc=True).limit(1).execute()
    pos = (pos_result.data[0]["position"] + 1) if pos_result.data else 1
    sb.table("queue").insert({"ticket_id": ticket_id, "position": pos, "status": "waiting"}).execute()
    return True


def get_failed_tickets() -> List[Dict]:
    return get_tickets(status="failed")


def get_open_mr_tickets() -> List[Dict]:
    sb = _get_client()
    result = sb.table("tickets").select("*").eq("mr_status", "open").execute()
    result2 = sb.table("tickets").select("*").not_.is_("mr_url", "null").execute()
    seen = {r["id"] for r in result.data}
    combined = list(result.data) + [r for r in result2.data if r["id"] not in seen]
    return [_parse_ticket_row(r) for r in combined]


def update_ticket_mr_tracking(ticket_id: str, pipeline_status: str = None, last_note_id: int = None, project_path: str = None, mr_iid: int = None, conflict_status: str = None):
    sb = _get_client()
    updates = {}
    if pipeline_status is not None:
        updates["mr_pipeline_status"] = pipeline_status
    if last_note_id is not None:
        updates["mr_last_note_id"] = last_note_id
    if project_path is not None:
        updates["mr_project_path"] = project_path
    if mr_iid is not None:
        updates["mr_iid"] = mr_iid
    if conflict_status is not None:
        updates["mr_conflict_status"] = conflict_status
    if updates:
        sb.table("tickets").update(updates).eq("id", ticket_id).execute()


# ── Agents ────────────────────────────────────────────

def get_agent(agent_id: str) -> Optional[Dict]:
    sb = _get_client()
    result = sb.table("agents").select("*").eq("id", agent_id).single().execute()
    return _parse_agent_row(result.data) if result.data else None


def get_or_create_agent(agent_id: str, name: str = "") -> Dict:
    existing = get_agent(agent_id)
    if existing:
        return existing
    sb = _get_client()
    sb.table("agents").insert({
        "id": agent_id,
        "name": name or f"Agent-{agent_id[:8]}",
        "status": "idle",
        "logs": [],
    }).execute()
    return get_agent(agent_id)


def set_agent_status(agent_id: str, status: str, ticket_id: Optional[str] = None, progress: int = 0):
    sb = _get_client()
    current = sb.table("agents").select("status").eq("id", agent_id).execute()
    if current.data and status not in _VALID_TRANSITIONS.get(current.data[0].get("status", ""), set()):
        log.warning(f"Invalid agent state transition: {current.data[0].get('status')} → {status} for agent {agent_id}")
    if status == "running":
        sb.table("agents").update({
            "status": status, "current_ticket_id": ticket_id, "started_at": _now(), "progress": progress,
        }).eq("id", agent_id).execute()
    elif status == "idle":
        sb.table("agents").update({
            "status": status, "current_ticket_id": None, "completed_at": _now(), "progress": progress,
        }).eq("id", agent_id).execute()
    else:
        sb.table("agents").update({"status": status, "progress": progress}).eq("id", agent_id).execute()


def get_all_agents() -> List[Dict]:
    sb = _get_client()
    result = sb.table("agents").select("*").order("id").execute()
    return [_parse_agent_row(r) for r in result.data]


def get_idle_agents() -> List[Dict]:
    sb = _get_client()
    result = sb.table("agents").select("*").eq("status", "idle").order("id").execute()
    return [_parse_agent_row(r) for r in result.data]


def get_max_agents() -> int:
    return int(get_setting("max_agents") or "3")


def set_max_agents(max_agents: int):
    set_setting("max_agents", str(max_agents))


_DEFAULT_AGENT_ROLES = ["developer", "reviewer", "tester"]


def ensure_agent_pool():
    max_agents = get_max_agents()
    sb = _get_client()
    for i in range(max_agents):
        agent_id = f"agent-{i+1}"
        role = _DEFAULT_AGENT_ROLES[i] if i < len(_DEFAULT_AGENT_ROLES) else "general"
        existing = sb.table("agents").select("id, role").eq("id", agent_id).execute()
        if not existing.data:
            sb.table("agents").insert({
                "id": agent_id, "name": f"Agent {i+1}", "role": role, "status": "idle", "logs": [],
            }).execute()
        elif not existing.data[0].get("role") or existing.data[0].get("role") == "general":
            sb.table("agents").update({"role": role}).eq("id", agent_id).execute()
    sb.table("agents").update({"status": "idle"}).eq("status", "disabled").execute()


def create_agent(agent_id: str, name: str, model_name: str = "", skill_names: Optional[List[str]] = None, instruction_ids: Optional[List[int]] = None, repo_affinities: Optional[List[str]] = None) -> Dict:
    sb = _get_client()
    sb.table("agents").insert({
        "id": agent_id, "name": name or f"Agent-{agent_id}",
        "status": "idle", "logs": [], "model_name": model_name,
    }).execute()
    if skill_names:
        for sn in skill_names:
            sb.table("agent_skills").insert({"agent_id": agent_id, "mcp_server_name": sn}).execute()
    if instruction_ids:
        for iid in instruction_ids:
            sb.table("agent_instruction_assignments").insert({"agent_id": agent_id, "instruction_id": iid}).execute()
    if repo_affinities:
        for rn in repo_affinities:
            sb.table("agent_repo_affinities").insert({"agent_id": agent_id, "repo_name": rn, "affinity": 1}).execute()
    return get_agent_with_profile(agent_id)


def update_agent_profile(agent_id: str, name: Optional[str] = None, model_name: Optional[str] = None) -> Dict:
    sb = _get_client()
    updates = {}
    if name is not None:
        updates["name"] = name
    if model_name is not None:
        updates["model_name"] = model_name
    if updates:
        sb.table("agents").update(updates).eq("id", agent_id).execute()
    return get_agent_with_profile(agent_id)


def set_agent_role(agent_id: str, role: str):
    sb = _get_client()
    sb.table("agents").update({"role": role}).eq("id", agent_id).execute()


def delete_agent(agent_id: str):
    sb = _get_client()
    sb.table("agent_skills").delete().eq("agent_id", agent_id).execute()
    sb.table("agent_instruction_assignments").delete().eq("agent_id", agent_id).execute()
    sb.table("agent_repo_affinities").delete().eq("agent_id", agent_id).execute()
    sb.table("agents").delete().eq("id", agent_id).execute()


def set_agent_skills(agent_id: str, skill_names: List[str]):
    sb = _get_client()
    sb.table("agent_skills").delete().eq("agent_id", agent_id).execute()
    for sn in skill_names:
        sb.table("agent_skills").insert({"agent_id": agent_id, "mcp_server_name": sn}).execute()


def set_agent_instruction_assignments(agent_id: str, instruction_ids: List[int]):
    sb = _get_client()
    sb.table("agent_instruction_assignments").delete().eq("agent_id", agent_id).execute()
    for iid in instruction_ids:
        sb.table("agent_instruction_assignments").insert({"agent_id": agent_id, "instruction_id": iid}).execute()


def get_agent_with_profile(agent_id: str) -> Optional[Dict]:
    agent = get_agent(agent_id)
    if not agent:
        return None
    sb = _get_client()
    skills = sb.table("agent_skills").select("mcp_server_name").eq("agent_id", agent_id).execute()
    agent["skills"] = [r["mcp_server_name"] for r in skills.data]
    instr = sb.table("agent_instruction_assignments").select("instruction_id").eq("agent_id", agent_id).execute()
    agent["instruction_ids"] = [r["instruction_id"] for r in instr.data]
    aff = sb.table("agent_repo_affinities").select("repo_name").eq("agent_id", agent_id).order("repo_name").execute()
    agent["repo_affinities"] = [r["repo_name"] for r in aff.data]
    return agent


def get_all_agents_with_profiles() -> List[Dict]:
    sb = _get_client()
    result = sb.table("agents").select("id").order("id").execute()
    return [get_agent_with_profile(r["id"]) for r in result.data]


def get_agent_mcp_servers(agent_id: str) -> List[Dict]:
    sb = _get_client()
    result = sb.table("agent_skills").select("mcp_server_name").eq("agent_id", agent_id).execute()
    if not result.data:
        return []
    names = [r["mcp_server_name"] for r in result.data]
    servers = sb.table("mcp_servers").select("*").in_("name", names).eq("enabled", 1).execute()
    return servers.data


def get_agent_assigned_instructions(agent_id: str) -> List[Dict]:
    sb = _get_client()
    result = sb.table("agent_instruction_assignments").select("instruction_id").eq("agent_id", agent_id).execute()
    if not result.data:
        return []
    ids = [r["instruction_id"] for r in result.data]
    instrs = sb.table("agent_instructions").select("*").in_("id", ids).eq("enabled", 1).order("sort_order").execute()
    return instrs.data


# ── Queue ──────────────────────────────────────────────

def get_next_queue_item() -> Optional[Dict]:
    sb = _get_client()
    result = sb.table("queue").select("*").eq("status", "waiting").order("position").order("id").limit(1).execute()
    return result.data[0] if result.data else None


def assign_next_queue_item(agent_id: str) -> Optional[Dict]:
    """Atomically claim the next waiting queue item for an agent.
    Uses Supabase's .eq("status", "waiting") filter + update to claim.
    Returns the claimed item dict or None if nothing available.
    """
    sb = _get_client()
    next_item = get_next_queue_item()
    if not next_item:
        return None
    result = sb.table("queue").update({
        "status": "running", "assigned_agent_id": agent_id, "started_at": _now(),
    }).eq("id", next_item["id"]).eq("status", "waiting").execute()
    if not result.data:
        return None
    return result.data[0]


def assign_queue_item(queue_id: int, agent_id: str) -> bool:
    sb = _get_client()
    result = sb.table("queue").update({
        "status": "running", "assigned_agent_id": agent_id, "started_at": _now(),
    }).eq("id", queue_id).eq("status", "waiting").execute()
    return len(result.data) > 0


def complete_queue_item(queue_id: int):
    sb = _get_client()
    sb.table("queue").update({"status": "completed", "completed_at": _now()}).eq("id", queue_id).execute()


def fail_queue_item(queue_id: int, error: str):
    sb = _get_client()
    sb.table("queue").update({"status": "failed", "completed_at": _now()}).eq("id", queue_id).execute()
    add_step(queue_id, None, None, "Error", "error", error)


def get_queue() -> List[Dict]:
    sb = _get_client()
    result = sb.table("queue").select("*, tickets(title), agents!queue_assigned_agent_id_fkey(name)").order("status").order("position").execute()
    queue_list = []
    for row in result.data:
        item = dict(row)
        item["agent_name"] = item.get("agents", {}).get("name") if item.get("agents") else None
        item["title"] = item.get("tickets", {}).get("title") if item.get("tickets") else None
        item.pop("agents", None)
        item.pop("tickets", None)
        queue_list.append(item)
    return queue_list


# ── Steps ──────────────────────────────────────────────

def add_step(queue_id: int, ticket_id: str, agent_id: str, step_name: str, status: str = "running", detail: str = ""):
    sb = _get_client()
    sb.table("steps").insert({
        "queue_id": queue_id, "ticket_id": ticket_id, "agent_id": agent_id,
        "step_name": step_name, "status": status, "detail": detail,
    }).execute()


def get_steps(ticket_id: Optional[str] = None, agent_id: Optional[str] = None, queue_id: Optional[int] = None) -> List[Dict]:
    sb = _get_client()
    query = sb.table("steps").select("*").order("timestamp", desc=True)
    if ticket_id:
        query = query.eq("ticket_id", ticket_id)
    if agent_id:
        query = query.eq("agent_id", agent_id)
    if queue_id:
        query = query.eq("queue_id", queue_id)
    result = query.execute()
    return result.data


def get_all_steps() -> List[Dict]:
    sb = _get_client()
    result = sb.table("steps").select("*, queue(ticket_id), tickets(title)").order("timestamp", desc=True).limit(200).execute()
    steps = []
    for row in result.data:
        s = dict(row)
        q = s.pop("queue", None)
        t = s.pop("tickets", None)
        if q:
            s["ticket_id"] = s.get("ticket_id") or q.get("ticket_id")
        if t:
            s["title"] = t.get("title")
        steps.append(s)
    return steps


# ── Settings ────────────────────────────────────────────

def get_all_settings() -> List[Dict]:
    sb = _get_client()
    result = sb.table("config").select("key, value").order("key").execute()
    return result.data


def get_setting(key: str) -> Optional[str]:
    sb = _get_client()
    result = sb.table("config").select("value").eq("key", key).execute()
    return result.data[0]["value"] if result.data else None


def set_setting(key: str, value: str):
    sb = _get_client()
    existing = sb.table("config").select("key").eq("key", key).execute()
    if existing.data:
        sb.table("config").update({"value": value}).eq("key", key).execute()
    else:
        sb.table("config").insert({"key": key, "value": value}).execute()


# ── Repos ────────────────────────────────────────────

def get_all_repos(active_only: bool = False) -> List[Dict]:
    sb = _get_client()
    query = sb.table("repos").select("name, url, branch, description, tags, active").order("name")
    if active_only:
        query = query.eq("active", 1)
    result = query.execute()
    repos = []
    for r in result.data:
        r["tags"] = _parse_json(r.get("tags")) or []
        r["active"] = bool(r.get("active", 0))
        repos.append(r)
    return repos


def get_repo(name: str) -> Optional[Dict]:
    sb = _get_client()
    result = sb.table("repos").select("name, url, branch, description, tags, active").eq("name", name).single().execute()
    if not result.data:
        return None
    r = result.data
    r["tags"] = _parse_json(r.get("tags")) or []
    r["active"] = bool(r.get("active", 0))
    return r


def add_repo(name: str, url: str, branch: str = "", description: str = "", tags: Optional[list] = None, active: int = 1) -> bool:
    if not branch:
        branch = get_setting("default_branch") or "development"
    sb = _get_client()
    try:
        sb.table("repos").insert({
            "name": name, "url": url, "branch": branch,
            "description": description, "tags": tags or [], "active": active,
        }).execute()
        return True
    except Exception:
        return False


def delete_repo(name: str) -> bool:
    sb = _get_client()
    result = sb.table("repos").delete().eq("name", name).execute()
    return len(result.data) > 0


def update_repo(name: str, **fields) -> bool:
    allowed = {"url", "branch", "description", "tags", "active"}
    updates = {}
    for k, v in fields.items():
        if k in allowed:
            if k == "tags":
                v = v if v else []
            updates[k] = v
    if not updates:
        return False
    sb = _get_client()
    result = sb.table("repos").update(updates).eq("name", name).execute()
    return len(result.data) > 0


def import_repos_from_config(config_path: str):
    existing = get_all_repos()
    existing_names = {r["name"] for r in existing}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    for repo in data.get("repositories", []):
        name = repo.get("name", "")
        if not name or name in existing_names:
            continue
        add_repo(
            name=name,
            url=repo.get("url", ""),
            branch=repo.get("branch", "development"),
            description=repo.get("description", ""),
            tags=repo.get("tags", []),
            active=1,
        )


def set_repo_active(name: str, active: bool) -> bool:
    sb = _get_client()
    result = sb.table("repos").update({"active": 1 if active else 0}).eq("name", name).execute()
    return len(result.data) > 0


def get_agent_repo_affinities(agent_id: str) -> List[str]:
    sb = _get_client()
    result = sb.table("agent_repo_affinities").select("repo_name").eq("agent_id", agent_id).order("repo_name").execute()
    return [r["repo_name"] for r in result.data]


def set_agent_repo_affinities(agent_id: str, repo_names: List[str]):
    sb = _get_client()
    sb.table("agent_repo_affinities").delete().eq("agent_id", agent_id).execute()
    for rn in repo_names:
        sb.table("agent_repo_affinities").insert({"agent_id": agent_id, "repo_name": rn, "affinity": 1}).execute()


def get_all_repo_names() -> List[str]:
    repos = get_all_repos()
    return [r["name"] for r in repos]


def find_best_agent_for_repo(primary_repo: str, idle_agents: List[Dict]) -> Optional[str]:
    if not primary_repo or not idle_agents:
        return None
    sb = _get_client()
    agent_ids = [a["id"] for a in idle_agents]
    result = sb.table("agent_repo_affinities").select("agent_id, affinity").in_("agent_id", agent_ids).eq("repo_name", primary_repo).order("affinity", desc=True).limit(1).execute()
    return result.data[0]["agent_id"] if result.data else None


def score_agent_for_repo(agent_id: str, primary_repo: str, skill_names: List[str] = None) -> float:
    score = 0.0
    sb = _get_client()
    result = sb.table("agent_repo_affinities").select("affinity").eq("agent_id", agent_id).eq("repo_name", primary_repo).execute()
    if result.data:
        score += result.data[0].get("affinity", 1) * 10.0
    if skill_names:
        skill_result = sb.table("agent_skills").select("id").eq("agent_id", agent_id).in_("mcp_server_name", skill_names).execute()
        score += len(skill_result.data) * 2.0
    return score


# ── MCP Servers ────────────────────────────────────────

def get_mcp_servers() -> List[Dict]:
    sb = _get_client()
    result = sb.table("mcp_servers").select("*").order("name").execute()
    return [_parse_json_fields(r, ["args", "env"]) for r in result.data]


def get_enabled_mcp_servers() -> List[Dict]:
    sb = _get_client()
    result = sb.table("mcp_servers").select("*").eq("enabled", 1).order("name").execute()
    return [_parse_json_fields(r, ["args", "env"]) for r in result.data]


def add_mcp_server(data: Dict) -> str:
    sb = _get_client()
    sb.table("mcp_servers").insert({
        "name": data["name"],
        "enabled": int(data.get("enabled", True)),
        "server_type": data.get("server_type", "local"),
        "command": data.get("command", ""),
        "args": data.get("args", []),
        "env": data.get("env", {}),
        "description": data.get("description", ""),
    }).execute()
    return data["name"]


def update_mcp_server(name: str, data: Dict):
    sb = _get_client()
    updates = {}
    for key in ("enabled", "server_type", "command", "args", "env", "description"):
        if key in data:
            val = data[key]
            if key == "enabled":
                val = int(val)
            updates[key] = val
    if not updates:
        return
    sb.table("mcp_servers").update(updates).eq("name", name).execute()


def delete_mcp_server(name: str):
    sb = _get_client()
    sb.table("agent_skills").delete().eq("mcp_server_name", name).execute()
    sb.table("mcp_servers").delete().eq("name", name).execute()


# ── Agent Instructions ─────────────────────────────────

def get_agent_instructions() -> List[Dict]:
    sb = _get_client()
    result = sb.table("agent_instructions").select("*").order("sort_order").order("id").execute()
    return result.data


def get_enabled_agent_instructions() -> str:
    result = get_agent_instructions()
    enabled = [r for r in result if r.get("enabled")]
    return "\n\n".join(r["content"] for r in enabled)


def add_agent_instruction(data: Dict) -> int:
    sb = _get_client()
    result = sb.table("agent_instructions").insert({
        "name": data["name"], "content": data["content"],
        "enabled": int(data.get("enabled", True)), "sort_order": data.get("sort_order", 0),
    }).execute()
    return result.data[0]["id"]


def update_agent_instruction(id: int, data: Dict):
    sb = _get_client()
    updates = {}
    for key in ("name", "content", "enabled", "sort_order"):
        if key in data:
            val = data[key]
            if key == "enabled":
                val = int(val)
            updates[key] = val
    if not updates:
        return
    sb.table("agent_instructions").update(updates).eq("id", id).execute()


def delete_agent_instruction(id: int):
    sb = _get_client()
    sb.table("agent_instruction_assignments").delete().eq("instruction_id", id).execute()
    sb.table("agent_instructions").delete().eq("id", id).execute()


# ── OpenCode Plugins ───────────────────────────────────

def get_opencode_plugins() -> List[Dict]:
    sb = _get_client()
    result = sb.table("opencode_plugins").select("*").order("name").execute()
    return result.data


def get_enabled_opencode_plugins() -> List[Dict]:
    sb = _get_client()
    result = sb.table("opencode_plugins").select("*").eq("enabled", 1).order("name").execute()
    return result.data


def get_enabled_plugin_names() -> List[str]:
    sb = _get_client()
    result = sb.table("opencode_plugins").select("name").eq("enabled", 1).order("name").execute()
    return [r["name"] for r in result.data]


def add_opencode_plugin(data: Dict) -> str:
    sb = _get_client()
    sb.table("opencode_plugins").insert({
        "name": data["name"],
        "enabled": int(data.get("enabled", True)),
        "description": data.get("description", ""),
        "requires_binary": data.get("requires_binary", ""),
    }).execute()
    return data["name"]


def update_opencode_plugin(name: str, data: Dict):
    sb = _get_client()
    updates = {}
    for key in ("enabled", "description", "requires_binary"):
        if key in data:
            val = data[key]
            if key == "enabled":
                val = int(val)
            updates[key] = val
    if not updates:
        return
    sb.table("opencode_plugins").update(updates).eq("name", name).execute()


def delete_opencode_plugin(name: str):
    sb = _get_client()
    sb.table("opencode_plugins").delete().eq("name", name).execute()


# ── Memory ──────────────────────────────────────────────

def get_agent_memory_blocks(agent_id: str, repo_name: Optional[str] = None) -> List[Dict]:
    sb = _get_client()
    query = sb.table("agent_memory_blocks").select("*").eq("agent_id", agent_id)
    if repo_name:
        query = query.eq("repo_name", repo_name)
    result = query.order("repo_name").order("label").execute()
    return result.data


def set_agent_memory_block(agent_id: str, repo_name: str, label: str, content: str,
                            description: str = "", block_limit: int = 5000, read_only: bool = False) -> int:
    sb = _get_client()
    result = sb.table("agent_memory_blocks").upsert({
        "agent_id": agent_id,
        "repo_name": repo_name or "_global",
        "label": label,
        "content": content,
        "description": description,
        "block_limit": block_limit,
        "read_only": int(read_only),
    }, on_conflict="agent_id,repo_name,label").execute()
    return result.data[0]["id"]


def get_agent_memory_as_markdown(agent_id: str, repo_name: str) -> str:
    blocks = get_agent_memory_blocks(agent_id, repo_name)
    if not blocks:
        blocks = get_agent_memory_blocks(agent_id, "_global")
    parts = []
    for b in blocks:
        parts.append(f"""---
label: {b['label']}
description: {b['description']}
limit: {b['block_limit']}
read_only: {bool(b.get('read_only', 0))}
---
{b['content']}""")
    return "\n\n".join(parts)


def delete_agent_memory_block(block_id: int):
    sb = _get_client()
    sb.table("agent_memory_blocks").delete().eq("id", block_id).execute()


def seed_default_memory_blocks(agent_id: str):
    defaults = [
        ("_global", "persona", "You are an autonomous software developer. Work carefully and methodically. Prefer English comments in code.", "Agent identity and behavior"),
        ("_global", "human", "Prefer English UI language. Use Conventional Commits. No emojis in commits.", "Operator preferences"),
        ("_global", "project", "Tech stack: Vue 3 + TypeScript frontend, Go backend. Tests are mandatory.", "Project conventions and architecture"),
    ]
    for repo_name, label, content, desc in defaults:
        set_agent_memory_block(agent_id, repo_name, label, content, desc)


# ── Comments ───────────────────────────────────────────

def add_ticket_comment(ticket_id: str, author: str = "system", comment_type: str = "comment", content: str = "") -> int:
    sb = _get_client()
    result = sb.table("ticket_comments").insert({
        "ticket_id": ticket_id, "author": author,
        "comment_type": comment_type, "content": content,
    }).execute()
    return result.data[0]["id"]


def get_ticket_comments(ticket_id: str) -> List[Dict]:
    sb = _get_client()
    result = sb.table("ticket_comments").select("*").eq("ticket_id", ticket_id).order("created_at").execute()
    return result.data


# ── Groups ──────────────────────────────────────────────

def create_ticket_group(group_id: str, parent_ticket_id: str, title: str = "", description: str = "") -> str:
    sb = _get_client()
    sb.table("ticket_groups").upsert({
        "id": group_id, "parent_ticket_id": parent_ticket_id,
        "title": title, "description": description,
    }).execute()
    return group_id


def get_ticket_group(group_id: str) -> Optional[Dict]:
    sb = _get_client()
    result = sb.table("ticket_groups").select("*").eq("id", group_id).single().execute()
    return result.data if result.data else None


def add_team_message(group_id: str, sender_agent_id: str, content: str, message_type: str = "info") -> int:
    sb = _get_client()
    result = sb.table("team_channel_messages").insert({
        "group_id": group_id, "sender_agent_id": sender_agent_id,
        "message_type": message_type, "content": content,
    }).execute()
    return result.data[0]["id"]


def get_team_messages(group_id: str, limit: int = 50) -> List[Dict]:
    sb = _get_client()
    result = sb.table("team_channel_messages").select("*").eq("group_id", group_id).order("created_at", desc=True).limit(limit).execute()
    return result.data


# ── Metrics ────────────────────────────────────────────

def record_metric_event(event_type: str, ticket_id: Optional[str] = None, agent_id: Optional[str] = None,
                          phase: Optional[str] = None, duration_seconds: Optional[float] = None,
                          labels: Optional[dict] = None, value: Optional[float] = None):
    sb = _get_client()
    sb.table("metric_events").insert({
        "event_type": event_type, "ticket_id": ticket_id, "agent_id": agent_id,
        "phase": phase, "duration_seconds": duration_seconds,
        "labels": labels, "value": value,
    }).execute()


def get_metric_events(event_type: Optional[str] = None, ticket_id: Optional[str] = None,
                       agent_id: Optional[str] = None, phase: Optional[str] = None,
                       since: Optional[str] = None, limit: int = 1000) -> List[Dict]:
    sb = _get_client()
    query = sb.table("metric_events").select("*").order("created_at", desc=True).limit(limit)
    if event_type:
        query = query.eq("event_type", event_type)
    if ticket_id:
        query = query.eq("ticket_id", ticket_id)
    if agent_id:
        query = query.eq("agent_id", agent_id)
    if phase:
        query = query.eq("phase", phase)
    if since:
        query = query.gte("created_at", since)
    result = query.execute()
    return [_parse_ticket_row(r) for r in result.data]


def get_metrics_summary(since: Optional[str] = None) -> Dict:
    sb = _get_client()
    now = _now()
    query = sb.table("tickets").select("*")
    if since:
        query = query.gte("created_at", since)
    tickets = query.execute().data
    total = len(tickets)
    merged = sum(1 for t in tickets if t.get("status") == "merged")
    completed = sum(1 for t in tickets if t.get("status") == "completed")
    failed = sum(1 for t in tickets if t.get("status") == "failed")
    total_retries = sum(t.get("retry_count", 0) for t in tickets)
    avg_retries = (total_retries / max(sum(1 for t in tickets if t.get("retry_count", 0) > 0), 1)) if total_retries > 0 else 0
    first_pipeline_passes = sum(1 for t in tickets if t.get("first_pipeline_status") == "passed")
    first_pipeline_total = sum(1 for t in tickets if t.get("first_pipeline_status") and t.get("first_pipeline_status") != "unknown")
    avg_review_cycles = sum(t.get("review_cycle_count", 0) for t in tickets) / max(sum(1 for t in tickets if t.get("review_cycle_count", 0) > 0), 1) if any(t.get("review_cycle_count", 0) > 0 for t in tickets) else 0
    total_llm_cost = sum(float(t.get("llm_total_cost_usd", 0) or 0) for t in tickets)
    total_prompt_tokens = sum(int(t.get("llm_prompt_tokens", 0) or 0) for t in tickets)
    total_completion_tokens = sum(int(t.get("llm_completion_tokens", 0) or 0) for t in tickets)
    return {
        "timestamp": now,
        "success_rate": (merged / total * 100) if total > 0 else 0,
        "merge_rate": (merged / total * 100) if total > 0 else 0,
        "failure_rate": (failed / total * 100) if total > 0 else 0,
        "total_tickets": total,
        "merged_tickets": merged,
        "completed_tickets": completed,
        "failed_tickets": failed,
        "avg_retries": round(avg_retries, 2),
        "total_retries": total_retries,
        "first_pipeline_pass_rate": (first_pipeline_passes / first_pipeline_total * 100) if first_pipeline_total > 0 else 0,
        "avg_review_cycles": round(avg_review_cycles, 2),
        "avg_llm_cost_usd": round(total_llm_cost / max(sum(1 for t in tickets if float(t.get("llm_total_cost_usd", 0) or 0) > 0), 1), 4) if total_llm_cost > 0 else 0,
        "total_llm_cost_usd": round(total_llm_cost, 4),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
    }


def update_ticket_phase_timestamp(ticket_id: str, phase: str):
    phase_columns = {
        "work": "phase_work_started_at",
        "test": "phase_test_started_at",
        "ship": "phase_ship_started_at",
        "listen": "phase_listen_started_at",
    }
    col = phase_columns.get(phase)
    if not col:
        return
    sb = _get_client()
    sb.table("tickets").update({col: _now()}).eq("id", ticket_id).is_(col, "null").execute()


def update_ticket_llm_usage(ticket_id: str, prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0, model: str = ""):
    sb = _get_client()
    ticket = get_ticket(ticket_id)
    if not ticket:
        return
    updates = {
        "llm_prompt_tokens": (ticket.get("llm_prompt_tokens") or 0) + prompt_tokens,
        "llm_completion_tokens": (ticket.get("llm_completion_tokens") or 0) + completion_tokens,
        "llm_total_cost_usd": (float(ticket.get("llm_total_cost_usd") or 0)) + cost_usd,
    }
    if model:
        updates["model_used"] = model
    sb.table("tickets").update(updates).eq("id", ticket_id).execute()


def increment_review_cycle_count(ticket_id: str):
    sb = _get_client()
    ticket = get_ticket(ticket_id)
    if not ticket:
        return
    sb.table("tickets").update({
        "review_cycle_count": (ticket.get("review_cycle_count") or 0) + 1,
    }).eq("id", ticket_id).execute()


def set_ticket_first_pipeline_status(ticket_id: str, status: str):
    sb = _get_client()
    ticket = get_ticket(ticket_id)
    if not ticket:
        return
    if not ticket.get("first_pipeline_status") or ticket["first_pipeline_status"] == "unknown":
        sb.table("tickets").update({"first_pipeline_status": status}).eq("id", ticket_id).execute()


def set_ticket_completed_at(ticket_id: str, status: Optional[str] = None):
    sb = _get_client()
    col = "merged_at" if status == "merged" else "completed_at"
    sb.table("tickets").update({col: _now()}).eq("id", ticket_id).execute()


def set_ticket_primary_repo(ticket_id: str, primary_repo: str):
    sb = _get_client()
    sb.table("tickets").update({"primary_repo": primary_repo}).eq("id", ticket_id).execute()


def set_ticket_line_stats(ticket_id: str, lines_added: int = 0, lines_removed: int = 0, files_changed: int = 0):
    sb = _get_client()
    sb.table("tickets").update({
        "lines_added": lines_added, "lines_removed": lines_removed, "files_changed": files_changed,
    }).eq("id", ticket_id).execute()


# ── Rate Limiting (Supabase) ───────────────────────────

def check_rate_limit(client_ip: str, max_requests: int = 30) -> tuple:
    """Check rate limit using Supabase RPC. Returns (allowed, remaining)."""
    sb = _get_client()
    try:
        result = sb.rpc("check_rate_limit", {"p_client_ip": client_ip, "p_max_requests": max_requests}).execute()
        if result.data:
            return result.data[0].get("allowed", True), result.data[0].get("remaining", max_requests)
    except Exception:
        pass
    return True, max_requests


# ── Realtime ──────────────────────────────────────────

def subscribe_to_table(table: str, callback, event: str = "*"):
    """Subscribe to Supabase realtime changes for a table."""
    sb = _get_client()
    sb.table(table).select("*").execute()
    channel = sb.channel(f"{table}-changes")
    channel.on_postgres_changes(
        event=event, schema="public", table=table, callback=callback
    ).subscribe()
    return channel


# ── Helpers ────────────────────────────────────────────

def _parse_ticket_row(row: Dict) -> Dict:
    if not row:
        return row
    for json_field in ("labels", "selected_repos", "ai_planning"):
        if json_field in row and isinstance(row[json_field], str):
            row[json_field] = _parse_json(row[json_field])
    return row


def _parse_agent_row(row: Dict) -> Dict:
    if not row:
        return row
    if "logs" in row and isinstance(row["logs"], str):
        row["logs"] = _parse_json(row["logs"])
    return row


def _parse_json_fields(row: Dict, fields: List[str]) -> Dict:
    for f in fields:
        if f in row and isinstance(row[f], str):
            row[f] = _parse_json(row[f])
    return row


def get_db():
    return _get_client()