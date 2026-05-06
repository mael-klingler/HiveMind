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