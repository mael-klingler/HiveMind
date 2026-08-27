-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- Pipeline steps: per-ticket phase execution tracking
CREATE TABLE IF NOT EXISTS pipeline_steps (
    id TEXT PRIMARY KEY,
    ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    phase TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    role TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    retry_count INTEGER DEFAULT 0,
    context TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_steps_ticket ON pipeline_steps(ticket_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_steps_ticket_phase ON pipeline_steps(ticket_id, phase);
CREATE INDEX IF NOT EXISTS idx_pipeline_steps_status ON pipeline_steps(status);

-- +goose Down
DROP TABLE IF EXISTS pipeline_steps;