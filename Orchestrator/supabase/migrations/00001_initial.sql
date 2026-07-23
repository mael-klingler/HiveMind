-- ============================================
-- HiveMind Schema Migration for Supabase
-- Version: 00001
-- ============================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Tickets ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    labels JSONB DEFAULT '[]',
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
    ai_planning JSONB,
    mr_pipeline_status TEXT DEFAULT 'unknown',
    mr_last_note_id INTEGER DEFAULT 0,
    mr_project_path TEXT,
    mr_iid INTEGER,
    mr_conflict_status TEXT DEFAULT 'none',
    selected_repos JSONB DEFAULT '[]',
    phase_work_started_at TIMESTAMPTZ,
    phase_test_started_at TIMESTAMPTZ,
    phase_ship_started_at TIMESTAMPTZ,
    phase_listen_started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    merged_at TIMESTAMPTZ,
    first_pipeline_status TEXT DEFAULT 'unknown',
    review_cycle_count INTEGER DEFAULT 0,
    model_used TEXT,
    llm_prompt_tokens INTEGER DEFAULT 0,
    llm_completion_tokens INTEGER DEFAULT 0,
    llm_total_cost_usd NUMERIC DEFAULT 0.0,
    primary_repo TEXT,
    lines_added INTEGER DEFAULT 0,
    lines_removed INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    current_phase TEXT DEFAULT 'work',
    pipeline_group_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Agents ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    role TEXT DEFAULT 'general',
    status TEXT DEFAULT 'idle',
    current_ticket_id TEXT REFERENCES tickets(id),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    progress INTEGER DEFAULT 0,
    logs JSONB DEFAULT '[]',
    model_name TEXT DEFAULT ''
);

