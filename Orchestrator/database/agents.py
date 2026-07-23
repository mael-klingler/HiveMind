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

import json
from datetime import datetime
from typing import Dict, List, Optional

from database.sqlite_backend import get_db


def get_agent(agent_id: str) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_or_create_agent(agent_id: str, name: str = "") -> Dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
    row = c.fetchone()
    if row:
        conn.close()
        return dict(row)

    now = datetime.now().isoformat()
    c.execute("INSERT INTO agents (id, name, status, logs) VALUES (?, ?, ?, ?)",
              (agent_id, name or f"Agent-{agent_id[:8]}", "idle", json.dumps([])))
    conn.commit()
    conn.close()
    return get_agent(agent_id)


def set_agent_status(agent_id: str, status: str, ticket_id: Optional[str] = None, progress: int = 0):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    if status == "running":
        c.execute(
            "UPDATE agents SET status = ?, current_ticket_id = ?, started_at = ?, progress = ? WHERE id = ?",
            (status, ticket_id, now, progress, agent_id)
        )
    elif status == "idle":
        c.execute(
            "UPDATE agents SET status = ?, current_ticket_id = NULL, completed_at = ?, progress = ? WHERE id = ?",
            (status, now, progress, agent_id)
        )
    else:
        c.execute("UPDATE agents SET status = ?, progress = ? WHERE id = ?", (status, progress, agent_id))

    conn.commit()
    conn.close()


def get_all_agents() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM agents ORDER BY id")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_idle_agents() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM agents WHERE status = 'idle' ORDER BY id")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_max_agents() -> int:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = 'max_agents'")
    row = c.fetchone()
    conn.close()
    return int(row["value"]) if row else 3


def set_max_agents(max_agents: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('max_agents', ?)", (str(max_agents),))
    conn.commit()
    conn.close()


def ensure_agent_pool():
    """Ensures that default agents exist. max_agents = max concurrent running agents."""
    max_agents = get_max_agents()
    conn = get_db()
    c = conn.cursor()

    for i in range(max_agents):
        agent_id = f"agent-{i+1}"
        c.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        if not c.fetchone():
            c.execute(
                "INSERT INTO agents (id, name, status, logs) VALUES (?, ?, ?, ?)",
                (agent_id, f"Agent {i+1}", "idle", json.dumps([]))
            )

    # Re-enable disabled agents (non-running)
    c.execute("UPDATE agents SET status = 'idle' WHERE status = 'disabled'")

    conn.commit()
    conn.close()


def create_agent(agent_id: str, name: str, model_name: str = "", skill_names: List[str] = None, instruction_ids: List[int] = None, repo_affinities: List[str] = None) -> Dict:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("INSERT OR IGNORE INTO agents (id, name, status, logs, model_name) VALUES (?, ?, 'idle', ?, ?)",
              (agent_id, name or f"Agent-{agent_id}", json.dumps([]), model_name))
    conn.commit()
    if skill_names:
        for sn in skill_names:
            c.execute("INSERT OR IGNORE INTO agent_skills (agent_id, mcp_server_name) VALUES (?, ?)", (agent_id, sn))
        conn.commit()
    if instruction_ids:
        for iid in instruction_ids:
            c.execute("INSERT OR IGNORE INTO agent_instruction_assignments (agent_id, instruction_id) VALUES (?, ?)", (agent_id, iid))
        conn.commit()
    if repo_affinities:
        for rn in repo_affinities:
            c.execute("INSERT OR IGNORE INTO agent_repo_affinities (agent_id, repo_name, affinity) VALUES (?, ?, 1)", (agent_id, rn))
        conn.commit()
    conn.close()
    return get_agent_with_profile(agent_id)


def update_agent_profile(agent_id: str, name: str = None, model_name: str = None) -> Dict:
    conn = get_db()
    c = conn.cursor()
    sets, vals = [], []
    if name is not None:
        sets.append("name = ?"); vals.append(name)
    if model_name is not None:
        sets.append("model_name = ?"); vals.append(model_name)
    if sets:
        vals.append(agent_id)
        c.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()
    conn.close()
    return get_agent_with_profile(agent_id)


def delete_agent(agent_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_skills WHERE agent_id = ?", (agent_id,))
    c.execute("DELETE FROM agent_instruction_assignments WHERE agent_id = ?", (agent_id,))
    c.execute("DELETE FROM agent_repo_affinities WHERE agent_id = ?", (agent_id,))
    c.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    conn.commit()
    conn.close()


def set_agent_skills(agent_id: str, skill_names: List[str]):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_skills WHERE agent_id = ?", (agent_id,))
    for sn in skill_names:
        c.execute("INSERT OR IGNORE INTO agent_skills (agent_id, mcp_server_name) VALUES (?, ?)", (agent_id, sn))
    conn.commit()
    conn.close()


def set_agent_instruction_assignments(agent_id: str, instruction_ids: List[int]):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_instruction_assignments WHERE agent_id = ?", (agent_id,))
    for iid in instruction_ids:
        c.execute("INSERT OR IGNORE INTO agent_instruction_assignments (agent_id, instruction_id) VALUES (?, ?)", (agent_id, iid))
    conn.commit()
    conn.close()


def get_agent_with_profile(agent_id: str) -> Dict:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    agent = dict(row)
    c.execute("SELECT mcp_server_name FROM agent_skills WHERE agent_id = ?", (agent_id,))
    agent["skills"] = [r["mcp_server_name"] for r in c.fetchall()]
    c.execute("SELECT instruction_id FROM agent_instruction_assignments WHERE agent_id = ?", (agent_id,))
    agent["instruction_ids"] = [r["instruction_id"] for r in c.fetchall()]
    c.execute("SELECT repo_name FROM agent_repo_affinities WHERE agent_id = ? ORDER BY repo_name", (agent_id,))
    agent["repo_affinities"] = [r["repo_name"] for r in c.fetchall()]
    conn.close()
    return agent


def get_all_agents_with_profiles() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM agents ORDER BY id")
    agent_ids = [r["id"] for r in c.fetchall()]
    conn.close()
    return [get_agent_with_profile(aid) for aid in agent_ids]


def get_agent_mcp_servers(agent_id: str) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT ms.* FROM mcp_servers ms
                 JOIN agent_skills ask ON ms.name = ask.mcp_server_name
                 WHERE ask.agent_id = ? AND ms.enabled = 1""", (agent_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_agent_assigned_instructions(agent_id: str) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT ai.* FROM agent_instructions ai
                 JOIN agent_instruction_assignments aia ON ai.id = aia.instruction_id
                 WHERE aia.agent_id = ? AND ai.enabled = 1
                 ORDER BY ai.sort_order""", (agent_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]