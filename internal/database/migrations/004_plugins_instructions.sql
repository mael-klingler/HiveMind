-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- opencode plugins
CREATE TABLE IF NOT EXISTS opencode_plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    package TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    description TEXT DEFAULT '',
    config TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- agent instructions (reusable instruction snippets assignable to agents)
CREATE TABLE IF NOT EXISTS agent_instructions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT DEFAULT '',
    description TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opencode_plugins_name ON opencode_plugins(name);
CREATE INDEX IF NOT EXISTS idx_agent_instructions_name ON agent_instructions(name);

-- +goose Down
DROP TABLE IF EXISTS agent_instructions;
DROP TABLE IF EXISTS opencode_plugins;