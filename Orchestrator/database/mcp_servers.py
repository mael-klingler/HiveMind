import json
from datetime import datetime
from typing import Dict, List

from database.sqlite_backend import get_db


def get_mcp_servers() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM mcp_servers ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_enabled_mcp_servers() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_mcp_server(data: Dict) -> str:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""INSERT INTO mcp_servers (name, enabled, server_type, command, args, env, description, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
              (data["name"], int(data.get("enabled", True)), data.get("server_type", "local"),
               data.get("command", ""), json.dumps(data.get("args", [])),
               json.dumps(data.get("env", {})), data.get("description", ""), now, now))
    conn.commit()
    conn.close()
    return data["name"]


def update_mcp_server(name: str, data: Dict):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    sets = []
    vals = []
    for key in ("enabled", "server_type", "command", "args", "env", "description"):
        if key in data:
            val = data[key]
            if key == "enabled":
                val = int(val)
            elif key in ("args", "env"):
                val = json.dumps(val)
            sets.append(f"{key} = ?")
            vals.append(val)
    if not sets:
        conn.close()
        return
    vals.append(now)
    vals.append(name)
    c.execute(f"UPDATE mcp_servers SET {', '.join(sets)}, updated_at = ? WHERE name = ?", vals)
    conn.commit()
    conn.close()


def delete_mcp_server(name: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM mcp_servers WHERE name = ?", (name,))
    conn.commit()
    conn.close()