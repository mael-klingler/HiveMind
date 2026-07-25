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
API routes: Agents
"""

import os

from fastapi import APIRouter, Request

from database import (
    add_step,
    complete_queue_item,
    get_all_agents,
    get_all_steps,
    set_agent_status,
    update_ticket_status,
    add_ticket_comment,
    get_queue,
    update_ticket_llm_usage,
    set_ticket_line_stats,
    set_ticket_mr_url,
)
from logging_setup import log, metrics
from background.sse import broadcast_event

router = APIRouter()


async def _try_create_mr_for_ticket(ticket_id: str, source_branch: str):
    gitlab_host = os.getenv("GITLAB_HOST", os.getenv("GIT_TOKEN", ""))
    gitlab_token = os.getenv("GITLAB_TOKEN", os.getenv("GIT_TOKEN", ""))
    if not gitlab_host or not gitlab_token:
        return

    from database import get_ticket, get_all_repos
    ticket = get_ticket(ticket_id)
    if not ticket:
        return

    selected_repos = ticket.get("selected_repos") or []
    if isinstance(selected_repos, str):
        try:
            import json
            selected_repos = json.loads(selected_repos)
        except Exception:
            selected_repos = []

    all_repos = get_all_repos(active_only=True)
    repo_map = {r["name"]: r for r in all_repos}

    repo_name = selected_repos[0] if selected_repos else (all_repos[0]["name"] if all_repos else None)
    if not repo_name or repo_name not in repo_map:
        return

    repo = repo_map[repo_name]
    url = repo.get("url", "")
    project_path = url.split("://")[-1].replace(".git", "")
    if "/" in project_path and ":" in project_path.split("/")[0]:
        project_path = "/".join(project_path.split("/")[1:])

    if not project_path:
        return

    target_branch = repo.get("branch", "main")
    title = ticket.get("title", f"Fix: {ticket_id}")
    description = f"Automated MR for ticket {ticket_id}.\n\n{ticket.get('description', '')}"

    try:
        from vcs.gitlab import GitLabProvider
        provider = GitLabProvider()
        result = await provider.create_mr(project_path, source_branch, target_branch, title, description)
        if result and result.get("iid"):
            mr_url = result.get("web_url", "")
            if mr_url:
                set_ticket_mr_url(ticket_id, mr_url)
                add_ticket_comment(ticket_id, author="hivemind", comment_type="mr", content=f"Auto-created MR: {mr_url}")
                log.info(f"Auto-created MR for ticket {ticket_id}: {mr_url}", extra={"ticket_id": ticket_id, "mr_url": mr_url})
            else:
                mr_iid = result.get("iid")
                host = gitlab_host.rstrip("/")
                mr_url = f"{host}/{project_path}/-/merge_requests/{mr_iid}"
                set_ticket_mr_url(ticket_id, mr_url)
                add_ticket_comment(ticket_id, author="hivemind", comment_type="mr", content=f"Auto-created MR: {mr_url}")
                log.info(f"Auto-created MR for ticket {ticket_id}: {mr_url}", extra={"ticket_id": ticket_id, "mr_url": mr_url})
    except Exception as e:
        log.warning(f"Auto-create MR failed for ticket {ticket_id}: {e}", extra={"ticket_id": ticket_id})

VALID_AGENT_TRANSITIONS = {
    "idle": {"running"},
    "running": {"idle", "error", "stopped"},
    "error": {"idle", "running"},
    "stopped": {"idle"},
}


def _validate_transition(current: str, target: str) -> bool:
    allowed = VALID_AGENT_TRANSITIONS.get(current, set())
    return target in allowed


@router.get("/api/agents")
def api_agents():
    return get_all_agents()


@router.get("/api/agents/{agent_id}")
def api_agent(agent_id: str):
    from database import get_or_create_agent
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
    """Mark an agent's current ticket as completed and free the agent.
    The queue processor will automatically assign the next ticket.
    """
    data = await req.json()
    queue_id = data.get("queue_id")
    ticket_id = data.get("ticket_id")
    mr_url = data.get("mr_url")

    lines_added = data.get("lines_added", 0)
    lines_removed = data.get("lines_removed", 0)
    files_changed = data.get("files_changed", 0)
    prompt_tokens = data.get("prompt_tokens", 0)
    completion_tokens = data.get("completion_tokens", 0)
    cost_usd = data.get("cost_usd", 0.0)
    model = data.get("model", "")

    set_agent_status(agent_id, "idle", progress=100)

    if queue_id:
        complete_queue_item(queue_id)

    if ticket_id:
        update_ticket_status(ticket_id, "completed")
        add_ticket_comment(ticket_id, author=agent_id, comment_type="summary", content=f"Agent {agent_id} has completed the task.")

        if mr_url:
            set_ticket_mr_url(ticket_id, mr_url)
            add_ticket_comment(ticket_id, author=agent_id, comment_type="mr", content=f"Merge request: {mr_url}")
            log.info(f"Agent {agent_id} reported MR for ticket {ticket_id}: {mr_url}", extra={"ticket_id": ticket_id, "mr_url": mr_url})
        else:
            source_branch = data.get("source_branch") or data.get("branch") or f"feature/{ticket_id.lower()}"
            await _try_create_mr_for_ticket(ticket_id, source_branch)

        if lines_added or lines_removed or files_changed:
            set_ticket_line_stats(ticket_id, lines_added, lines_removed, files_changed)

        if prompt_tokens or completion_tokens:
            update_ticket_llm_usage(ticket_id, prompt_tokens, completion_tokens, cost_usd, model)

        metrics.inc("hivemind_tickets_completed_total")
        if lines_added or lines_removed:
            metrics.observe("hivemind_ticket_lines_added", lines_added or 0)
            metrics.observe("hivemind_ticket_lines_removed", lines_removed or 0)
            metrics.observe("hivemind_ticket_files_changed", files_changed or 0)
        if prompt_tokens:
            metrics.observe("hivemind_llm_prompt_tokens", prompt_tokens)
            metrics.observe("hivemind_llm_completion_tokens", completion_tokens)

    await broadcast_event("queue_updated", get_queue())
    await broadcast_event("agent_updated", {
        "agent_id": agent_id,
        "status": "idle",
    })

    return {"ok": True}


@router.post("/api/agents/{agent_id}/error")
async def api_agent_error(agent_id: str, req: Request):
    """Mark an agent's current ticket as failed and requeue if retries remain."""
    from database import fail_queue_item, requeue_ticket, get_agent

    data = await req.json()
    queue_id = data.get("queue_id")
    ticket_id = data.get("ticket_id")
    error = data.get("error", "Unknown error")

    set_agent_status(agent_id, "error")

    if queue_id:
        fail_queue_item(queue_id, error)

    if ticket_id:
        max_retries = 3
        requeued = requeue_ticket(ticket_id, max_retries=max_retries)
        if requeued:
            log.info(f"Ticket {ticket_id} requeued after agent {agent_id} error", extra={"ticket_id": ticket_id, "agent_id": agent_id})
        else:
            update_ticket_status(ticket_id, "failed")
            log.warning(f"Ticket {ticket_id} failed permanently after max retries", extra={"ticket_id": ticket_id, "agent_id": agent_id})

    await broadcast_event("queue_updated", get_queue())
    await broadcast_event("agent_updated", {
        "agent_id": agent_id,
        "status": "error",
    })

    return {"ok": True}