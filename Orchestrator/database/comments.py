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
from typing import Dict, List

from database.sqlite_backend import get_db


def add_ticket_comment(ticket_id: str, author: str = "system", comment_type: str = "comment", content: str = "") -> int:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT INTO ticket_comments (ticket_id, author, comment_type, content, created_at) VALUES (?, ?, ?, ?, ?)",
              (ticket_id, author, comment_type, content, now))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_ticket_comments(ticket_id: str) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM ticket_comments WHERE ticket_id = ? ORDER BY created_at", (ticket_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]