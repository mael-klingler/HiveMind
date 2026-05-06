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
from typing import Dict, List

from database.sqlite_backend import get_db


def get_agent_instructions() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM agent_instructions ORDER BY sort_order, id")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_enabled_agent_instructions() -> str:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT content FROM agent_instructions WHERE enabled = 1 ORDER BY sort_order, id")
    rows = c.fetchall()
    conn.close()
    return "\n\n".join(r["content"] for r in rows)


def add_agent_instruction(data: Dict) -> int:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""INSERT INTO agent_instructions (name, content, enabled, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
              (data["name"], data["content"], int(data.get("enabled", True)),
               data.get("sort_order", 0), now, now))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def update_agent_instruction(id: int, data: Dict):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    sets = []
    vals = []
    for key in ("name", "content", "enabled", "sort_order"):
        if key in data:
            val = data[key]
            if key == "enabled":
                val = int(val)
            sets.append(f"{key} = ?")
            vals.append(val)
    if not sets:
        conn.close()
        return
    vals.append(now)
    vals.append(id)
    c.execute(f"UPDATE agent_instructions SET {', '.join(sets)}, updated_at = ? WHERE id = ?", vals)
    conn.commit()
    conn.close()


def delete_agent_instruction(id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_instructions WHERE id = ?", (id,))
    c.execute("DELETE FROM agent_instruction_assignments WHERE instruction_id = ?", (id,))
    conn.commit()
    conn.close()