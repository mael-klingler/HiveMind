from typing import Dict, List, Optional

from database.sqlite_backend import get_db


def create_ticket_group(group_id, parent_ticket_id, title="", description=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO ticket_groups (id, parent_ticket_id, title, description) VALUES (?, ?, ?, ?)",
              (group_id, parent_ticket_id, title, description))
    conn.commit()
    conn.close()
    return group_id


def get_ticket_group(group_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM ticket_groups WHERE id = ?", (group_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def add_team_message(group_id, sender_agent_id, content, message_type="info"):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO team_channel_messages (group_id, sender_agent_id, message_type, content) VALUES (?, ?, ?, ?)",
              (group_id, sender_agent_id, message_type, content))
    conn.commit()
    row_id = c.lastrowid
    conn.close()
    return row_id


def get_team_messages(group_id, limit=50):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM team_channel_messages WHERE group_id = ? ORDER BY created_at DESC LIMIT ?", (group_id, limit))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]