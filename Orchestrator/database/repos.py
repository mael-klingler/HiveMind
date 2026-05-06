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

import json
from datetime import datetime
from typing import Dict, List, Optional

from database.sqlite_backend import get_db
from database.settings import get_setting


def get_all_repos(active_only: bool = False) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    if active_only:
        c.execute("SELECT name, url, branch, description, tags, active FROM repos WHERE active = 1 ORDER BY name")
    else:
        c.execute("SELECT name, url, branch, description, tags, active FROM repos ORDER BY name")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        tags = r["tags"]
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except (json.JSONDecodeError, TypeError):
                tags = []
        result.append({
            "name": r["name"],
            "url": r["url"],
            "branch": r["branch"],
            "description": r["description"],
            "tags": tags,
            "active": bool(r["active"]),
        })
    return result


def get_repo(name: str) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name, url, branch, description, tags, active FROM repos WHERE name = ?", (name,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    tags = row["tags"]
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    return {
        "name": row["name"],
        "url": row["url"],
        "branch": row["branch"],
        "description": row["description"],
        "tags": tags,
        "active": bool(row["active"]),
    }


def update_repo(name: str, **fields) -> bool:
    conn = get_db()
    c = conn.cursor()
    allowed = {"url", "branch", "description", "tags", "active"}
    updates = {}
    for k, v in fields.items():
        if k in allowed:
            if k == "tags":
                v = json.dumps(v if v else [])
            updates[k] = v
    if not updates:
        conn.close()
        return False
    now = datetime.now().isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    c.execute(f"UPDATE repos SET {set_clause} WHERE name = ?", (*updates.values(), name))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def add_repo(name: str, url: str, branch: str = "", description: str = "", tags: list = None, active: int = 0) -> bool:
    if not branch:
        branch = get_setting("default_branch") or "development"
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    try:
        c.execute(
            "INSERT INTO repos (name, url, branch, description, tags, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, url, branch, description, json.dumps(tags or []), active, now, now),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def delete_repo(name: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM repos WHERE name = ?", (name,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def set_repo_active(name: str, active: bool) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE repos SET active = ?, updated_at = ? WHERE name = ?", (1 if active else 0, datetime.now().isoformat(), name))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def import_repos_from_config(config_path: str):
    existing = get_all_repos()
    existing_names = {r["name"] for r in existing}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    for repo in data.get("repositories", []):
        name = repo.get("name", "")
        if not name or name in existing_names:
            continue
        add_repo(
            name=name,
            url=repo.get("url", ""),
            branch=repo.get("branch", "development"),
            description=repo.get("description", ""),
            tags=repo.get("tags", []),
            active=1
        )


def get_agent_repo_affinities(agent_id: str) -> List[str]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT repo_name FROM agent_repo_affinities WHERE agent_id = ? ORDER BY repo_name", (agent_id,))
    rows = c.fetchall()
    conn.close()
    return [r["repo_name"] for r in rows]


def set_agent_repo_affinities(agent_id: str, repo_names: List[str]):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_repo_affinities WHERE agent_id = ?", (agent_id,))
    for rn in repo_names:
        c.execute("INSERT OR IGNORE INTO agent_repo_affinities (agent_id, repo_name, affinity) VALUES (?, ?, 1)", (agent_id, rn))
    conn.commit()
    conn.close()


def get_all_repo_names() -> List[str]:
    repos = get_all_repos()
    return [r["name"] for r in repos]


def find_best_agent_for_repo(primary_repo: str, idle_agents: List[Dict]) -> str:
    best_agent = None
    best_score = -1
    conn = get_db()
    c = conn.cursor()
    for agent in idle_agents:
        c.execute("SELECT COUNT(*) as cnt FROM agent_repo_affinities WHERE agent_id = ? AND repo_name = ?",
                  (agent["id"], primary_repo))
        row = c.fetchone()
        score = row["cnt"] if row else 0
        if score > best_score:
            best_score = score
            best_agent = agent["id"]
    conn.close()
    return best_agent