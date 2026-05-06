# Copyright 2025 Mael Klingler
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

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.sqlite_backend import get_db


def create_ticket(ticket: Dict[str, Any]) -> str:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    selected_repos = ticket.get("selected_repos", [])
    selected_repos_json = json.dumps(selected_repos) if selected_repos else None
    c.execute("""
    INSERT INTO tickets (id, title, description, labels, issue_type, priority, status, created_at, updated_at, selected_repos)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticket["id"], ticket["title"], ticket.get("description", ""),
        json.dumps(ticket.get("labels", [])), ticket.get("issue_type", "Task"),
        ticket.get("priority", "Medium"), "queued", now, now,
        selected_repos_json
    ))
    conn.commit()

    # Insert into queue
    c.execute("SELECT COUNT(*) FROM queue WHERE status IN ('waiting', 'running', 'queued')")
    pos = c.fetchone()[0] + 1
    c.execute("""
    INSERT INTO queue (ticket_id, position, status, created_at)
    VALUES (?, ?, ?, ?)
    """, (ticket["id"], pos, "waiting", now))
    conn.commit()
    conn.close()
    return ticket["id"]


def get_tickets(status: Optional[str] = None) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    if status:
        c.execute("SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        c.execute("SELECT * FROM tickets ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket(ticket_id: str) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def update_ticket_status(ticket_id: str, status: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
              (status, datetime.now().isoformat(), ticket_id))
    conn.commit()
    if status not in ("running", "queued"):
        c.execute("SELECT agent_id FROM tickets WHERE id = ?", (ticket_id,))
        row = c.fetchone()
        if row and row["agent_id"]:
            c.execute("UPDATE agents SET status = 'idle', current_ticket_id = NULL, progress = 0 WHERE id = ?", (row["agent_id"],))
            conn.commit()
    conn.close()


def stop_ticket(ticket_id: str) -> bool:
    """Stop a running/queued ticket: cancel queue entry, set status to 'stopped'."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, status FROM queue WHERE ticket_id = ? AND status IN ('waiting', 'running', 'queued')", (ticket_id,))
    queue_row = c.fetchone()
    if queue_row:
        c.execute("UPDATE queue SET status = 'completed', completed_at = ? WHERE id = ?", (datetime.now().isoformat(), queue_row["id"]))
    c.execute("UPDATE tickets SET status = 'stopped', updated_at = ? WHERE id = ? AND status NOT IN ('completed', 'stopped')",
              (datetime.now().isoformat(), ticket_id))
    affected = c.rowcount
    # Reset assigned agent
    c.execute("UPDATE agents SET status = 'idle', current_ticket_id = NULL, progress = 0 WHERE current_ticket_id = ?", (ticket_id,))
    conn.commit()
    conn.close()
    return affected > 0


# ── Review / MR Lifecycle ──────────────────────────────────

def get_tickets_with_queue() -> List[Dict]:
    """All tickets with current queue status."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT t.*, q.status as queue_status, q.assigned_agent_id
    FROM tickets t
    LEFT JOIN queue q ON t.id = q.ticket_id AND q.status != 'completed'
    ORDER BY t.updated_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ticket_review(ticket_id: str, review_status: str, notes: str = "", mr_url: str = ""):
    """Save review result: approved or changes_requested."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    if review_status == "approved":
        c.execute("""
        UPDATE tickets SET status = 'merged', mr_status = 'merged', review_status = 'approved',
        review_notes = ?, mr_url = ?, updated_at = ? WHERE id = ?
        """, (notes, mr_url, now, ticket_id))
    elif review_status == "changes_requested":
        # Re-enqueue ticket
        c.execute("""
        UPDATE tickets SET status = 'queued', review_status = 'changes_requested',
        review_notes = ?, retry_count = retry_count + 1, mr_url = ?, updated_at = ? WHERE id = ?
        """, (notes, mr_url, now, ticket_id))
        # Create new queue entry
        c.execute("""
        INSERT INTO queue (ticket_id, position, status, created_at)
        VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', ?)
        """, (ticket_id, now))
    else:
        c.execute("""
        UPDATE tickets SET review_status = ?, review_notes = ?, mr_url = ?, updated_at = ? WHERE id = ?
        """, (review_status, notes, mr_url, now, ticket_id))

    conn.commit()
    conn.close()


