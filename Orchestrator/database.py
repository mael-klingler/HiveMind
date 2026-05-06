"""
Database module – thin adapter that delegates to either PostgreSQL or SQLite
based on DATABASE_URL environment variable.

To use PostgreSQL (recommended for production):
  DATABASE_URL=postgresql://user:pass@host:5432/dbname

To use SQLite (for local dev / single-instance):
  DB_PATH=/path/to/orchestrator.db
  (or no env = defaults to /app/data/orchestrator.db)
"""
import os

USE_POSTGRES = os.getenv("DATABASE_URL", "").startswith("postgresql")

if USE_POSTGRES:
    from database_pg import *  # noqa: F401,F403
else:
    import json
    import sqlite3
    from datetime import datetime
    from pathlib import Path
    from typing import Any, Dict, List, Optional

    DB_PATH = Path(os.getenv("DB_PATH", "/app/data/orchestrator.db"))

    def get_db():
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def _add_column_if_not_exists(cursor, table: str, column: str, definition: str):
    """Adds a column if it does not already exist (for migrations)."""
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    conn = get_db()
    c = conn.cursor()

    # Tickets table (with MR + Review Lifecycle)
    c.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        labels TEXT,
        issue_type TEXT,
        priority TEXT,
        status TEXT DEFAULT 'queued',
        mr_status TEXT DEFAULT 'none',         -- none | open | merged | rejected
        mr_url TEXT,                           -- Link to merge request
        review_status TEXT DEFAULT 'pending',  -- pending | approved | changes_requested
        review_notes TEXT,                     -- Review feedback
        retry_count INTEGER DEFAULT 0,         -- Retries after rejection
        workspace_path TEXT,                   -- Path to workspace folder
        agent_id TEXT,                         -- Assigned agent
        created_at TEXT,
        updated_at TEXT
    )
    """)

    # Migrate old columns (for existing DBs)
    _add_column_if_not_exists(c, "tickets", "mr_status", "TEXT DEFAULT 'none'")
    _add_column_if_not_exists(c, "tickets", "mr_url", "TEXT")
    _add_column_if_not_exists(c, "tickets", "review_status", "TEXT DEFAULT 'pending'")
    _add_column_if_not_exists(c, "tickets", "review_notes", "TEXT")
    _add_column_if_not_exists(c, "tickets", "retry_count", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(c, "tickets", "workspace_path", "TEXT")
    _add_column_if_not_exists(c, "tickets", "agent_id", "TEXT")
    _add_column_if_not_exists(c, "tickets", "ai_planning", "TEXT")
    _add_column_if_not_exists(c, "tickets", "mr_pipeline_status", "TEXT DEFAULT 'unknown'")
    _add_column_if_not_exists(c, "tickets", "mr_last_note_id", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(c, "tickets", "mr_project_path", "TEXT")
    _add_column_if_not_exists(c, "tickets", "mr_iid", "INTEGER")
    _add_column_if_not_exists(c, "tickets", "mr_conflict_status", "TEXT DEFAULT 'none'")
    _add_column_if_not_exists(c, "tickets", "selected_repos", "TEXT")

    # Tickets table (with MR + Review Lifecycle)
    c.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        labels TEXT,
        issue_type TEXT,
        priority TEXT,
        status TEXT DEFAULT 'queued',
        mr_status TEXT DEFAULT 'none',
        mr_url TEXT,
        review_status TEXT DEFAULT 'pending',
        review_notes TEXT,
        retry_count INTEGER DEFAULT 0,
        workspace_path TEXT,
        agent_id TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    # Migrate columns (for existing DBs)
    _add_column_if_not_exists(c, "tickets", "mr_status", "TEXT DEFAULT 'none'")
    _add_column_if_not_exists(c, "tickets", "mr_url", "TEXT")
    _add_column_if_not_exists(c, "tickets", "review_status", "TEXT DEFAULT 'pending'")
    _add_column_if_not_exists(c, "tickets", "review_notes", "TEXT")
    _add_column_if_not_exists(c, "tickets", "retry_count", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(c, "tickets", "workspace_path", "TEXT")
    _add_column_if_not_exists(c, "tickets", "agent_id", "TEXT")
    _add_column_if_not_exists(c, "tickets", "ai_planning", "TEXT")
    _add_column_if_not_exists(c, "tickets", "selected_repos", "TEXT")

    # Agents table
    c.execute("""
    CREATE TABLE IF NOT EXISTS agents (
        id TEXT PRIMARY KEY,
        name TEXT,
        status TEXT DEFAULT 'idle',
        current_ticket_id TEXT,
        started_at TEXT,
        completed_at TEXT,
        progress INTEGER DEFAULT 0,
        logs TEXT,
        FOREIGN KEY (current_ticket_id) REFERENCES tickets(id)
    )
    """)

    # Queue table
    c.execute("""
    CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT,
        position INTEGER,
        assigned_agent_id TEXT,
        status TEXT DEFAULT 'waiting',
        created_at TEXT,
        started_at TEXT,
        completed_at TEXT,
        FOREIGN KEY (ticket_id) REFERENCES tickets(id),
        FOREIGN KEY (assigned_agent_id) REFERENCES agents(id)
    )
    """)

    # Work steps per ticket/agent
    c.execute("""
    CREATE TABLE IF NOT EXISTS steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        queue_id INTEGER,
        ticket_id TEXT,
        agent_id TEXT,
        step_name TEXT,
        status TEXT DEFAULT 'pending',
        detail TEXT,
        timestamp TEXT,
        FOREIGN KEY (queue_id) REFERENCES queue(id),
        FOREIGN KEY (ticket_id) REFERENCES tickets(id)
    )
    """)

    # Configuration
    c.execute("""
    CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # Insert defaults
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('max_agents', '3')")
    c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('polling_interval_seconds', '5')")

    # MCP Servers table
    c.execute("""
    CREATE TABLE IF NOT EXISTS mcp_servers (
        name TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 1,
        server_type TEXT DEFAULT 'local',
        command TEXT,
        args TEXT,
        env TEXT,
        description TEXT,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    c.execute("INSERT OR IGNORE INTO mcp_servers (name, server_type, command, description) VALUES ('leankg-mcp', 'local', 'leankg mcp-stdio', 'LeanKG code search and dependency analysis')")

    # Agent Instructions table
    c.execute("""
    CREATE TABLE IF NOT EXISTS agent_instructions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        sort_order INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    _add_column_if_not_exists(c, "mcp_servers", "args", "TEXT")
    _add_column_if_not_exists(c, "mcp_servers", "env", "TEXT")

    # Agent Skills (MCP server assignments)
    c.execute("""
    CREATE TABLE IF NOT EXISTS agent_skills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        mcp_server_name TEXT NOT NULL,
        FOREIGN KEY (agent_id) REFERENCES agents(id),
        FOREIGN KEY (mcp_server_name) REFERENCES mcp_servers(name),
        UNIQUE(agent_id, mcp_server_name)
    )
    """)

    # Agent Instruction Assignments
    c.execute("""
    CREATE TABLE IF NOT EXISTS agent_instruction_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        instruction_id INTEGER NOT NULL,
        FOREIGN KEY (agent_id) REFERENCES agents(id),
        FOREIGN KEY (instruction_id) REFERENCES agent_instructions(id),
        UNIQUE(agent_id, instruction_id)
    )
    """)

    _add_column_if_not_exists(c, "agents", "model_name", "TEXT")

    # Ticket Comments
    c.execute("""
    CREATE TABLE IF NOT EXISTS ticket_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id TEXT NOT NULL,
        author TEXT DEFAULT 'system',
        comment_type TEXT DEFAULT 'comment',
        content TEXT NOT NULL,
        created_at TEXT,
        FOREIGN KEY (ticket_id) REFERENCES tickets(id)
    )
    """)

    # Agent Repo Affinities (Agent → Repo assignments)
    c.execute("""
    CREATE TABLE IF NOT EXISTS agent_repo_affinities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        repo_name TEXT NOT NULL,
        affinity INTEGER DEFAULT 1,
        FOREIGN KEY (agent_id) REFERENCES agents(id),
        UNIQUE(agent_id, repo_name)
    )
    """)

    # OpenCode Plugins
    c.execute("""
    CREATE TABLE IF NOT EXISTS opencode_plugins (
        name TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 1,
        description TEXT,
        requires_binary TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT
    )
    """)
    c.execute("INSERT OR IGNORE INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-snip', 'Compress shell output (60-97% token savings)', 'snip')")
    c.execute("INSERT OR IGNORE INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-agent-memory', 'Persistent memory blocks for agents (Letta-inspired)', '')")
    c.execute("INSERT OR IGNORE INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-handoff', 'Session handoff for contextual transitions on retries', '')")

    # Agent Memory Blocks (DB-persisted)
    c.execute("""
    CREATE TABLE IF NOT EXISTS agent_memory_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        repo_name TEXT NOT NULL DEFAULT '_global',
        label TEXT NOT NULL,
        content TEXT NOT NULL,
        description TEXT DEFAULT '',
        block_limit INTEGER DEFAULT 5000,
        read_only INTEGER DEFAULT 0,
        updated_at TEXT,
        FOREIGN KEY (agent_id) REFERENCES agents(id),
        UNIQUE(agent_id, repo_name, label)
    )
    """)

    # Repos table
    c.execute("""
    CREATE TABLE IF NOT EXISTS repos (
        name TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        branch TEXT DEFAULT 'development',
        description TEXT DEFAULT '',
        tags TEXT DEFAULT '[]',
        active INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
    )
    """)

    _add_column_if_not_exists(c, "repos", "active", "INTEGER DEFAULT 0")
    c.execute("UPDATE repos SET active = 1 WHERE active = 0 AND name IN (SELECT name FROM repos)")

    c.execute("""
    CREATE TABLE IF NOT EXISTS ticket_groups (
        id TEXT PRIMARY KEY,
        parent_ticket_id TEXT NOT NULL,
        title TEXT,
        description TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_ticket_id) REFERENCES tickets(id)
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS team_channel_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id TEXT NOT NULL,
        sender_agent_id TEXT NOT NULL,
        message_type TEXT DEFAULT 'info',
        content TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (group_id) REFERENCES ticket_groups(id)
    )
    """)

    conn.commit()
    conn.close()


# ── Repos ────────────────────────────────────────────────

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


# ── Tickets ──────────────────────────────────────────────

def create_ticket(ticket: Dict[str, Any]) -> str:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    selected_repos = ticket.get("selected_repos", [])
    selected_repos_json = json.dumps(selected_repos) if selected_repos else None
    c.execute("""
    INSERT INTO tickets (id, title, description, labels, issue_type, priority, status, created_at, updated_at, selected_repos)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticket["id"], ticket["title"], ticket.get("description", ""),
        json.dumps(ticket.get("labels", [])), ticket.get("issue_type", "Task"),
        ticket.get("priority", "Medium"), "queued", now, now,
        selected_repos_json
    ))
    conn.commit()

    # Insert into queue
    c.execute("SELECT COUNT(*) FROM queue WHERE status IN ('waiting', 'running', 'queued')")
    pos = c.fetchone()[0] + 1
    c.execute("""
    INSERT INTO queue (ticket_id, position, status, created_at)
    VALUES (?, ?, ?, ?)
    """, (ticket["id"], pos, "waiting", now))
    conn.commit()
    conn.close()
    return ticket["id"]


def get_tickets(status: Optional[str] = None) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    if status:
        c.execute("SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC", (status,))
    else:
        c.execute("SELECT * FROM tickets ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket(ticket_id: str) -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def update_ticket_status(ticket_id: str, status: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
              (status, datetime.now().isoformat(), ticket_id))
    conn.commit()
    if status not in ("running", "queued"):
        c.execute("SELECT agent_id FROM tickets WHERE id = ?", (ticket_id,))
        row = c.fetchone()
        if row and row["agent_id"]:
            c.execute("UPDATE agents SET status = 'idle', current_ticket_id = NULL, progress = 0 WHERE id = ?", (row["agent_id"],))
            conn.commit()
    conn.close()


def stop_ticket(ticket_id: str) -> bool:
    """Stop a running/queued ticket: cancel queue entry, set status to 'stopped'."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, status FROM queue WHERE ticket_id = ? AND status IN ('waiting', 'running', 'queued')", (ticket_id,))
    queue_row = c.fetchone()
    if queue_row:
        c.execute("UPDATE queue SET status = 'completed', completed_at = ? WHERE id = ?", (datetime.now().isoformat(), queue_row["id"]))
    c.execute("UPDATE tickets SET status = 'stopped', updated_at = ? WHERE id = ? AND status NOT IN ('completed', 'stopped')",
              (datetime.now().isoformat(), ticket_id))
    affected = c.rowcount
    # Reset assigned agent
    c.execute("UPDATE agents SET status = 'idle', current_ticket_id = NULL, progress = 0 WHERE current_ticket_id = ?", (ticket_id,))
    conn.commit()
    conn.close()
    return affected > 0


# ── Review / MR Lifecycle ──────────────────────────────────

def get_tickets_with_queue() -> List[Dict]:
    """All tickets with current queue status."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT t.*, q.status as queue_status, q.assigned_agent_id
    FROM tickets t
    LEFT JOIN queue q ON t.id = q.ticket_id AND q.status != 'completed'
    ORDER BY t.updated_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ticket_review(ticket_id: str, review_status: str, notes: str = "", mr_url: str = ""):
    """Save review result: approved or changes_requested."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    if review_status == "approved":
        c.execute("""
        UPDATE tickets SET status = 'merged', mr_status = 'merged', review_status = 'approved',
        review_notes = ?, mr_url = ?, updated_at = ? WHERE id = ?
        """, (notes, mr_url, now, ticket_id))
    elif review_status == "changes_requested":
        # Re-enqueue ticket
        c.execute("""
        UPDATE tickets SET status = 'queued', review_status = 'changes_requested',
        review_notes = ?, retry_count = retry_count + 1, mr_url = ?, updated_at = ? WHERE id = ?
        """, (notes, mr_url, now, ticket_id))
        # Create new queue entry
        c.execute("""
        INSERT INTO queue (ticket_id, position, status, created_at)
        VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', ?)
        """, (ticket_id, now))
    else:
        c.execute("""
        UPDATE tickets SET review_status = ?, review_notes = ?, mr_url = ?, updated_at = ? WHERE id = ?
        """, (review_status, notes, mr_url, now, ticket_id))

    conn.commit()
    conn.close()


def set_ticket_mr_url(ticket_id: str, mr_url: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET mr_url = ?, mr_status = 'open', updated_at = ? WHERE id = ?",
              (mr_url, datetime.now().isoformat(), ticket_id))
    conn.commit()
    conn.close()


def set_ticket_workspace(ticket_id: str, workspace_path: str, agent_id: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET workspace_path = ?, agent_id = ? WHERE id = ?",
              (workspace_path, agent_id, ticket_id))
    conn.commit()
    conn.close()


def set_ticket_ai_planning(ticket_id: str, planning: Dict):
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE tickets SET ai_planning = ?, updated_at = ? WHERE id = ?",
              (json.dumps(planning, ensure_ascii=False), datetime.now().isoformat(), ticket_id))
    conn.commit()
    conn.close()


