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
Background task: Queue processor – assigns tickets to free agents and spawns K8s pods.
Uses atomic assign_next_queue_item() to prevent race conditions.
Selects the best agent based on repo affinity before claiming the queue item.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from database import (
    ensure_agent_pool, get_idle_agents, assign_next_queue_item, get_ticket,
    set_agent_status, fail_queue_item, requeue_ticket,
    update_ticket_status, add_step, get_queue, get_agent_repo_affinities,
    set_ticket_ai_planning, set_ticket_workspace,
    find_best_agent_for_repo, get_agent_with_profile, get_all_agents_with_profiles,
    get_all_repos as _get_all_repos, assign_queue_item,
)
from config import AGENT_MAX_RETRIES
from workspace import WorkspaceBuilder

log = logging.getLogger("hivemind")

_running = False
_shutdown_requested = False
_worker = None


def _get_worker():
    global _worker
    if _worker is None:
        _worker = WorkspaceBuilder()
    return _worker


def set_shutdown(shutdown: bool):
    global _shutdown_requested
    _shutdown_requested = shutdown


def set_running(running: bool):
    global _running
    _running = running


def _select_best_agent(primary_repo: str, idle_agents: list) -> dict:
    """Select the best idle agent for a given repo.
    Uses repo affinity scoring with load-balancing fallback (pick agent
    with fewest completed tickets for fairness).
    """
    if not idle_agents:
        return None

    if primary_repo:
        best_id = find_best_agent_for_repo(primary_repo, idle_agents)
        if best_id:
            agent = next((a for a in idle_agents if a["id"] == best_id), None)
            if agent:
                return agent

    # Load-balancing fallback: pick the agent that has been idle the longest
    # (completed_at oldest = has been idle longest = next in rotation)
    idle_agents_sorted = sorted(
        idle_agents,
        key=lambda a: a.get("completed_at") or a.get("started_at") or "",
    )
    return idle_agents_sorted[0]


async def queue_processor():
    """Background task: Assigns tickets to free agents and spawns K8s pods.
    Uses Redis distributed lock when available to prevent duplicate processing.
    """
    from background.sse import broadcast_event
    from logging_setup import metrics
    from config import REDIS_ENABLED

    global _running
    _running = True
    log.info("Queue processor started")

    while _running and not _shutdown_requested:
        lock_acquired = False
        try:
            if REDIS_ENABLED:
                from redis_client import acquire_lock, release_lock, renew_lock
                lock_acquired = acquire_lock("hivemind:queue_processor", timeout=30)
                if not lock_acquired:
                    await asyncio.sleep(5)
                    continue

            ensure_agent_pool()

            idle = get_idle_agents()
            if not idle:
                await asyncio.sleep(3)
                continue

            next_item = assign_next_queue_item(idle[0]["id"])
            if not next_item:
                await asyncio.sleep(3)
                continue

            ticket_data = get_ticket(next_item["ticket_id"])
            if ticket_data and ticket_data.get("status") == "stopped":
                fail_queue_item(next_item["id"], "Ticket was stopped")
                set_agent_status(idle[0]["id"], "idle")
                continue

            primary_repo = ""
            if ticket_data:
                if not ticket_data.get("ai_planning") and not ticket_data.get("selected_repos"):
                    try:
                        w = _get_worker()
                        await w._aensure_init()
                        loop = asyncio.get_event_loop()
                        if w.llm.is_available():
                            from git_manager import Ticket
                            _ticket = Ticket(
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

                if ticket_data.get("ai_planning"):
                    try:
                        planning = ticket_data["ai_planning"]
                        if isinstance(planning, str):
                            planning = json.loads(planning)
                        primary_repo = planning.get("primary_repo", "")
                    except (json.JSONDecodeError, TypeError):
                        pass

                if not primary_repo:
                    desc = (ticket_data.get("description", "") + " " + ticket_data.get("title", "")).lower()
                    for a in idle:
                        agent_repos = get_agent_repo_affinities(a["id"])
                        if not agent_repos:
                            continue
                        for rn in agent_repos:
                            if rn.lower() in desc:
                                primary_repo = rn
                                break
                        if primary_repo:
                            break

            # Select best agent based on repo affinity + load balancing
            best_agent = _select_best_agent(primary_repo, idle)

            # If the initially claimed agent differs from best, reassign
            initially_claimed = idle[0]["id"]
            if best_agent["id"] != initially_claimed:
                assign_queue_item(next_item["id"], best_agent["id"])

            agent = best_agent

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
            affinity_msg = f" (repo affinity: {primary_repo})" if primary_repo else ""
            log.info(f"Ticket {next_item['ticket_id']} assigned to agent {agent['name']}{affinity_msg}", extra={"ticket_id": next_item['ticket_id'], "agent_id": agent['id'], "event": "ticket_assigned"})

            if ticket_data:
                from git_manager import Ticket
                ticket = Ticket(
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
        finally:
            if lock_acquired:
                from redis_client import release_lock
                release_lock("hivemind:queue_processor")