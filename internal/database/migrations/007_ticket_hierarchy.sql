-- Copyright 2026 Mael Klingler
-- Licensed under the Apache License, Version 2.0

-- +goose Up
-- Ticket hierarchy: parent-child relationships + approval gate
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS parent_id TEXT DEFAULT '';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS ticket_type TEXT DEFAULT 'task';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS approval_status TEXT DEFAULT '';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS approval_feedback TEXT DEFAULT '';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS approval_required INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_tickets_parent ON tickets(parent_id);
CREATE INDEX IF NOT EXISTS idx_tickets_approval ON tickets(approval_status);
CREATE INDEX IF NOT EXISTS idx_tickets_type ON tickets(ticket_type);

-- +goose Down
DROP INDEX IF EXISTS idx_tickets_type;
DROP INDEX IF EXISTS idx_tickets_approval;
DROP INDEX IF EXISTS idx_tickets_parent;
ALTER TABLE tickets DROP COLUMN IF EXISTS approval_required;
ALTER TABLE tickets DROP COLUMN IF EXISTS approval_feedback;
ALTER TABLE tickets DROP COLUMN IF EXISTS approval_status;
ALTER TABLE tickets DROP COLUMN IF EXISTS ticket_type;
ALTER TABLE tickets DROP COLUMN IF EXISTS parent_id;