# ── Agents ───────────────────────────────────────────────

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

    for i in range(3):
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


# ── Queue ──────────────────────────────────────────────

def get_next_queue_item() -> Optional[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT * FROM queue
    WHERE status = 'waiting'
    ORDER BY position ASC, id ASC
    LIMIT 1
    """)
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def assign_queue_item(queue_id: int, agent_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'running', assigned_agent_id = ?, started_at = ? WHERE id = ?",
              (agent_id, now, queue_id))
    conn.commit()
    conn.close()
    return c.rowcount > 0


def complete_queue_item(queue_id: int):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'completed', completed_at = ? WHERE id = ?", (now, queue_id))
    conn.commit()
    conn.close()


def fail_queue_item(queue_id: int, error: str):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("UPDATE queue SET status = 'failed', completed_at = ? WHERE id = ?", (now, queue_id))
    c.execute("INSERT INTO steps (queue_id, step_name, status, detail, timestamp) VALUES (?, ?, ?, ?, ?)",
              (queue_id, "Error", "error", error, now))
    conn.commit()
    conn.close()


def get_queue() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT q.*, t.title, a.name as agent_name
    FROM queue q
    LEFT JOIN tickets t ON q.ticket_id = t.id
    LEFT JOIN agents a ON q.assigned_agent_id = a.id
    ORDER BY
      CASE q.status
        WHEN 'running' THEN 1
        WHEN 'waiting' THEN 2
        WHEN 'completed' THEN 3
        WHEN 'failed' THEN 4
      END,
      q.position ASC
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Steps ──────────────────────────────────────────────

def add_step(queue_id: int, ticket_id: str, agent_id: str, step_name: str, status: str = "running", detail: str = ""):
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""
    INSERT INTO steps (queue_id, ticket_id, agent_id, step_name, status, detail, timestamp)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (queue_id, ticket_id, agent_id, step_name, status, detail, now))
    conn.commit()
    conn.close()


def get_steps(ticket_id: Optional[str] = None, agent_id: Optional[str] = None, queue_id: Optional[int] = None) -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    conditions = []
    params = []

    if ticket_id:
        conditions.append("ticket_id = ?")
        params.append(ticket_id)
    if agent_id:
        conditions.append("agent_id = ?")
        params.append(agent_id)
    if queue_id:
        conditions.append("queue_id = ?")
        params.append(queue_id)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    c.execute(f"SELECT * FROM steps {where} ORDER BY timestamp DESC", params)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_steps() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT s.*, q.ticket_id, t.title
    FROM steps s
    LEFT JOIN queue q ON s.queue_id = q.id
    LEFT JOIN tickets t ON s.ticket_id = t.id
    ORDER BY s.timestamp DESC
    LIMIT 200
    """)
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Settings ────────────────────────────────────────────

