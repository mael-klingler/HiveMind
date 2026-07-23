-- Migration: Add priority column to queue table + role column to agents + phase columns to tickets + pipeline_steps
ALTER TABLE queue ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 5;
ALTER TABLE agents ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'general';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS current_phase TEXT DEFAULT 'work';
ALTER TABLE tickets ADD COLUMN IF NOT EXISTS pipeline_group_id TEXT;
CREATE INDEX IF NOT EXISTS idx_queue_priority ON queue(priority);
CREATE INDEX IF NOT EXISTS idx_agents_role ON agents(role);