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


def add_step(queue_id: int, ticket_id: str, agent_id: str, step_name: str, status: str = "running", detail: str = ""):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""
    INSERT INTO steps (queue_id, ticket_id, agent_id, step_name, status, detail, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (queue_id, ticket_id, agent_id, step_name, status, detail, now))
    conn.commit()
    conn.close()


def get_steps(ticket_id: Optional[str] = None, agent_id: Optional[str] = None, queue_id: Optional[int] = None) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    conditions = []
    params = []

    if ticket_id:
        conditions.append("ticket_id = ?")
        params.append(ticket_id)
    if agent_id:
        conditions.append("agent_id = ?")
        params.append(agent_id)
    if queue_id:
        conditions.append("queue_id = ?")
        params.append(queue_id)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    c.execute(f"SELECT * FROM steps {where} ORDER BY timestamp DESC", params)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_steps() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT s.*, q.ticket_id, t.title
    FROM steps s
    LEFT JOIN queue q ON s.queue_id = q.id
    LEFT JOIN tickets t ON s.ticket_id = t.id
    ORDER BY s.timestamp DESC
    LIMIT 200
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]