def ensure_config_defaults():
    defaults = {
        "max_agents": os.getenv("MAX_AGENTS", "3"),
        "polling_interval_seconds": os.getenv("POLLING_INTERVAL_SECONDS", "5"),
        "git_host": os.getenv("GITLAB_HOST") or "",
        "git_user": os.getenv("GIT_USER", "gitlab-ci-token"),
        "git_token": os.getenv("GITLAB_TOKEN", ""),
        "ollama_host": os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        "ollama_model": os.getenv("OLLAMA_MODEL", "glm-5.1:cloud"),
        "opencode_model": os.getenv("OPENCODE_MODEL", "glm-5.1:cloud"),
        "auto_pull_enabled": "true",
        "default_branch": "development",
        "branch_fallback_order": "development,qa,main",
    }
    conn = get_db()
    c = conn.cursor()
    for key, value in defaults.items():
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def get_all_settings() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT key, value FROM config ORDER BY key")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_setting(key: str) -> Optional[str]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT value FROM config WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def import_settings_from_env():
    """Overrides settings from environment variables."""
    env_mapping = {
        "GIT_HOST": "git_host",
        "GIT_USER": "git_user",
        "GIT_TOKEN": "git_token",
        "OLLAMA_HOST": "ollama_host",
        "OLLAMA_MODEL": "ollama_model",
        "TRACK_BRANCH": "default_branch",
        "BRANCH_FALLBACK_ORDER": "branch_fallback_order",
    }
    for env_key, db_key in env_mapping.items():
        val = os.getenv(env_key)
        if val:
            set_setting(db_key, val)