def set_ticket_mr_url(ticket_id: str, mr_url: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET mr_url = ?, mr_status = 'open', updated_at = ? WHERE id = ?",
              (mr_url, datetime.now().isoformat(), ticket_id))
    conn.commit()
    conn.close()


def set_ticket_workspace(ticket_id: str, workspace_path: str, agent_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET workspace_path = ?, agent_id = ? WHERE id = ?",
              (workspace_path, agent_id, ticket_id))
    conn.commit()
    conn.close()


def set_ticket_ai_planning(ticket_id: str, planning: Dict):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET ai_planning = ?, updated_at = ? WHERE id = ?",
              (json.dumps(planning, ensure_ascii=False), datetime.now().isoformat(), ticket_id))
    conn.commit()
    conn.close()


def update_ticket_description(ticket_id: str, description: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET description = ?, updated_at = ? WHERE id = ?",
              (description, datetime.now().isoformat(), ticket_id))
    conn.commit()
    conn.close()


def requeue_ticket(ticket_id: str, max_retries: int = 3) -> bool:
    """Puts a failed ticket back into the queue if retry_count < max_retries."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    c.execute("SELECT retry_count, status FROM tickets WHERE id = ?", (ticket_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    retry_count = row["retry_count"]
    if retry_count >= max_retries:
        conn.close()
        return False

    c.execute("""
    UPDATE tickets SET status = 'queued', retry_count = retry_count + 1, updated_at = ?
    WHERE id = ?
    """, (now, ticket_id))

    c.execute("""
    INSERT INTO queue (ticket_id, position, status, created_at)
    VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', ?)
    """, (ticket_id, now))

    conn.commit()
    conn.close()
    return True


def reopen_ticket(ticket_id: str) -> bool:
    """Manually reopens a completed/failed ticket. Does NOT increment retry_count."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    c.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    c.execute("""
    UPDATE tickets SET status = 'queued', mr_status = 'none', review_status = 'pending',
    mr_conflict_status = 'none', updated_at = ?
    WHERE id = ?
    """, (now, ticket_id))

    c.execute("""
    INSERT INTO queue (ticket_id, position, status, created_at)
    VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', ?)
    """, (ticket_id, now))

    conn.commit()
    conn.close()
    return True


def get_failed_tickets() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE status = 'failed'")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_mr_tickets() -> List[Dict]:
    """Tickets with an open MR (for monitoring pipelines and comments)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE mr_status = 'open' OR mr_url IS NOT NULL")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ticket_mr_tracking(ticket_id: str, pipeline_status: str = None, last_note_id: int = None, project_path: str = None, mr_iid: int = None, conflict_status: str = None):
    conn = get_db()
    c = conn.cursor()
    updates = []
    params = []
    if pipeline_status is not None:
        updates.append("mr_pipeline_status = ?")
        params.append(pipeline_status)
    if last_note_id is not None:
        updates.append("mr_last_note_id = ?")
        params.append(last_note_id)
    if project_path is not None:
        updates.append("mr_project_path = ?")
        params.append(project_path)
    if mr_iid is not None:
        updates.append("mr_iid = ?")
        params.append(mr_iid)
    if conflict_status is not None:
        updates.append("mr_conflict_status = ?")
        params.append(conflict_status)
    if not updates:
        conn.close()
        return
    params.append(ticket_id)
    c.execute(f"UPDATE tickets SET {', '.join(updates)}, updated_at = ? WHERE id = ?",
              params[:-1] + [datetime.now().isoformat(), ticket_id])
    conn.commit()
    conn.close()