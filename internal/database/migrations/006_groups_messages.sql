-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- Rename team_channel_messages.channel_id -> group_id to match the Python
-- schema and the GroupRepository interface. SQLite/Postgres rename is safe.
ALTER TABLE team_channel_messages RENAME COLUMN channel_id TO group_id;

CREATE INDEX IF NOT EXISTS idx_team_channel_messages_group ON team_channel_messages(group_id);

-- +goose Down
DROP INDEX IF EXISTS idx_team_channel_messages_group;
ALTER TABLE team_channel_messages RENAME COLUMN group_id TO channel_id;