def requeue_ticket(ticket_id: str, max_retries: int = 3) -> bool:
    """Puts a failed ticket back into the queue if retry_count < max_retries."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    c.execute("SELECT retry_count, status FROM tickets WHERE id = ?", (ticket_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    retry_count = row["retry_count"]
    if retry_count >= max_retries:
        conn.close()
        return False

    c.execute("""
    UPDATE tickets SET status = 'queued', retry_count = retry_count + 1, updated_at = ?
    WHERE id = ?
    """, (now, ticket_id))

    c.execute("""
    INSERT INTO queue (ticket_id, position, status, created_at)
    VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', ?)
    """, (ticket_id, now))

    conn.commit()
    conn.close()
    return True


def reopen_ticket(ticket_id: str) -> bool:
    """Manually reopens a completed/failed ticket. Does NOT increment retry_count."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()

    c.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return False

    c.execute("""
    UPDATE tickets SET status = 'queued', mr_status = 'none', review_status = 'pending',
    mr_conflict_status = 'none', updated_at = ?
    WHERE id = ?
    """, (now, ticket_id))

    c.execute("""
    INSERT INTO queue (ticket_id, position, status, created_at)
    VALUES (?, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', ?)
    """, (ticket_id, now))

    conn.commit()
    conn.close()
    return True


