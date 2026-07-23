-- ============================================
-- RLS Policies for HiveMind
-- Version: 00002
-- ============================================

-- Enable RLS on all tables
ALTER TABLE tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE config ENABLE ROW LEVEL SECURITY;
ALTER TABLE mcp_servers ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_skills ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_instructions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_instruction_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_repo_affinities ENABLE ROW LEVEL SECURITY;
ALTER TABLE opencode_plugins ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_memory_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE repos ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE ticket_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_channel_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE rate_limits ENABLE ROW LEVEL SECURITY;

-- Service role can do everything (used by Backend)
CREATE POLICY "Service role full access on tickets" ON tickets FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on agents" ON agents FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on queue" ON queue FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on steps" ON steps FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on config" ON config FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on mcp_servers" ON mcp_servers FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on agent_skills" ON agent_skills FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on agent_instructions" ON agent_instructions FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on agent_instruction_assignments" ON agent_instruction_assignments FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on ticket_comments" ON ticket_comments FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on agent_repo_affinities" ON agent_repo_affinities FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on opencode_plugins" ON opencode_plugins FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on agent_memory_blocks" ON agent_memory_blocks FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on repos" ON repos FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on metric_events" ON metric_events FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on ticket_groups" ON ticket_groups FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Service role full access on team_channel_messages" ON team_channel_messages FOR ALL USING (auth.role() = 'service_role');

-- Authenticated users can read most data (Dashboard)
CREATE POLICY "Authenticated read access on tickets" ON tickets FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on agents" ON agents FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on queue" ON queue FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on steps" ON steps FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on config" ON config FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on repos" ON repos FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on mcp_servers" ON mcp_servers FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on metric_events" ON metric_events FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on ticket_groups" ON ticket_groups FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));
CREATE POLICY "Authenticated read access on team_channel_messages" ON team_channel_messages FOR SELECT USING (auth.role() IN ('authenticated', 'service_role'));

-- Agents can see their own tickets, memory, steps
CREATE POLICY "Agent sees own tickets" ON tickets FOR SELECT USING (agent_id = auth.uid()::text OR auth.role() = 'service_role');
CREATE POLICY "Agent sees own memory" ON agent_memory_blocks FOR ALL USING (agent_id = auth.uid()::text OR auth.role() = 'service_role');

-- Rate limit cleanup function
CREATE OR REPLACE FUNCTION cleanup_rate_limits()
RETURNS void AS $$
BEGIN
    DELETE FROM rate_limits WHERE request_at < NOW() - INTERVAL '1 minute';
END;
$$ LANGUAGE plpgsql;

-- Rate limit check function
CREATE OR REPLACE FUNCTION check_rate_limit(p_client_ip TEXT, p_max_requests INTEGER)
RETURNS TABLE(allowed BOOLEAN, remaining INTEGER) AS $$
DECLARE
    v_count INTEGER;
BEGIN
    DELETE FROM rate_limits WHERE request_at < NOW() - INTERVAL '1 minute';
    INSERT INTO rate_limits (client_ip, request_at) VALUES (p_client_ip, NOW());
    SELECT COUNT(*) INTO v_count FROM rate_limits WHERE client_ip = p_client_ip;
    RETURN QUERY SELECT (v_count <= p_max_requests) AS allowed, (p_max_requests - v_count) AS remaining;
END;
$$ LANGUAGE plpgsql;