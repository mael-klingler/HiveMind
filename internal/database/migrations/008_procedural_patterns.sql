-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- Procedural patterns: learned patterns for cross-session agent-set optimization
CREATE TABLE IF NOT EXISTS procedural_patterns (
    id SERIAL PRIMARY KEY,
    pattern_type TEXT NOT NULL,
    description TEXT DEFAULT '',
    context TEXT DEFAULT '',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_procedural_type ON procedural_patterns(pattern_type);

-- +goose Down
DROP INDEX IF EXISTS idx_procedural_type;
DROP TABLE IF EXISTS procedural_patterns;