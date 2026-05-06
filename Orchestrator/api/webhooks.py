"""
API routes: GitLab and GitHub Webhooks
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
from typing import Dict, List, Optional

import time as _time

from fastapi import APIRouter, HTTPException, Request

from database import (
    add_ticket_comment,
    create_ticket,
    get_queue,
    get_ticket,
    requeue_ticket,
    set_agent_status,
    update_ticket_review,
    update_ticket_status,
)
from logging_setup import log, metrics
from background.sse import broadcast_event
from config import AGENT_MAX_RETRIES, GITLAB_WEBHOOK_SECRET

router = APIRouter()

_webhook_dedup: Dict[str, float] = {}
_WEBHOOK_DEDUP_TTL = 300
_webhook_dedup_lock = asyncio.Lock()


def verify_gitlab_webhook(body: bytes, signature: str) -> bool:
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
    issue = payload.get("object_attributes", {})
    if not issue:
        return {"ok": False, "error": "No issue data"}

    action = issue.get("action", "")
    if action not in ("open", "update", "reopen"):
        return {"ok": True, "ignored": f"action={action}"}

    ticket_id = f"GL-{issue.get('iid', issue.get('id', 'UNKNOWN'))}"

    existing = get_ticket(ticket_id)
    if existing:
        return {"ok": True, "id": ticket_id, "status": "already_exists"}

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
    mr = payload.get("object_attributes", {})
    if not mr:
        return {"ok": False, "error": "No MR data"}

    action = mr.get("action", "")
    state = mr.get("state", "")
    mr_url = mr.get("url", "")
    source_branch = mr.get("source_branch", "")

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

    if action in ("merge", "close") or state == "merged":
        update_ticket_review(ticket_id, "approved", f"MR {action}: {mr_url}", mr_url)
        update_ticket_status(ticket_id, "merged")
        log.info(f"MR merged → Ticket {ticket_id} set to 'merged'", extra={"ticket_id": ticket_id, "event": "ticket_merged"})
        await broadcast_event("ticket_merged", {"ticket_id": ticket_id, "mr_url": mr_url})

        agent_pod = os.environ.get(f"AGENT_POD_{ticket_id}")
        if agent_pod:
            from k8s_client import kubectl_exec
            namespace = os.getenv("AGENT_NAMESPACE", "hivemind")
            kubectl_exec(f"delete pod {agent_pod} -n {namespace} --grace-period=0 --force")
            log.info(f"Agent pod {agent_pod} deleted", extra={"ticket_id": ticket_id, "pod_name": agent_pod})
        _cleanup_agent_resources(ticket_id)

    elif action == "update" and state == "opened":
        pass

    elif action == "reopen":
        update_ticket_review(ticket_id, "changes_requested", f"MR reopened: {mr_url}", mr_url)
        update_ticket_status(ticket_id, "queued")
        log.info(f"MR reopened → Ticket {ticket_id} added to re-queue", extra={"ticket_id": ticket_id, "event": "ticket_requeued"})
        await broadcast_event("ticket_queued", {"ticket_id": ticket_id, "mr_url": mr_url})

    return {"ok": True, "ticket_id": ticket_id, "action": action}


def _cleanup_agent_resources(ticket_id: str):
    from k8s_client import cleanup_agent_resources
    cleanup_agent_resources(ticket_id)


@router.post("/webhooks/gitlab")
async def gitlab_webhook(req: Request):
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

    if "issue" in event_type:
        return await handle_gitlab_issue(payload)

    elif "merge request" in event_type:
        return await handle_gitlab_mr(payload)

    elif "note" in event_type or "comment" in event_type:
        note = payload.get("object_attributes", {})
        noteable_type = note.get("noteable_type", "").lower()

        if noteable_type == "mergerequest":
            mr = payload.get("merge_request", {})
            source_branch = mr.get("source_branch", "")

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


@router.post("/webhooks/github")
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