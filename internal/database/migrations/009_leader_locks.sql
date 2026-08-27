-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
CREATE TABLE IF NOT EXISTS leader_locks (
    lock_key TEXT PRIMARY KEY,
    holder TEXT NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    acquired_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- +goose Down
DROP TABLE IF EXISTS leader_locks;