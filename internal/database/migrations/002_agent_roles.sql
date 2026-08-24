-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- Add role / model_name / logs / current_ticket_id columns to agents
ALTER TABLE agents ADD COLUMN IF NOT EXISTS role TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS model_name TEXT DEFAULT '';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS logs TEXT DEFAULT '[]';
ALTER TABLE agents ADD COLUMN IF NOT EXISTS current_ticket_id TEXT DEFAULT '';

-- Agent skills (tags describing what an agent is good at)
CREATE TABLE IF NOT EXISTS agent_skills (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    skill TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (agent_id, skill)
);

-- Agent repo affinities (preference weights for repos)
CREATE TABLE IF NOT EXISTS agent_repo_affinities (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    repo_name TEXT NOT NULL,
    weight INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (agent_id, repo_name)
);

-- Agent instruction assignments (many-to-many agent <-> instruction)
CREATE TABLE IF NOT EXISTS agent_instruction_assignments (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    instruction_id TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE (agent_id, instruction_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_skills_agent ON agent_skills(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_affinities_agent ON agent_repo_affinities(agent_id);

-- +goose Down
DROP TABLE IF EXISTS agent_instruction_assignments;
DROP TABLE IF EXISTS agent_repo_affinities;
DROP TABLE IF EXISTS agent_skills;
ALTER TABLE agents DROP COLUMN IF EXISTS current_ticket_id;
ALTER TABLE agents DROP COLUMN IF EXISTS logs;
ALTER TABLE agents DROP COLUMN IF EXISTS model_name;
ALTER TABLE agents DROP COLUMN IF EXISTS role;