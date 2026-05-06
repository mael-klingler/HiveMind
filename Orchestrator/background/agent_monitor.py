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
Background task: Agent pod monitor – checks pod status, re-queues failed tickets, cleans up.
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone

from database import (
    get_tickets, get_ticket, set_agent_status, update_ticket_status,
    requeue_ticket, add_ticket_comment, get_all_agents,
    get_db, get_queue,
)
from background.queue_processor import _get_worker
from background.sse import broadcast_event as _broadcast_event
from config import AGENT_RETRY_DELAY, AGENT_MAX_RETRIES, AGENT_STALE_TIMEOUT
from k8s_client import cleanup_agent_resources
from logging_setup import log, metrics

_shutdown_requested = False


def set_shutdown(val: bool):
    global _shutdown_requested
    _shutdown_requested = val


async def agent_pod_monitor():
    """Background task: Checks agent pod status, re-queues failed tickets, marks completed ones and updates MR URLs."""
    log.info(f"Agent Pod Monitor started (retry-delay: {AGENT_RETRY_DELAY}s, max-retries: {AGENT_MAX_RETRIES})")
    while not _shutdown_requested:
        try:
            completed_pod_ids = set()
            list_rc, list_out, _ = _get_worker()._main._kubectl("get pods -n hivemind -o jsonpath='{range .items[*]}{.metadata.name}{\"\\t\"}{.status.phase}{\"\\n\"}{end}'")
            if list_rc == 0:
                for line in list_out.strip().split("\n"):
                    if not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and parts[1] in ("Succeeded", "Completed"):
                        pn = parts[0]
                        if pn.startswith("agent-worker-"):
                            completed_pod_ids.add(pn.replace("agent-worker-", ""))

            for t in [t for t in get_tickets(status=None) if t.get("status") == "running"]:
                ticket_id = t["id"]
                pod_name = f"agent-worker-{ticket_id.lower()}"
                namespace = os.getenv("AGENT_NAMESPACE", "hivemind")

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

                rc, out, err = _get_worker()._main._kubectl(f"get pod {pod_name} -n {namespace} -o jsonpath='{{.status.phase}}'")
                if rc != 0:
                    if ticket_id.lower() in completed_pod_ids or ticket_id in completed_pod_ids:
                        update_ticket_status(ticket_id, "completed")
                        metrics.inc("hivemind_tickets_completed_total")
                        cleanup_agent_resources(ticket_id)
                        set_agent_status(t.get("agent_id", "") or ticket_id.lower(), "idle")
                        add_ticket_comment(ticket_id, author="system", comment_type="summary", content="Agent pod completed. Ticket was marked as completed.")
                        log.info(f"Ticket {ticket_id}: pod completed", extra={"ticket_id": ticket_id, "event": "ticket_completed"})
                        await _broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                        await _broadcast_event("queue_updated", get_queue())
                    else:
                        agent_id = t.get("agent_id", "") or ticket_id.lower()
                        retry_count = t.get("retry_count", 0)
                        if retry_count < AGENT_MAX_RETRIES:
                            log.info(f"Ticket {ticket_id}: Pod not found → re-queued", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                            requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                            set_agent_status(agent_id, "idle")
                            await _broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "Pod not found"})
                            await _broadcast_event("queue_updated", get_queue())
                        else:
                            log.error(f"Ticket {ticket_id}: Pod not found, max retries reached → failed", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                            update_ticket_status(ticket_id, "failed")
                            metrics.inc("hivemind_tickets_failed_total")
                            cleanup_agent_resources(ticket_id)
                            set_agent_status(agent_id, "idle")
                            add_ticket_comment(ticket_id, author="system", comment_type="system", content="Agent pod not found and max retries reached.")
                            await _broadcast_event("ticket_failed", {"ticket_id": ticket_id})
                    continue

                phase = out.strip().strip("'\"")

                if phase in ("Succeeded", "Completed"):
                    update_ticket_status(ticket_id, "completed")
                    metrics.inc("hivemind_tickets_completed_total")
                    cleanup_agent_resources(ticket_id)
                    set_agent_status(t.get("agent_id", "") or ticket_id.lower(), "idle")
                    add_ticket_comment(ticket_id, author="system", comment_type="summary", content=f"Agent pod status: {phase}. Ticket was marked as completed.")
                    log.info(f"Ticket {ticket_id}: pod {phase}, marking completed", extra={"ticket_id": ticket_id, "event": "ticket_completed"})
                    await _broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                    await _broadcast_event("queue_updated", get_queue())
                    try:
                        _get_worker()._main._kubectl(f"delete pod {pod_name} -n {namespace} --grace-period=0 --force 2>/dev/null")
                    except Exception:
                        pass
                    continue

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
                        metrics.inc("hivemind_ticket_retries_total", labels={"ticket_id": ticket_id})
                        log.info(f"Ticket {ticket_id}: re-queued (retry {new_retry}/{AGENT_MAX_RETRIES})", extra={"ticket_id": ticket_id, "event": "ticket_requeued", "agent_id": agent_id})
                        await _broadcast_event("ticket_requeued", {
                            "ticket_id": ticket_id,
                            "retry_count": new_retry,
                            "max_retries": AGENT_MAX_RETRIES,
                            "reason": f"Pod {phase}",
                        })
                        await _broadcast_event("queue_updated", get_queue())
                    try:
                        _get_worker()._main._kubectl(f"delete pod {pod_name} -n {namespace} --grace-period=0 --force 2>/dev/null")
                    except Exception:
                        pass
                    cleanup_agent_resources(ticket_id)

            # ── Recover: if ticket is failed/queued but pod is still running, resume it ──
            for t in [t for t in get_tickets(status=None) if t.get("status") in ("failed", "queued")]:
                ticket_id = t["id"]
                pod_name = f"agent-worker-{ticket_id.lower()}"
                namespace = os.getenv("AGENT_NAMESPACE", "hivemind")

                try:
                    from k8s_client import get_pod as _get_pod, delete_pod as _delete_pod
                    pod = _get_pod(pod_name, namespace)
                except Exception:
                    continue
                if not pod:
                    continue

                phase = pod.status.phase if pod.status else "Unknown"

                # If pod is in a terminal or error state, clean it up and re-queue
                if phase in ("Failed", "Succeeded"):
                    agent_id = t.get("agent_id", "") or ticket_id.lower()
                    log.info(f"Ticket {ticket_id}: Pod is {phase}, deleting and re-queuing", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                    try:
                        _delete_pod(pod_name, namespace)
                    except Exception:
                        pass
                    cleanup_agent_resources(ticket_id)
                    if t.get("retry_count", 0) < AGENT_MAX_RETRIES:
                        requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                        set_agent_status(agent_id, "idle")
                        await _broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": f"Pod {phase}, cleaned up"})
                        await _broadcast_event("queue_updated", get_queue())
                    else:
                        log.error(f"Ticket {ticket_id}: Pod {phase}, max retries reached → failed", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                        update_ticket_status(ticket_id, "failed")
                        set_agent_status(agent_id, "idle")
                        await _broadcast_event("ticket_failed", {"ticket_id": ticket_id})
                    continue

                # Pod is Running/Pending — check if containers are actually ready
                if phase in ("Running", "Pending", "ContainerCreating"):
                    container_ready = False
                    init_ok = True
                    if pod.status and pod.status.container_statuses:
                        for cs in pod.status.container_statuses:
                            if cs.ready:
                                container_ready = True
                                break
                    if pod.status and pod.status.init_container_statuses:
                        for ics in pod.status.init_container_statuses:
                            if ics.state and ics.state.terminated and ics.state.terminated.exit_code != 0:
                                init_ok = False
                                break
                            if ics.state and ics.state.waiting and ics.state.waiting.reason in ("CrashLoopBackOff", "ImagePullBackOff"):
                                init_ok = False
                                break

                    if init_ok and (container_ready or phase == "Pending"):
                        agent_id = t.get("agent_id", "") or ticket_id.lower()
                        old_status = t.get("status", "unknown")
                        log.info(f"Ticket {ticket_id}: Pod is {phase} but ticket is '{old_status}' → recovering to running", extra={"ticket_id": ticket_id, "event": "ticket_recovered"})
                        update_ticket_status(ticket_id, "running")
                        set_agent_status(agent_id, "running", ticket_id)
                        add_ticket_comment(ticket_id, author="system", comment_type="system", content=f"Pod detected as {phase} while ticket was '{old_status}'. Recovered to running.")
                        await _broadcast_event("ticket_updated", {"ticket_id": ticket_id, "status": "running", "reason": f"pod_{phase}_recovered_from_{old_status}"})
                        await _broadcast_event("queue_updated", get_queue())
                    elif not init_ok or (phase == "Running" and not container_ready):
                        # Pod stuck (init failed or container not ready) — nuke and re-queue
                        agent_id = t.get("agent_id", "") or ticket_id.lower()
                        log.warning(f"Ticket {ticket_id}: Pod is {phase} but containers not ready (init_ok={init_ok}, container_ready={container_ready}), deleting and re-queuing", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                        try:
                            _delete_pod(pod_name, namespace)
                        except Exception:
                            pass
                        cleanup_agent_resources(ticket_id)
                        if t.get("retry_count", 0) < AGENT_MAX_RETRIES:
                            requeue_ticket(ticket_id, AGENT_MAX_RETRIES)
                            set_agent_status(agent_id, "idle")
                            await _broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "Pod containers not ready"})
                            await _broadcast_event("queue_updated", get_queue())
                        else:
                            log.error(f"Ticket {ticket_id}: Pod stuck, max retries reached → failed", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                            update_ticket_status(ticket_id, "failed")
                            set_agent_status(agent_id, "idle")
                            await _broadcast_event("ticket_failed", {"ticket_id": ticket_id})

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
                        project_path = repo.url.split("://")[-1].replace(".git", "").replace(":", "") if "://" in repo.url else ""
                        if "/" in project_path and ":" in project_path.split("/")[0]:
                            project_path = "/".join(project_path.split("/")[1:])
                        if not project_path:
                            continue
                        try:
                            mrs = await search_open_mrs(gitlab_host, gitlab_token, project_path, branch_name)
                            if mrs:
                                mr_url = mrs[0].get("web_url", "")
                                if mr_url and not t.get("mr_url"):
                                    from database import set_ticket_mr_url, update_ticket_mr_tracking
                                    set_ticket_mr_url(ticket_id, mr_url)
                                    add_ticket_comment(ticket_id, author="system", comment_type="mr_created", content=f"Merge Request created: {mr_url}")
                                    update_ticket_mr_tracking(ticket_id, pipeline_status=mrs[0].get("head_pipeline", {}).get("status", "unknown") if mrs[0].get("head_pipeline") else "unknown")
                                    conn = get_db()
                                    cursor = conn.cursor()
                                    cursor.execute("UPDATE tickets SET mr_status = %s WHERE id = %s", ("open", ticket_id))
                                    conn.commit()
                                    conn.close()
                                    log.info(f"Ticket {ticket_id}: MR found → {mr_url}", extra={"ticket_id": ticket_id, "event": "mr_found"})
                                break
                        except Exception:
                            pass

            stale_threshold = int(os.getenv("AGENT_STALE_TIMEOUT", str(AGENT_STALE_TIMEOUT)))
            for t in [t for t in get_tickets(status=None) if t.get("status") == "running"]:
                ticket_id = t["id"]
                pod_name = f"agent-worker-{ticket_id.lower()}"
                ns = os.getenv("AGENT_NAMESPACE", "hivemind")
                pod_still_active = False
                try:
                    rc, out, err = _get_worker()._main._kubectl(f"get pod {pod_name} -n {ns} -o jsonpath='{{.status.phase}}'")
                    if rc == 0:
                        phase = out.strip().strip("'\"")
                        if phase in ("Running", "Pending", "ContainerCreating"):
                            pod_still_active = True
                    else:
                        rc2, out2, _ = _get_worker()._main._kubectl(f"get pod {pod_name} -n {ns} -o name 2>/dev/null")
                        if rc2 == 0 and pod_name in out2:
                            pod_still_active = True
                except Exception:
                    pod_still_active = True

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
                            cleanup_agent_resources(ticket_id)
                            agent_id = t.get("agent_id", "") or f"agent-{ticket_id.lower()}"
                            set_agent_status(agent_id, "idle")
                            add_ticket_comment(ticket_id, author="system", comment_type="summary", content="Pod disappeared. Ticket automatically marked as completed.")
                            log.info(f"Ticket {ticket_id}: Pod disappeared, >30min old → 'completed', Agent {agent_id} → 'idle'", extra={"ticket_id": ticket_id, "agent_id": agent_id, "event": "ticket_completed"})
                            await _broadcast_event("ticket_completed", {"ticket_id": ticket_id})
                            await _broadcast_event("queue_updated", get_queue())
                            try:
                                _get_worker()._main._kubectl(f"delete pod {pod_name} -n {ns} --grace-period=0 --force 2>/dev/null")
                            except Exception:
                                pass
                    except (ValueError, TypeError):
                        pass

            all_agents = get_all_agents()
            all_tickets = {t["id"]: t for t in get_tickets(status=None)}

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
                        ticket_owners[tid] = a["id"]
                    else:
                        set_agent_status(a["id"], "idle")
                else:
                    ticket_owners[tid] = a["id"]

            for a in all_agents:
                if a["status"] != "running":
                    continue
                tid = a.get("current_ticket_id")
                if not tid:
                    set_agent_status(a["id"], "idle")
                    continue
                ticket = all_tickets.get(tid)
                if not ticket or ticket.get("status") not in ("running", "queued"):
                    set_agent_status(a["id"], "idle")

            await asyncio.sleep(30)
        except Exception as e:
            log.error(f"Agent pod monitor error: {e}", exc_info=True)
            await asyncio.sleep(30)