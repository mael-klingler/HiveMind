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