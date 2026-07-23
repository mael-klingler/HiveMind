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

from datetime import datetime
from typing import Dict, List, Optional

from database.sqlite_backend import get_db, _add_column_if_not_exists


def init_queue_extensions():
    conn = get_db()
    c = conn.cursor()
    _add_column_if_not_exists(c, "queue", "priority", "INTEGER DEFAULT 5")
    conn.commit()
    conn.close()


def get_next_queue_item() -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT * FROM queue
    WHERE status = 'waiting'
    ORDER BY priority ASC, position ASC, id ASC
    LIMIT 1
    """)
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def assign_next_queue_item(agent_id: str) -> Optional[Dict]:
    """Atomically claim the next waiting queue item for an agent.

    Uses BEGIN IMMEDIATE to acquire an exclusive lock in SQLite,
    preventing concurrent claim-and-assign race conditions.
    Returns the claimed item dict or None if nothing available.
    """
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        c = conn.cursor()
        c.execute("""
        SELECT * FROM queue
        WHERE status = 'waiting'
        ORDER BY priority ASC, position ASC, id ASC
        LIMIT 1
        """)
        row = c.fetchone()
        if not row:
            conn.rollback()
            return None
        item = dict(row)
        now = datetime.now().isoformat()
        c.execute("UPDATE queue SET status = 'running', assigned_agent_id = ?, started_at = ? WHERE id = ? AND status = 'waiting'",
                  (agent_id, now, item["id"]))
        if c.rowcount == 0:
            conn.rollback()
            return None
        conn.commit()
        item["status"] = "running"
        item["assigned_agent_id"] = agent_id
        item["started_at"] = now
        return item
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def assign_queue_item(queue_id: int, agent_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'running', assigned_agent_id = ?, started_at = ? WHERE id = ? AND status = 'waiting'",
              (agent_id, now, queue_id))
    conn.commit()
    conn.close()
    return c.rowcount > 0


def complete_queue_item(queue_id: int):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'completed', completed_at = ? WHERE id = ?", (now, queue_id))
    conn.commit()
    conn.close()


def fail_queue_item(queue_id: int, error: str):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'failed', completed_at = ? WHERE id = ?", (now, queue_id))
    c.execute("INSERT INTO steps (queue_id, step_name, status, detail, timestamp) VALUES (?, ?, ?, ?, ?)",
              (queue_id, "Error", "error", error, now))
    conn.commit()
    conn.close()


def requeue_ticket(ticket_id: str, max_retries: int = 3) -> bool:
    from database.tickets import get_ticket
    ticket = get_ticket(ticket_id)
    if not ticket:
        return False
    retry_count = ticket.get("retry_count", 0) or 0
    if retry_count >= max_retries:
        return False

    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    priority = 2 if ticket.get("priority") == "High" else (1 if ticket.get("priority") == "Critical" else 5)
    c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM queue")
    next_pos = c.fetchone()[0]
    c.execute(
        "INSERT INTO queue (ticket_id, position, status, created_at, priority) VALUES (?, ?, 'waiting', ?, ?)",
        (ticket_id, next_pos, now, priority)
    )
    c.execute("UPDATE tickets SET retry_count = retry_count + 1, status = 'queued', updated_at = ? WHERE id = ?",
              (now, ticket_id))
    conn.commit()
    conn.close()
    return True


def get_queue() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT q.*, t.title, a.name as agent_name
    FROM queue q
    LEFT JOIN tickets t ON q.ticket_id = t.id
    LEFT JOIN agents a ON q.assigned_agent_id = a.id
    ORDER BY
      CASE q.status
        WHEN 'running' THEN 1
        WHEN 'waiting' THEN 2
        WHEN 'completed' THEN 3
        WHEN 'failed' THEN 4
      END,
      q.priority ASC,
      q.position ASC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]