-- ── Queue ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS queue (
    id BIGSERIAL PRIMARY KEY,
    ticket_id TEXT REFERENCES tickets(id),
    position INTEGER,
    assigned_agent_id TEXT REFERENCES agents(id),
    status TEXT DEFAULT 'waiting',
    priority INTEGER DEFAULT 5,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- ── Steps ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS steps (
    id BIGSERIAL PRIMARY KEY,
    queue_id BIGINT REFERENCES queue(id),
    ticket_id TEXT REFERENCES tickets(id),
    agent_id TEXT,
    step_name TEXT,
    status TEXT DEFAULT 'pending',
    detail TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- ── Config (Settings) ────────────────────────────────
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- ── MCP Servers ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS mcp_servers (
    name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    server_type TEXT DEFAULT 'local',
    command TEXT DEFAULT '',
    args JSONB DEFAULT '[]',
    env JSONB DEFAULT '{}',
    description TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Agent Skills (MCP assignments) ──────────────────
CREATE TABLE IF NOT EXISTS agent_skills (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    mcp_server_name TEXT NOT NULL REFERENCES mcp_servers(name) ON DELETE CASCADE,
    UNIQUE(agent_id, mcp_server_name)
);

-- ── Agent Instructions ───────────────────────────────
CREATE TABLE IF NOT EXISTS agent_instructions (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Agent Instruction Assignments ────────────────────
CREATE TABLE IF NOT EXISTS agent_instruction_assignments (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    instruction_id INTEGER NOT NULL REFERENCES agent_instructions(id) ON DELETE CASCADE,
    UNIQUE(agent_id, instruction_id)
);

-- ── Ticket Comments ─────────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_comments (
    id BIGSERIAL PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author TEXT DEFAULT 'system',
    comment_type TEXT DEFAULT 'comment',
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Agent Repo Affinities ────────────────────────────
CREATE TABLE IF NOT EXISTS agent_repo_affinities (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    repo_name TEXT NOT NULL,
    affinity INTEGER DEFAULT 1,
    UNIQUE(agent_id, repo_name)
);

-- ── OpenCode Plugins ────────────────────────────────
CREATE TABLE IF NOT EXISTS opencode_plugins (
    name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    description TEXT DEFAULT '',
    requires_binary TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Agent Memory Blocks ──────────────────────────────
CREATE TABLE IF NOT EXISTS agent_memory_blocks (
    id BIGSERIAL PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    repo_name TEXT NOT NULL DEFAULT '_global',
    label TEXT NOT NULL,
    content TEXT NOT NULL,
    description TEXT DEFAULT '',
    block_limit INTEGER DEFAULT 5000,
    read_only INTEGER DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(agent_id, repo_name, label)
);

-- ── Repos ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repos (
    name TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    branch TEXT DEFAULT 'development',
    description TEXT DEFAULT '',
    tags JSONB DEFAULT '[]',
    active INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Metric Events ───────────────────────────────────
CREATE TABLE IF NOT EXISTS metric_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    ticket_id TEXT REFERENCES tickets(id),
    agent_id TEXT,
    phase TEXT,
    duration_seconds NUMERIC,
    labels JSONB,
    value NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Ticket Groups ────────────────────────────────────
CREATE TABLE IF NOT EXISTS ticket_groups (
    id TEXT PRIMARY KEY,
    parent_ticket_id TEXT NOT NULL REFERENCES tickets(id),
    title TEXT,
    description TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Team Channel Messages ───────────────────────────
CREATE TABLE IF NOT EXISTS team_channel_messages (
    id BIGSERIAL PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES ticket_groups(id) ON DELETE CASCADE,
    sender_agent_id TEXT NOT NULL,
    message_type TEXT DEFAULT 'info',
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Rate Limits (for Edge Function) ─────────────────
CREATE TABLE IF NOT EXISTS rate_limits (
    id BIGSERIAL PRIMARY KEY,
    client_ip TEXT NOT NULL,
    request_at TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_mr_status ON tickets(mr_status);
CREATE INDEX IF NOT EXISTS idx_tickets_agent_id ON tickets(agent_id);
CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_ticket_id ON queue(ticket_id);
CREATE INDEX IF NOT EXISTS idx_steps_ticket_id ON steps(ticket_id);
CREATE INDEX IF NOT EXISTS idx_steps_agent_id ON steps(agent_id);
CREATE INDEX IF NOT EXISTS idx_metric_events_type ON metric_events(event_type);
CREATE INDEX IF NOT EXISTS idx_metric_events_ticket ON metric_events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_metric_events_created ON metric_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ticket_comments_ticket ON ticket_comments(ticket_id);
CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON agent_memory_blocks(agent_id);
CREATE INDEX IF NOT EXISTS idx_rate_limits_ip_time ON rate_limits(client_ip, request_at);

-- ── Seed Data ───────────────────────────────────────
INSERT INTO config (key, value) VALUES ('max_agents', '3') ON CONFLICT (key) DO NOTHING;
INSERT INTO config (key, value) VALUES ('polling_interval_seconds', '5') ON CONFLICT (key) DO NOTHING;
INSERT INTO mcp_servers (name, server_type, command, description) VALUES ('leankg-mcp', 'local', 'leankg mcp-stdio', 'LeanKG code search and dependency analysis') ON CONFLICT (name) DO NOTHING;
INSERT INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-snip', 'Compress shell output (60-97% token savings)', 'snip') ON CONFLICT (name) DO NOTHING;
INSERT INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-agent-memory', 'Persistent memory blocks for agents (Letta-inspired)', '') ON CONFLICT (name) DO NOTHING;
INSERT INTO opencode_plugins (name, description, requires_binary) VALUES ('opencode-handoff', 'Session handoff for contextual transitions on retries', '') ON CONFLICT (name) DO NOTHING;

-- ── Pipeline Steps ───────────────────────────────────
CREATE TABLE IF NOT EXISTS pipeline_steps (
    id BIGSERIAL PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    group_id TEXT REFERENCES ticket_groups(id),
    phase TEXT NOT NULL,
    agent_id TEXT,
    status TEXT DEFAULT 'pending',
    result TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pipeline_steps_ticket ON pipeline_steps(ticket_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_steps_status ON pipeline_steps(status);

-- ── Auto-update timestamp trigger ───────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_tickets_updated_at BEFORE UPDATE ON tickets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_mcp_servers_updated_at BEFORE UPDATE ON mcp_servers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_agent_instructions_updated_at BEFORE UPDATE ON agent_instructions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_opencode_plugins_updated_at BEFORE UPDATE ON opencode_plugins
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_repos_updated_at BEFORE UPDATE ON repos
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER update_agent_memory_blocks_updated_at BEFORE UPDATE ON agent_memory_blocks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();