def get_failed_tickets() -> List[Dict]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE status = 'failed'")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_open_mr_tickets() -> List[Dict]:
    """Tickets with an open MR (for monitoring pipelines and comments)."""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE mr_status = 'open' OR mr_url IS NOT NULL")
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_ticket_mr_tracking(ticket_id: str, pipeline_status: str = None, last_note_id: int = None, project_path: str = None, mr_iid: int = None, conflict_status: str = None):
    conn = get_db()
    c = conn.cursor()
    updates = []
    params = []
    if pipeline_status is not None:
        updates.append("mr_pipeline_status = ?")
        params.append(pipeline_status)
    if last_note_id is not None:
        updates.append("mr_last_note_id = ?")
        params.append(last_note_id)
    if project_path is not None:
        updates.append("mr_project_path = ?")
        params.append(project_path)
    if mr_iid is not None:
        updates.append("mr_iid = ?")
        params.append(mr_iid)
    if conflict_status is not None:
        updates.append("mr_conflict_status = ?")
        params.append(conflict_status)
    if not updates:
        conn.close()
        return
    params.append(ticket_id)
    c.execute(f"UPDATE tickets SET {', '.join(updates)}, updated_at = ? WHERE id = ?",
              params[:-1] + [datetime.now().isoformat(), ticket_id])
    conn.commit()
    conn.close()


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


# ── Agent CRUD with Skills ──────────────────────────────────

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


# ── Ticket Comments ──────────────────────────────────────────

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


# ── Agent Repo Affinities ────────────────────────────────────

def set_agent_repo_affinities(agent_id: str, repo_names: List[str]):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM agent_repo_affinities WHERE agent_id = ?", (agent_id,))
    for rn in repo_names:
        c.execute("INSERT OR IGNORE INTO agent_repo_affinities (agent_id, repo_name, affinity) VALUES (?, ?, 1)", (agent_id, rn))
    conn.commit()
    conn.close()


def get_agent_repo_affinities(agent_id: str) -> List[str]:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT repo_name FROM agent_repo_affinities WHERE agent_id = ? ORDER BY repo_name", (agent_id,))
    rows = c.fetchall()
    conn.close()
    return [r["repo_name"] for r in rows]


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


# ── OpenCode Plugins ──────────────────────────────────────────

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


# ── Agent Memory Blocks (DB-persisted) ────────────────────────

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


# ── Ticket Groups & Team Channel ────────────────────────

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


# ── Init ──
init_db()
ensure_config_defaults()
import_settings_from_env()