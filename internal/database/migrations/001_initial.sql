-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    labels TEXT DEFAULT '[]',
    issue_type TEXT DEFAULT '',
    priority TEXT DEFAULT '',
    status TEXT DEFAULT 'queued',
    mr_status TEXT DEFAULT 'none',
    mr_url TEXT DEFAULT '',
    mr_project_path TEXT DEFAULT '',
    mr_iid INTEGER,
    review_status TEXT DEFAULT 'pending',
    review_notes TEXT DEFAULT '',
    retry_count INTEGER DEFAULT 0,
    workspace_path TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    selected_repos TEXT DEFAULT '[]',
    primary_repo TEXT DEFAULT '',
    ai_planning TEXT DEFAULT '',
    branch TEXT DEFAULT '',
    model_used TEXT DEFAULT '',
    mr_pipeline_status TEXT DEFAULT 'unknown',
    mr_last_note_id INTEGER DEFAULT 0,
    mr_conflict_status TEXT DEFAULT 'none',
    phase_work_started_at TIMESTAMP,
    phase_test_started_at TIMESTAMP,
    phase_ship_started_at TIMESTAMP,
    phase_listen_started_at TIMESTAMP,
    completed_at TIMESTAMP,
    merged_at TIMESTAMP,
    llm_prompt_tokens INTEGER DEFAULT 0,
    llm_completion_tokens INTEGER DEFAULT 0,
    llm_total_cost_usd REAL DEFAULT 0.0,
    review_cycle_count INTEGER DEFAULT 0,
    first_pipeline_status TEXT DEFAULT 'unknown',
    lines_added INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'idle',
    current_task TEXT DEFAULT '',
    progress TEXT DEFAULT '',
    last_seen TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS repos (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    branch TEXT DEFAULT 'development',
    description TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    active INTEGER DEFAULT 1,
    last_synced TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS queue (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    agent_id TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_comments (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    author TEXT DEFAULT '',
    comment_type TEXT DEFAULT 'comment',
    content TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    skills TEXT DEFAULT '',
    instructions TEXT DEFAULT '',
    memory_summary TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_memory (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    label TEXT NOT NULL,
    content TEXT DEFAULT '',
    description TEXT DEFAULT '',
    read_only INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    command TEXT DEFAULT '',
    args TEXT DEFAULT '[]',
    env TEXT DEFAULT '{}',
    server_type TEXT DEFAULT 'local',
    enabled INTEGER DEFAULT 1,
    description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS metric_events (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    ticket_id TEXT REFERENCES tickets(id),
    agent_id TEXT DEFAULT '',
    phase TEXT DEFAULT '',
    duration_seconds REAL DEFAULT 0,
    labels TEXT DEFAULT '{}',
    value REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ticket_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    ticket_ids TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS team_channel_messages (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    agent_id TEXT DEFAULT '',
    content TEXT DEFAULT '',
    message_type TEXT DEFAULT 'message',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_agent ON tickets(agent_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created ON tickets(created_at);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_queue_ticket ON queue(ticket_id);
CREATE INDEX IF NOT EXISTS idx_metric_events_type ON metric_events(event_type);
CREATE INDEX IF NOT EXISTS idx_metric_events_ticket ON metric_events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_metric_events_created ON metric_events(created_at);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket_created ON ticket_comments(ticket_id, created_at);
CREATE INDEX IF NOT EXISTS idx_queue_priority_created ON queue(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_team_channel_messages_group ON team_channel_messages(channel_id);

-- +goose Down
DROP TABLE IF EXISTS team_channel_messages;
DROP TABLE IF EXISTS ticket_groups;
DROP TABLE IF EXISTS metric_events;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS mcp_servers;
DROP TABLE IF EXISTS agent_memory;
DROP TABLE IF EXISTS agent_profiles;
DROP TABLE IF EXISTS ticket_comments;
DROP TABLE IF EXISTS queue;
DROP TABLE IF EXISTS repos;
DROP TABLE IF EXISTS agents;
DROP TABLE IF EXISTS tickets;