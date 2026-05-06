"""
Background task: Review lifecycle monitor – monitors MR status, pipeline failures, and comments via GitLab API.
"""

import asyncio
import hashlib
import hmac
import logging
import os
import re
import traceback
from typing import Dict, Optional

from database import (
    get_open_mr_tickets, get_ticket, update_ticket_status,
    update_ticket_review, set_ticket_mr_url, add_ticket_comment,
    update_ticket_mr_tracking, requeue_ticket,
)
from config import GITLAB_WEBHOOK_SECRET, AGENT_MAX_RETRIES
from k8s_client import cleanup_agent_resources

log = logging.getLogger("hivemind")

from background.queue_processor import _get_worker, _shutdown_requested


def verify_gitlab_webhook(body: bytes, signature: str) -> bool:
    if not GITLAB_WEBHOOK_SECRET:
        return True
    if not signature:
        return False
    expected = hmac.new(
        GITLAB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _fetch_mr(gitlab_host: str, gitlab_token: str, mr_url: str) -> Optional[Dict]:
    from gitlab_client import fetch_mr_sync
    return fetch_mr_sync(gitlab_host, gitlab_token, mr_url)


def _parse_mr_url(mr_url: str) -> tuple:
    from gitlab_client import parse_mr_url
    return parse_mr_url(mr_url)


async def _check_mr_comments(ticket_id, ticket, mr_url, project_path, mr_iid, gitlab_host, gitlab_token):
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

    from logging_setup import metrics
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
            metrics.inc("hivemind_review_cycles_total", labels={"ticket_id": ticket_id})
            if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                log.info(f"Ticket {ticket_id}: Changes requested → re-queued", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                from background.sse import broadcast_event
                await broadcast_event("ticket_requeued", {
                    "ticket_id": ticket_id,
                    "reason": f"Review feedback from {author}",
                    "comment": body[:200],
                })
                await broadcast_event("queue_updated", get_queue())
            else:
                update_ticket_status(ticket_id, "failed")
                metrics.inc("hivemind_tickets_failed_total")
                cleanup_agent_resources(ticket_id)
                log.error(f"Ticket {ticket_id}: Changes requested → failed (max retries)", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
            return


async def review_lifecycle_monitor():
    """Background task: Monitors MR status, pipeline failures and new comments via GitLab API."""
    from background.sse import broadcast_event
    from logging_setup import metrics

    log.info("Review Lifecycle Monitor started")
    await asyncio.sleep(15)

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

                if status == "running":
                    pod_name = f"agent-worker-{ticket_id.lower()}"
                    ns = os.getenv("AGENT_NAMESPACE", "hivemind")
                    rc, out, _ = _get_worker()._main._kubectl(f"get pod {pod_name} -n {ns} -o jsonpath='{{.status.phase}}'")
                    if rc == 0 and out.strip().strip("'\"") in ("Running", "Pending", "ContainerCreating"):
                        continue

                try:
                    mr_data = _fetch_mr(gitlab_host, gitlab_token, mr_url)
                    if not mr_data:
                        continue

                    mr_state = mr_data.get("state", "")

                    if mr_state == "merged":
                        update_ticket_status(ticket_id, "completed")
                        cleanup_agent_resources(ticket_id)
                        update_ticket_review(ticket_id, "approved", "", mr_url)
                        set_ticket_completed_at(ticket_id, status="merged")
                        add_ticket_comment(ticket_id, author="system", comment_type="summary", content="Ticket completed: MR was merged.")
                        record_metric_event("ticket_merged", ticket_id=ticket_id, labels={"source": "review_monitor"})
                        metrics.inc("hivemind_tickets_merged_total")
                        log.info(f"Ticket {ticket_id}: MR merged", extra={"ticket_id": ticket_id, "event": "ticket_merged"})
                        await broadcast_event("ticket_merged", {"ticket_id": ticket_id})
                        continue

                    if mr_state == "closed":
                        update_ticket_review(ticket_id, "changes_requested", "MR closed without merge", mr_url)
                        increment_review_cycle_count(ticket_id)
                        record_metric_event("review_cycle", ticket_id=ticket_id, labels={"outcome": "mr_closed"})
                        if requeue_ticket(ticket_id, AGENT_MAX_RETRIES):
                            log.info(f"Ticket {ticket_id}: MR closed → re-queued", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
                            await broadcast_event("ticket_requeued", {"ticket_id": ticket_id, "reason": "MR closed"})
                        else:
                            update_ticket_status(ticket_id, "failed")
                            metrics.inc("hivemind_tickets_failed_total")
                            cleanup_agent_resources(ticket_id)
                            log.error(f"Ticket {ticket_id}: MR closed → failed (max retries)", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                        continue

                    if mr_state == "opened":
                        update_ticket_phase_timestamp(ticket_id, "listen")
                        project_path, mr_iid = _parse_mr_url(mr_url)
                        if not project_path:
                            continue

                        update_ticket_mr_tracking(
                            ticket_id,
                            project_path=project_path,
                            mr_iid=int(mr_iid) if mr_iid else None,
                        )

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
                                increment_review_cycle_count(ticket_id)
                                record_metric_event("merge_conflict", ticket_id=ticket_id)
                                add_ticket_comment(ticket_id, author="system", comment_type="system",
                                    content="⚠️ Merge conflict detected. Agent will automatically rebase the branch.")
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
                                    cleanup_agent_resources(ticket_id)
                                    log.error(f"Ticket {ticket_id}: Merge conflict → failed (max retries)", extra={"ticket_id": ticket_id, "event": "ticket_failed"})
                                continue
                        elif merge_status == "can_be_merged":
                            update_ticket_mr_tracking(ticket_id, conflict_status="none")

                        pipeline = mr_data.get("head_pipeline") or mr_data.get("pipeline")
                        pipeline_status = pipeline.get("status", "unknown") if pipeline else "unknown"
                        last_pipeline = t.get("mr_pipeline_status", "unknown")

                        if pipeline_status == "failed" and last_pipeline != "failed":
                            set_ticket_first_pipeline_status(ticket_id, "failed")
                            record_metric_event("pipeline_failed", ticket_id=ticket_id, labels={"pipeline_status": "failed"})
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
                            if pipeline_status == "success":
                                set_ticket_first_pipeline_status(ticket_id, "passed")
                                record_metric_event("pipeline_success", ticket_id=ticket_id, labels={"pipeline_status": "success"})

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