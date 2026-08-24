-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- Agent memory blocks (replaces the minimal agent_memory table with a richer
-- schema matching the Python agent_memory_blocks model). We keep the old
-- agent_memory table for backward compatibility but add the new one alongside.
CREATE TABLE IF NOT EXISTS agent_memory_blocks (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    label TEXT NOT NULL,
    content TEXT DEFAULT '',
    description TEXT DEFAULT '',
    read_only INTEGER DEFAULT 0,
    block_limit INTEGER DEFAULT 5000,
    repo_name TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (agent_id, label)
);

CREATE INDEX IF NOT EXISTS idx_agent_memory_blocks_agent ON agent_memory_blocks(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_memory_blocks_agent_label ON agent_memory_blocks(agent_id, label);

-- +goose Down
DROP TABLE IF EXISTS agent_memory_blocks;