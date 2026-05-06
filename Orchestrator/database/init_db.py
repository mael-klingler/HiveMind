import json
from database.sqlite_backend import get_db, _add_column_if_not_exists


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

    # Metrics: ticket phase and lifecycle columns
    _add_column_if_not_exists(c, "tickets", "phase_work_started_at", "TEXT")
    _add_column_if_not_exists(c, "tickets", "phase_test_started_at", "TEXT")
    _add_column_if_not_exists(c, "tickets", "phase_ship_started_at", "TEXT")
    _add_column_if_not_exists(c, "tickets", "phase_listen_started_at", "TEXT")
    _add_column_if_not_exists(c, "tickets", "completed_at", "TEXT")
    _add_column_if_not_exists(c, "tickets", "merged_at", "TEXT")
    _add_column_if_not_exists(c, "tickets", "first_pipeline_status", "TEXT DEFAULT 'unknown'")
    _add_column_if_not_exists(c, "tickets", "review_cycle_count", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(c, "tickets", "model_used", "TEXT")
    _add_column_if_not_exists(c, "tickets", "llm_prompt_tokens", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(c, "tickets", "llm_completion_tokens", "INTEGER DEFAULT 0")
    _add_column_if_not_exists(c, "tickets", "llm_total_cost_usd", "REAL DEFAULT 0.0")
    _add_column_if_not_exists(c, "tickets", "primary_repo", "TEXT")

    # Metric events table
    c.execute("""
    CREATE TABLE IF NOT EXISTS metric_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        ticket_id TEXT,
        agent_id TEXT,
        phase TEXT,
        duration_seconds REAL,
        labels TEXT,
        value REAL,
        created_at TEXT,
        FOREIGN KEY (ticket_id) REFERENCES tickets(id)
    )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_metric_events_type ON metric_events(event_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_metric_events_ticket ON metric_events(ticket_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_metric_events_created ON metric_events(created_at)")

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