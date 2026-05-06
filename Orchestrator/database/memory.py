from typing import Dict, List

from database.sqlite_backend import get_db


def get_agent_memory_blocks(agent_id: str, repo_name: str = None) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    if repo_name:
        c.execute("SELECT * FROM agent_memory_blocks WHERE agent_id = ? AND repo_name = ? ORDER BY label",
                  (agent_id, repo_name))
    else:
        c.execute("SELECT * FROM agent_memory_blocks WHERE agent_id = ? ORDER BY repo_name, label", (agent_id,))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_agent_memory_block(agent_id: str, repo_name: str, label: str, content: str,
                           description: str = "", block_limit: int = 5000, read_only: bool = False) -> int:
    conn = get_db()
    c = conn.cursor()
    from datetime import datetime
    now = datetime.now().isoformat()
    c.execute("""INSERT INTO agent_memory_blocks (agent_id, repo_name, label, content, description, block_limit, read_only, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, repo_name, label) DO UPDATE SET
                  content = excluded.content,
                  description = excluded.description,
                  block_limit = excluded.block_limit,
                  read_only = excluded.read_only,
                  updated_at = excluded.updated_at""",
              (agent_id, repo_name or "_global", label, content, description,
               block_limit, int(read_only), now))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_agent_memory_as_markdown(agent_id: str, repo_name: str) -> str:
    """Returns all memory blocks for an agent+repo as concatenated markdown with frontmatter."""
    blocks = get_agent_memory_blocks(agent_id, repo_name)
    if not blocks:
        blocks = get_agent_memory_blocks(agent_id, "_global")
    parts = []
    for b in blocks:
        parts.append(f"""---
label: {b['label']}
description: {b['description']}
limit: {b['block_limit']}
read_only: {bool(b['read_only'])}
---
{b['content']}""")
    return "\n\n".join(parts)


def delete_agent_memory_block(block_id: int):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_memory_blocks WHERE id = ?", (block_id,))
    conn.commit()
    conn.close()


def seed_default_memory_blocks(agent_id: str):
    """Seed default memory blocks for a new agent."""
    defaults = [
        ("_global", "persona", "You are an autonomous software developer. Work carefully and methodically. Prefer English comments in code.", "Agent identity and behavior"),
        ("_global", "human", "Prefer English UI language. Use Conventional Commits. No emojis in commits.", "Operator preferences"),
        ("_global", "project", "Tech stack: Vue 3 + TypeScript frontend, Go backend. Tests are mandatory.", "Project conventions and architecture"),
    ]
    for repo_name, label, content, desc in defaults:
        set_agent_memory_block(agent_id, repo_name, label, content, desc)