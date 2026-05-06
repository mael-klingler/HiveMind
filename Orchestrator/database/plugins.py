from datetime import datetime
from typing import Dict, List

from database.sqlite_backend import get_db


def get_opencode_plugins() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM opencode_plugins ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_enabled_opencode_plugins() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM opencode_plugins WHERE enabled = 1 ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_enabled_plugin_names() -> List[str]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name FROM opencode_plugins WHERE enabled = 1 ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_opencode_plugin(data: Dict) -> str:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""INSERT INTO opencode_plugins (name, enabled, description, requires_binary, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
              (data["name"], int(data.get("enabled", True)), data.get("description", ""),
               data.get("requires_binary", ""), now, now))
    conn.commit()
    conn.close()
    return data["name"]


def update_opencode_plugin(name: str, data: Dict):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    sets, vals = [], []
    for key in ("enabled", "description", "requires_binary"):
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
    vals.append(name)
    c.execute(f"UPDATE opencode_plugins SET {', '.join(sets)}, updated_at = ? WHERE name = ?", vals)
    conn.commit()
    conn.close()


def delete_opencode_plugin(name: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM opencode_plugins WHERE name = ?", (name,))
    conn.commit()
    conn.close()