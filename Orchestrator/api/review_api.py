"""
API routes: Review Lifecycle
"""

from fastapi import APIRouter, HTTPException, Request

from database import (
    get_queue,
    get_ticket,
    requeue_ticket,
    update_ticket_review,
    update_ticket_status,
)
from logging_setup import log
from background.sse import broadcast_event
from config import AGENT_MAX_RETRIES

router = APIRouter()


@router.post("/api/tickets/{ticket_id}/review")
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