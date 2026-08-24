// Copyright 2026 Mael Klingler
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package pgxrepo

import (
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/models"
)

// --- SettingsRepository ---

// SettingsRepo implements repository.SettingsRepository.
type SettingsRepo struct{ pool *pgxpool.Pool }

func NewSettingsRepo(pool *pgxpool.Pool) *SettingsRepo { return &SettingsRepo{pool: pool} }

func (r *SettingsRepo) GetSetting(ctx context.Context, key string) (string, error) {
	var value string
	err := r.pool.QueryRow(ctx, `SELECT value FROM settings WHERE key = $1`, key).Scan(&value)
	if err != nil {
		return "", nil
	}
	return value, nil
}

func (r *SettingsRepo) SetSetting(ctx context.Context, key, value string) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO settings (key, value) VALUES ($1, $2)
		ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`, key, value)
	return err
}

func (r *SettingsRepo) GetAllSettings(ctx context.Context) (map[string]string, error) {
	rows, err := r.pool.Query(ctx, `SELECT key, value FROM settings ORDER BY key`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	settings := make(map[string]string)
	for rows.Next() {
		var k, v string
		if err := rows.Scan(&k, &v); err != nil {
			return nil, err
		}
		settings[k] = v
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return settings, nil
}

// --- MetricRepository ---

// MetricRepo implements repository.MetricRepository.
type MetricRepo struct{ pool *pgxpool.Pool }

func NewMetricRepo(pool *pgxpool.Pool) *MetricRepo { return &MetricRepo{pool: pool} }

func (r *MetricRepo) RecordMetricEvent(ctx context.Context, in *repository.MetricEventInput) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO metric_events (event_type, ticket_id, agent_id, phase, duration_seconds, labels, value, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())`,
		in.EventType, in.TicketID, in.AgentID, in.Phase, in.DurationSeconds, in.Labels, in.Value)
	return err
}

func (r *MetricRepo) GetMetricsSummary(ctx context.Context) (*repository.MetricsSummary, error) {
	row := r.pool.QueryRow(ctx, `
		SELECT
			COUNT(*) as total,
			COUNT(*) FILTER (WHERE status = 'completed') as completed,
			COUNT(*) FILTER (WHERE status = 'failed') as failed,
			COUNT(*) FILTER (WHERE status = 'merged') as merged,
			COALESCE(SUM(retry_count), 0) as total_retries,
			COALESCE(AVG(review_cycle_count), 0) as avg_review_cycles,
			COALESCE(SUM(llm_prompt_tokens), 0) as total_prompt_tokens,
			COALESCE(SUM(llm_completion_tokens), 0) as total_completion_tokens,
			COALESCE(SUM(llm_total_cost_usd), 0) as total_llm_cost_usd
		FROM tickets`)
	m := &repository.MetricsSummary{}
	err := row.Scan(&m.TotalTickets, &m.CompletedTickets, &m.FailedTickets, &m.MergedTickets,
		&m.TotalRetries, &m.AvgReviewCycles, &m.TotalPromptTokens, &m.TotalCompletionTokens, &m.TotalLLMCostUSD)
	if err != nil {
		return nil, err
	}
	return m, nil
}

// --- MCPRepository ---

// MCPRepo implements repository.MCPRepository.
type MCPRepo struct{ pool *pgxpool.Pool }

func NewMCPRepo(pool *pgxpool.Pool) *MCPRepo { return &MCPRepo{pool: pool} }

func (r *MCPRepo) ListMCPServers(ctx context.Context) ([]*models.MCPServer, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, name, command, args, env, server_type, enabled, description
		FROM mcp_servers ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var servers []*models.MCPServer
	for rows.Next() {
		s := &models.MCPServer{}
		var enabled int
		err := rows.Scan(&s.ID, &s.Name, &s.Command, &s.Args, &s.Env, &s.ServerType, &enabled, &s.Description)
		if err != nil {
			return nil, err
		}
		s.Enabled = enabled == 1
		servers = append(servers, s)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return servers, nil
}

func (r *MCPRepo) GetEnabledMCPServers(ctx context.Context) ([]*models.MCPServer, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, name, command, args, env, server_type, enabled, description
		FROM mcp_servers WHERE enabled = 1 ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var servers []*models.MCPServer
	for rows.Next() {
		s := &models.MCPServer{}
		var enabled int
		err := rows.Scan(&s.ID, &s.Name, &s.Command, &s.Args, &s.Env, &s.ServerType, &enabled, &s.Description)
		if err != nil {
			return nil, err
		}
		s.Enabled = true
		servers = append(servers, s)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return servers, nil
}

func (r *MCPRepo) CreateMCPServer(ctx context.Context, in *repository.MCPServerInput) (string, error) {
	id := fmt.Sprintf("mcp-%d", time.Now().UnixNano())
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO mcp_servers (id, name, command, args, env, server_type, enabled, description)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		id, in.Name, in.Command, in.Args, in.Env, in.ServerType, enabled, in.Description)
	if err != nil {
		return "", err
	}
	return id, nil
}

func (r *MCPRepo) UpdateMCPServer(ctx context.Context, id string, in *repository.MCPServerInput) error {
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := r.pool.Exec(ctx, `
		UPDATE mcp_servers SET name = $2, command = $3, args = $4, env = $5, server_type = $6, enabled = $7, description = $8
		WHERE id = $1`,
		id, in.Name, in.Command, in.Args, in.Env, in.ServerType, enabled, in.Description)
	return err
}

func (r *MCPRepo) DeleteMCPServer(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM mcp_servers WHERE id = $1`, id)
	return err
}

// --- StepRepository (hardcoded for now — DB-backed in Phase 2) ---

// StepRepo implements repository.StepRepository.
type StepRepo struct{}

func NewStepRepo() *StepRepo { return &StepRepo{} }

func (r *StepRepo) ListSteps(ctx context.Context) ([]*repository.Step, error) {
	return []*repository.Step{
		{ID: "plan", Name: "Plan", Order: 1},
		{ID: "work", Name: "Work", Order: 2},
		{ID: "review", Name: "Review", Order: 3},
		{ID: "ship", Name: "Ship", Order: 4},
	}, nil
}

var (
	_ repository.SettingsRepository = (*SettingsRepo)(nil)
	_ repository.MetricRepository   = (*MetricRepo)(nil)
	_ repository.MCPRepository      = (*MCPRepo)(nil)
	_ repository.StepRepository     = (*StepRepo)(nil)
)