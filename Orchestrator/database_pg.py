#!/usr/bin/env python3
"""
Database layer – PostgreSQL-backed.
Replaces SQLite with psycopg2 for better concurrency in parallel queue processors.
"""

import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger("hivemind.db")

DB_URL = os.getenv("DATABASE_URL", "postgresql://hivemind:hivemind@localhost:5432/hivemind")


def get_db():
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    return conn


def _row_to_dict(row) -> Optional[Dict]:
    if row is None:
        return None
    if hasattr(row, '_asdict'):
        return dict(row._asdict())
    if isinstance(row, dict):
        return row
    return dict(row)


def _ensure_json(val):
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return val


def _parse_json(val):
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def init_db():
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            labels TEXT,
            issue_type TEXT DEFAULT 'Task',
            priority TEXT DEFAULT 'Medium',
            status TEXT DEFAULT 'queued',
            mr_status TEXT DEFAULT 'none',
            mr_url TEXT,
            review_status TEXT DEFAULT 'pending',
            review_notes TEXT,
            retry_count INTEGER DEFAULT 0,
            workspace_path TEXT,
            agent_id TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            ai_planning TEXT,
            mr_pipeline_status TEXT DEFAULT 'unknown',
            mr_last_note_id INTEGER DEFAULT 0,
            mr_project_path TEXT,
            mr_iid INTEGER,
            mr_conflict_status TEXT DEFAULT 'none',
            selected_repos TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT DEFAULT 'idle',
            current_ticket_id TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            progress INTEGER DEFAULT 0,
            logs TEXT,
            model_name TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id SERIAL PRIMARY KEY,
            ticket_id TEXT REFERENCES tickets(id),
            position INTEGER,
            assigned_agent_id TEXT REFERENCES agents(id),
            status TEXT DEFAULT 'waiting',
            created_at TIMESTAMP DEFAULT NOW(),
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS steps (
            id SERIAL PRIMARY KEY,
            queue_id INTEGER REFERENCES queue(id),
            ticket_id TEXT REFERENCES tickets(id),
            agent_id TEXT,
            step_name TEXT,
            status TEXT DEFAULT 'pending',
            detail TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS mcp_servers (
            name TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            server_type TEXT DEFAULT 'local',
            command TEXT,
            args TEXT,
            env TEXT,
            description TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_instructions (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_skills (
            id SERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(id),
            mcp_server_name TEXT NOT NULL REFERENCES mcp_servers(name),
            UNIQUE(agent_id, mcp_server_name)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_instruction_assignments (
            id SERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(id),
            instruction_id INTEGER NOT NULL REFERENCES agent_instructions(id),
            UNIQUE(agent_id, instruction_id)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS ticket_comments (
            id SERIAL PRIMARY KEY,
            ticket_id TEXT NOT NULL REFERENCES tickets(id),
            author TEXT DEFAULT 'system',
            comment_type TEXT DEFAULT 'comment',
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_repo_affinities (
            id SERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(id),
            repo_name TEXT NOT NULL,
            affinity INTEGER DEFAULT 1,
            UNIQUE(agent_id, repo_name)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS opencode_plugins (
            name TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            description TEXT,
            requires_binary TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS agent_memory_blocks (
            id SERIAL PRIMARY KEY,
            agent_id TEXT NOT NULL REFERENCES agents(id),
            repo_name TEXT NOT NULL DEFAULT '_global',
            label TEXT NOT NULL,
            content TEXT NOT NULL,
            description TEXT DEFAULT '',
            block_limit INTEGER DEFAULT 5000,
            read_only INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(agent_id, repo_name, label)
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS repos (
            name TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            branch TEXT DEFAULT 'development',
            description TEXT DEFAULT '',
            tags TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS ticket_groups (
            id TEXT PRIMARY KEY,
            parent_ticket_id TEXT NOT NULL REFERENCES tickets(id),
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        c.execute("""
        CREATE TABLE IF NOT EXISTS team_channel_messages (
            id SERIAL PRIMARY KEY,
            group_id TEXT NOT NULL REFERENCES ticket_groups(id),
            sender_agent_id TEXT NOT NULL,
            message_type TEXT DEFAULT 'info',
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        c.execute("INSERT INTO config (key, value) VALUES ('max_agents', '3') ON CONFLICT (key) DO NOTHING")
        c.execute("INSERT INTO config (key, value) VALUES ('polling_interval_seconds', '5') ON CONFLICT (key) DO NOTHING")
        c.execute("INSERT INTO mcp_servers (name, server_type, command, description) VALUES ('leankg-mcp', 'local', 'leankg mcp-stdio', 'LeanKG code search and dependency analysis') ON CONFLICT (name) DO NOTHING")
        c.execute("INSERT INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-snip', 'Compress shell output (60-97%% token savings)', 'snip') ON CONFLICT (name) DO NOTHING")
        c.execute("INSERT INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-agent-memory', 'Persistent memory blocks for agents (Letta-inspired)', '') ON CONFLICT (name) DO NOTHING")
        c.execute("INSERT INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-handoff', 'Session handoff for contextual transitions on retries', '') ON CONFLICT (name) DO NOTHING")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Repos ────────────────────────────────────────────────

def get_all_repos() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT name, url, branch, description, tags FROM repos ORDER BY name")
            rows = c.fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["tags"] = _parse_json(d["tags"]) or []
                result.append(d)
            return result
    finally:
        conn.close()


def get_repo(name: str) -> Optional[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT name, url, branch, description, tags FROM repos WHERE name = %s", (name,))
            row = c.fetchone()
            if not row:
                return None
            d = dict(row)
            d["tags"] = _parse_json(d["tags"]) or []
            return d
    finally:
        conn.close()


def update_repo(name: str, **fields) -> bool:
    allowed = {"url", "branch", "description", "tags"}
    updates = {}
    for k, v in fields.items():
        if k in allowed:
            if k == "tags":
                v = json.dumps(v if v else [])
            updates[k] = v
    if not updates:
        return False
    updates["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = %s" for k in updates)
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute(f"UPDATE repos SET {set_clause} WHERE name = %s", (*updates.values(), name))
            ok = c.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def add_repo(name: str, url: str, branch: str = "", description: str = "", tags: list = None) -> bool:
    if not branch:
        branch = get_setting("default_branch") or "development"
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute(
                "INSERT INTO repos (name, url, branch, description, tags, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())",
                (name, url, branch, description, json.dumps(tags or [])),
            )
        conn.commit()
        return True
    except psycopg2.IntegrityError:
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_repo(name: str) -> bool:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM repos WHERE name = %s", (name,))
            deleted = c.rowcount > 0
        conn.commit()
        return deleted
    finally:
        conn.close()


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
        )


# ── Tickets ──────────────────────────────────────────────

def create_ticket(ticket: Dict[str, Any]) -> str:
    conn = get_db()
    try:
        with conn.cursor() as c:
            now = datetime.now().isoformat()
            selected_repos = _ensure_json(ticket.get("selected_repos", []))
            c.execute("""
            INSERT INTO tickets (id, title, description, labels, issue_type, priority, status, created_at, updated_at, selected_repos)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                ticket["id"], ticket["title"], ticket.get("description", ""),
                json.dumps(ticket.get("labels", [])), ticket.get("issue_type", "Task"),
                ticket.get("priority", "Medium"), "queued", now, now,
                selected_repos,
            ))
            c.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM queue")
            pos = c.fetchone()[0]
            c.execute("""
            INSERT INTO queue (ticket_id, position, status, created_at)
            VALUES (%s, %s, %s, %s)
            """, (ticket["id"], pos, "waiting", now))
        conn.commit()
        return ticket["id"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_tickets(status: Optional[str] = None) -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            if status:
                c.execute("SELECT * FROM tickets WHERE status = %s ORDER BY created_at DESC", (status,))
            else:
                c.execute("SELECT * FROM tickets ORDER BY created_at DESC")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_ticket(ticket_id: str) -> Optional[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
            row = c.fetchone()
            return _row_to_dict(row) if row else None
    finally:
        conn.close()


def update_ticket_status(ticket_id: str, status: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE tickets SET status = %s, updated_at = NOW() WHERE id = %s", (status, ticket_id))
            if status not in ("running", "queued"):
                c.execute("SELECT agent_id FROM tickets WHERE id = %s", (ticket_id,))
                row = c.fetchone()
                if row and row[0]:
                    c.execute("UPDATE agents SET status = 'idle', current_ticket_id = NULL, progress = 0 WHERE id = %s", (row[0],))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def stop_ticket(ticket_id: str) -> bool:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id, status FROM queue WHERE ticket_id = %s AND status IN ('waiting', 'running', 'queued')", (ticket_id,))
            queue_row = c.fetchone()
            if queue_row:
                c.execute("UPDATE queue SET status = 'completed', completed_at = NOW() WHERE id = %s", (queue_row[0],))
            c.execute("UPDATE tickets SET status = 'stopped', updated_at = NOW() WHERE id = %s AND status NOT IN ('completed', 'stopped')", (ticket_id,))
            affected = c.rowcount
            c.execute("UPDATE agents SET status = 'idle', current_ticket_id = NULL, progress = 0 WHERE current_ticket_id = %s", (ticket_id,))
        conn.commit()
        return affected > 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Review / MR Lifecycle ──────────────────────────────────

def get_tickets_with_queue() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""
            SELECT t.*, q.status as queue_status, q.assigned_agent_id
            FROM tickets t
            LEFT JOIN queue q ON t.id = q.ticket_id AND q.status != 'completed'
            ORDER BY t.updated_at DESC
            """)
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def update_ticket_review(ticket_id: str, review_status: str, notes: str = "", mr_url: str = ""):
    conn = get_db()
    try:
        with conn.cursor() as c:
            if review_status == "approved":
                c.execute("""
                UPDATE tickets SET status = 'merged', mr_status = 'merged', review_status = 'approved',
                review_notes = %s, mr_url = %s, updated_at = NOW() WHERE id = %s
                """, (notes, mr_url, ticket_id))
            elif review_status == "changes_requested":
                c.execute("""
                UPDATE tickets SET status = 'queued', review_status = 'changes_requested',
                review_notes = %s, retry_count = retry_count + 1, mr_url = %s, updated_at = NOW() WHERE id = %s
                """, (notes, mr_url, ticket_id))
                c.execute("""
                INSERT INTO queue (ticket_id, position, status, created_at)
                VALUES (%s, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', NOW())
                """, (ticket_id,))
            else:
                c.execute("""
                UPDATE tickets SET review_status = %s, review_notes = %s, mr_url = %s, updated_at = NOW() WHERE id = %s
                """, (review_status, notes, mr_url, ticket_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_ticket_mr_url(ticket_id: str, mr_url: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE tickets SET mr_url = %s, mr_status = 'open', updated_at = NOW() WHERE id = %s", (mr_url, ticket_id))
        conn.commit()
    finally:
        conn.close()


def set_ticket_workspace(ticket_id: str, workspace_path: str, agent_id: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE tickets SET workspace_path = %s, agent_id = %s WHERE id = %s", (workspace_path, agent_id, ticket_id))
        conn.commit()
    finally:
        conn.close()


def set_ticket_ai_planning(ticket_id: str, planning: Dict):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE tickets SET ai_planning = %s, updated_at = NOW() WHERE id = %s", (json.dumps(planning, ensure_ascii=False), ticket_id))
        conn.commit()
    finally:
        conn.close()


# ── Agents ───────────────────────────────────────────────

def get_agent(agent_id: str) -> Optional[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM agents WHERE id = %s", (agent_id,))
            row = c.fetchone()
            return _row_to_dict(row) if row else None
    finally:
        conn.close()


def get_or_create_agent(agent_id: str, name: str = "") -> Dict:
    existing = get_agent(agent_id)
    if existing:
        return existing
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO agents (id, name, status, logs) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                      (agent_id, name or f"Agent-{agent_id[:8]}", "idle", json.dumps([])))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()
    return get_agent(agent_id)


def set_agent_status(agent_id: str, status: str, ticket_id: Optional[str] = None, progress: int = 0):
    conn = get_db()
    try:
        with conn.cursor() as c:
            if status == "running":
                c.execute("UPDATE agents SET status = %s, current_ticket_id = %s, started_at = NOW(), progress = %s WHERE id = %s",
                          (status, ticket_id, progress, agent_id))
            elif status == "idle":
                c.execute("UPDATE agents SET status = %s, current_ticket_id = NULL, completed_at = NOW(), progress = %s WHERE id = %s",
                          (status, progress, agent_id))
            else:
                c.execute("UPDATE agents SET status = %s, progress = %s WHERE id = %s", (status, progress, agent_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_all_agents() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM agents ORDER BY id")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_idle_agents() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM agents WHERE status = 'idle' ORDER BY id")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_max_agents() -> int:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT value FROM config WHERE key = 'max_agents'")
            row = c.fetchone()
            return int(row[0]) if row else 3
    finally:
        conn.close()


def set_max_agents(max_agents: int):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO config (key, value) VALUES ('max_agents', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (str(max_agents),))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_agent_pool():
    max_agents = get_max_agents()
    conn = get_db()
    try:
        with conn.cursor() as c:
            for i in range(3):
                agent_id = f"agent-{i+1}"
                c.execute("INSERT INTO agents (id, name, status, logs) VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                          (agent_id, f"Agent {i+1}", "idle", json.dumps([])))
            c.execute("UPDATE agents SET status = 'idle' WHERE status = 'disabled'")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Queue ──────────────────────────────────────────────

def get_next_queue_item() -> Optional[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""
            SELECT * FROM queue
            WHERE status = 'waiting'
            ORDER BY position ASC, id ASC
            LIMIT 1
            """)
            row = c.fetchone()
            return _row_to_dict(row) if row else None
    finally:
        conn.close()


def assign_queue_item(queue_id: int, agent_id: str) -> bool:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE queue SET status = 'running', assigned_agent_id = %s, started_at = NOW() WHERE id = %s", (agent_id, queue_id))
            ok = c.rowcount > 0
        conn.commit()
        return ok
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def complete_queue_item(queue_id: int):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE queue SET status = 'completed', completed_at = NOW() WHERE id = %s", (queue_id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def fail_queue_item(queue_id: int, error: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("UPDATE queue SET status = 'failed', completed_at = NOW() WHERE id = %s", (queue_id,))
            c.execute("INSERT INTO steps (queue_id, ticket_id, agent_id, step_name, status, detail, timestamp) VALUES (%s, %s, %s, %s, %s, %s, NOW())",
                      (queue_id, None, None, "Error", "error", error))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_queue() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
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
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


# ── Steps ──────────────────────────────────────────────

def add_step(queue_id: int, ticket_id: str, agent_id: str, step_name: str, status: str = "running", detail: str = ""):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("""
            INSERT INTO steps (queue_id, ticket_id, agent_id, step_name, status, detail, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (queue_id, ticket_id, agent_id, step_name, status, detail))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_all_steps(ticket_id: Optional[str] = None, agent_id: Optional[str] = None, queue_id: Optional[int] = None) -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            conditions = []
            params = []
            if ticket_id:
                conditions.append("ticket_id = %s")
                params.append(ticket_id)
            if agent_id:
                conditions.append("agent_id = %s")
                params.append(agent_id)
            if queue_id:
                conditions.append("queue_id = %s")
                params.append(queue_id)
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            c.execute(f"SELECT * FROM steps {where} ORDER BY timestamp DESC", params)
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


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
    try:
        with conn.cursor() as c:
            for key, value in defaults.items():
                c.execute("INSERT INTO config (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING", (key, value))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_all_settings() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT key, value FROM config ORDER BY key")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_setting(key: str) -> Optional[str]:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT value FROM config WHERE key = %s", (key,))
            row = c.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO config (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def import_settings_from_env():
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
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT retry_count, status FROM tickets WHERE id = %s", (ticket_id,))
            row = c.fetchone()
            if not row:
                return False
            retry_count = row[0]
            if retry_count >= max_retries:
                return False
            c.execute("""
            UPDATE tickets SET status = 'queued', retry_count = retry_count + 1, updated_at = NOW()
            WHERE id = %s
            """, (ticket_id,))
            c.execute("""
            INSERT INTO queue (ticket_id, position, status, created_at)
            VALUES (%s, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', NOW())
            """, (ticket_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def reopen_ticket(ticket_id: str) -> bool:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT status FROM tickets WHERE id = %s", (ticket_id,))
            row = c.fetchone()
            if not row:
                return False
            c.execute("""
            UPDATE tickets SET status = 'queued', mr_status = 'none', review_status = 'pending',
            mr_conflict_status = 'none', updated_at = NOW()
            WHERE id = %s
            """, (ticket_id,))
            c.execute("""
            INSERT INTO queue (ticket_id, position, status, created_at)
            VALUES (%s, (SELECT COALESCE(MAX(position), 0) + 1 FROM queue), 'waiting', NOW())
            """, (ticket_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def get_failed_tickets() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM tickets WHERE status = 'failed'")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_open_mr_tickets() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM tickets WHERE mr_status = 'open' OR mr_url IS NOT NULL")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def update_ticket_mr_tracking(ticket_id: str, pipeline_status: str = None, last_note_id: int = None, project_path: str = None, mr_iid: int = None, conflict_status: str = None):
    conn = get_db()
    try:
        with conn.cursor() as c:
            updates = []
            params = []
            if pipeline_status is not None:
                updates.append("mr_pipeline_status = %s")
                params.append(pipeline_status)
            if last_note_id is not None:
                updates.append("mr_last_note_id = %s")
                params.append(last_note_id)
            if project_path is not None:
                updates.append("mr_project_path = %s")
                params.append(project_path)
            if mr_iid is not None:
                updates.append("mr_iid = %s")
                params.append(mr_iid)
            if conflict_status is not None:
                updates.append("mr_conflict_status = %s")
                params.append(conflict_status)
            if not updates:
                return
            updates.append("updated_at = NOW()")
            params.append(ticket_id)
            c.execute(f"UPDATE tickets SET {', '.join(updates)} WHERE id = %s", params)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_mcp_servers() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM mcp_servers ORDER BY name")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_enabled_mcp_servers() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM mcp_servers WHERE enabled = 1 ORDER BY name")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def add_mcp_server(data: Dict) -> str:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO mcp_servers (name, enabled, server_type, command, args, env, description, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())""",
                      (data["name"], int(data.get("enabled", True)), data.get("server_type", "local"),
                       data.get("command", ""), json.dumps(data.get("args", [])),
                       json.dumps(data.get("env", {})), data.get("description", "")))
        conn.commit()
        return data["name"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_mcp_server(name: str, data: Dict):
    conn = get_db()
    try:
        with conn.cursor() as c:
            sets = []
            vals = []
            for key in ("enabled", "server_type", "command", "args", "env", "description"):
                if key in data:
                    val = data[key]
                    if key == "enabled":
                        val = int(val)
                    elif key in ("args", "env"):
                        val = json.dumps(val)
                    sets.append(f"{key} = %s")
                    vals.append(val)
            if not sets:
                return
            vals.append(name)
            c.execute(f"UPDATE mcp_servers SET {', '.join(sets)}, updated_at = NOW() WHERE name = %s", vals)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def delete_mcp_server(name: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM mcp_servers WHERE name = %s", (name,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_agent_instructions() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM agent_instructions ORDER BY sort_order, id")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_enabled_agent_instructions() -> str:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT content FROM agent_instructions WHERE enabled = 1 ORDER BY sort_order, id")
            rows = c.fetchall()
            return "\n\n".join(r[0] for r in rows)
    finally:
        conn.close()


def add_agent_instruction(data: Dict) -> int:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO agent_instructions (name, content, enabled, sort_order, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, NOW(), NOW()) RETURNING id""",
                      (data["name"], data["content"], int(data.get("enabled", True)), data.get("sort_order", 0)))
            row_id = c.fetchone()[0]
        conn.commit()
        return row_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_agent_instruction(id: int, data: Dict):
    conn = get_db()
    try:
        with conn.cursor() as c:
            sets, vals = [], []
            for key in ("name", "content", "enabled", "sort_order"):
                if key in data:
                    val = data[key]
                    if key == "enabled":
                        val = int(val)
                    sets.append(f"{key} = %s")
                    vals.append(val)
            if not sets:
                return
            vals.append(id)
            c.execute(f"UPDATE agent_instructions SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s", vals)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def delete_agent_instruction(id: int):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM agent_instruction_assignments WHERE instruction_id = %s", (id,))
            c.execute("DELETE FROM agent_instructions WHERE id = %s", (id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


# ── Agent CRUD with Skills ──────────────────────────────────

def create_agent(agent_id: str, name: str, model_name: str = "", skill_names: List[str] = None, instruction_ids: List[int] = None, repo_affinities: List[str] = None) -> Dict:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO agents (id, name, status, logs, model_name) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                      (agent_id, name or f"Agent-{agent_id}", "idle", json.dumps([]), model_name))
            if skill_names:
                for sn in skill_names:
                    c.execute("INSERT INTO agent_skills (agent_id, mcp_server_name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (agent_id, sn))
            if instruction_ids:
                for iid in instruction_ids:
                    c.execute("INSERT INTO agent_instruction_assignments (agent_id, instruction_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (agent_id, iid))
            if repo_affinities:
                for rn in repo_affinities:
                    c.execute("INSERT INTO agent_repo_affinities (agent_id, repo_name, affinity) VALUES (%s, %s, 1) ON CONFLICT DO NOTHING", (agent_id, rn))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()
    return get_agent_with_profile(agent_id)


def update_agent_profile(agent_id: str, name: str = None, model_name: str = None) -> Dict:
    conn = get_db()
    try:
        with conn.cursor() as c:
            sets, vals = [], []
            if name is not None:
                sets.append("name = %s"); vals.append(name)
            if model_name is not None:
                sets.append("model_name = %s"); vals.append(model_name)
            if sets:
                vals.append(agent_id)
                c.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = %s", vals)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()
    return get_agent_with_profile(agent_id)


def delete_agent(agent_id: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM agent_skills WHERE agent_id = %s", (agent_id,))
            c.execute("DELETE FROM agent_instruction_assignments WHERE agent_id = %s", (agent_id,))
            c.execute("DELETE FROM agents WHERE id = %s", (agent_id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def set_agent_skills(agent_id: str, skill_names: List[str]):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM agent_skills WHERE agent_id = %s", (agent_id,))
            for sn in skill_names:
                c.execute("INSERT INTO agent_skills (agent_id, mcp_server_name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (agent_id, sn))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def set_agent_instruction_assignments(agent_id: str, instruction_ids: List[int]):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM agent_instruction_assignments WHERE agent_id = %s", (agent_id,))
            for iid in instruction_ids:
                c.execute("INSERT INTO agent_instruction_assignments (agent_id, instruction_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (agent_id, iid))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_agent_with_profile(agent_id: str) -> Dict:
    agent = get_agent(agent_id)
    if not agent:
        return None
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT mcp_server_name FROM agent_skills WHERE agent_id = %s", (agent_id,))
            agent["skills"] = [r[0] for r in c.fetchall()]
            c.execute("SELECT instruction_id FROM agent_instruction_assignments WHERE agent_id = %s", (agent_id,))
            agent["instruction_ids"] = [r[0] for r in c.fetchall()]
            c.execute("SELECT repo_name FROM agent_repo_affinities WHERE agent_id = %s ORDER BY repo_name", (agent_id,))
            agent["repo_affinities"] = [r[0] for r in c.fetchall()]
    finally:
        conn.close()
    return agent


def get_all_agents_with_profiles() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT id FROM agents ORDER BY id")
            agent_ids = [r[0] for r in c.fetchall()]
    finally:
        conn.close()
    return [get_agent_with_profile(aid) for aid in agent_ids]


def get_agent_mcp_servers(agent_id: str) -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""SELECT ms.* FROM mcp_servers ms
                         JOIN agent_skills ask ON ms.name = ask.mcp_server_name
                         WHERE ask.agent_id = %s AND ms.enabled = 1""", (agent_id,))
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_agent_assigned_instructions(agent_id: str) -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("""SELECT ai.* FROM agent_instructions ai
                         JOIN agent_instruction_assignments aia ON ai.id = aia.instruction_id
                         WHERE aia.agent_id = %s AND ai.enabled = 1
                         ORDER BY ai.sort_order""", (agent_id,))
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


# ── Ticket Comments ──────────────────────────────────────────

def add_ticket_comment(ticket_id: str, author: str = "system", comment_type: str = "comment", content: str = "") -> int:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO ticket_comments (ticket_id, author, comment_type, content, created_at) VALUES (%s, %s, %s, %s, NOW()) RETURNING id",
                      (ticket_id, author, comment_type, content))
            row_id = c.fetchone()[0]
        conn.commit()
        return row_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_ticket_comments(ticket_id: str) -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM ticket_comments WHERE ticket_id = %s ORDER BY created_at", (ticket_id,))
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


# ── Agent Repo Affinities ────────────────────────────────────

def set_agent_repo_affinities(agent_id: str, repo_names: List[str]):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM agent_repo_affinities WHERE agent_id = %s", (agent_id,))
            for rn in repo_names:
                c.execute("INSERT INTO agent_repo_affinities (agent_id, repo_name, affinity) VALUES (%s, %s, 1) ON CONFLICT DO NOTHING", (agent_id, rn))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def get_agent_repo_affinities(agent_id: str) -> List[str]:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT repo_name FROM agent_repo_affinities WHERE agent_id = %s ORDER BY repo_name", (agent_id,))
            return [r[0] for r in c.fetchall()]
    finally:
        conn.close()


def get_all_repo_names() -> List[str]:
    repos = get_all_repos()
    return [r["name"] for r in repos]


def find_best_agent_for_repo(primary_repo: str, idle_agents: List[Dict]) -> str:
    best_agent = None
    best_score = -1
    conn = get_db()
    try:
        with conn.cursor() as c:
            for agent in idle_agents:
                c.execute("SELECT COUNT(*) as cnt FROM agent_repo_affinities WHERE agent_id = %s AND repo_name = %s",
                          (agent["id"], primary_repo))
                row = c.fetchone()
                score = row[0] if row else 0
                if score > best_score:
                    best_score = score
                    best_agent = agent["id"]
    finally:
        conn.close()
    return best_agent


# ── OpenCode Plugins ──────────────────────────────────────────

def get_opencode_plugins() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM opencode_plugins ORDER BY name")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_enabled_opencode_plugins() -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM opencode_plugins WHERE enabled = 1 ORDER BY name")
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def get_enabled_plugin_names() -> List[str]:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("SELECT name FROM opencode_plugins WHERE enabled = 1 ORDER BY name")
            return [r[0] for r in c.fetchall()]
    finally:
        conn.close()


def add_opencode_plugin(data: Dict) -> str:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO opencode_plugins (name, enabled, description, requires_binary, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, NOW(), NOW()) RETURNING name""",
                      (data["name"], int(data.get("enabled", True)), data.get("description", ""), data.get("requires_binary", "")))
            name = c.fetchone()[0]
        conn.commit()
        return name
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_opencode_plugin(name: str, data: Dict):
    conn = get_db()
    try:
        with conn.cursor() as c:
            sets, vals = [], []
            for key in ("enabled", "description", "requires_binary"):
                if key in data:
                    val = data[key]
                    if key == "enabled":
                        val = int(val)
                    sets.append(f"{key} = %s")
                    vals.append(val)
            if not sets:
                return
            vals.append(name)
            c.execute(f"UPDATE opencode_plugins SET {', '.join(sets)}, updated_at = NOW() WHERE name = %s", vals)
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def delete_opencode_plugin(name: str):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM opencode_plugins WHERE name = %s", (name,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


# ── Agent Memory Blocks (DB-persisted) ────────────────────────

def get_agent_memory_blocks(agent_id: str, repo_name: str = None) -> List[Dict]:
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            if repo_name:
                c.execute("SELECT * FROM agent_memory_blocks WHERE agent_id = %s AND repo_name = %s ORDER BY label", (agent_id, repo_name))
            else:
                c.execute("SELECT * FROM agent_memory_blocks WHERE agent_id = %s ORDER BY repo_name, label", (agent_id,))
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def set_agent_memory_block(agent_id: str, repo_name: str, label: str, content: str,
                           description: str = "", block_limit: int = 5000, read_only: bool = False) -> int:
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO agent_memory_blocks (agent_id, repo_name, label, content, description, block_limit, read_only, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (agent_id, repo_name, label) DO UPDATE SET
                          content = EXCLUDED.content,
                          description = EXCLUDED.description,
                          block_limit = EXCLUDED.block_limit,
                          read_only = EXCLUDED.read_only,
                          updated_at = EXCLUDED.updated_at
                        RETURNING id""",
                      (agent_id, repo_name or "_global", label, content, description, block_limit, int(read_only)))
            row_id = c.fetchone()[0]
        conn.commit()
        return row_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_agent_memory_as_markdown(agent_id: str, repo_name: str) -> str:
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
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM agent_memory_blocks WHERE id = %s", (block_id,))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def seed_default_memory_blocks(agent_id: str):
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
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO ticket_groups (id, parent_ticket_id, title, description)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title, description=EXCLUDED.description""",
                      (group_id, parent_ticket_id, title, description))
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()
    return group_id


def get_ticket_group(group_id):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM ticket_groups WHERE id = %s", (group_id,))
            row = c.fetchone()
            return _row_to_dict(row) if row else None
    finally:
        conn.close()


def add_team_message(group_id, sender_agent_id, content, message_type="info"):
    conn = get_db()
    try:
        with conn.cursor() as c:
            c.execute("""INSERT INTO team_channel_messages (group_id, sender_agent_id, message_type, content)
                        VALUES (%s, %s, %s, %s) RETURNING id""",
                      (group_id, sender_agent_id, message_type, content))
            row_id = c.fetchone()[0]
        conn.commit()
        return row_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_team_messages(group_id, limit=50):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT * FROM team_channel_messages WHERE group_id = %s ORDER BY created_at DESC LIMIT %s", (group_id, limit))
            return [_row_to_dict(r) for r in c.fetchall()]
    finally:
        conn.close()


# ── Init ──
try:
    init_db()
    ensure_config_defaults()
    import_settings_from_env()
except Exception:
    pass