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

from datetime import datetime
from typing import Dict, List, Optional

from database.sqlite_backend import get_db


def get_next_queue_item() -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT * FROM queue
    WHERE status = 'waiting'
    ORDER BY position ASC, id ASC
    LIMIT 1
    """)
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def assign_queue_item(queue_id: int, agent_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'running', assigned_agent_id = ?, started_at = ? WHERE id = ?",
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
      q.position ASC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]