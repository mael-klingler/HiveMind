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
	"encoding/json"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/maelklingler/hivemind/internal/database/repository"
)

// --- PipelineRepository ---

// PipelineRepo implements repository.PipelineRepository.
type PipelineRepo struct{ pool *pgxpool.Pool }

func NewPipelineRepo(pool *pgxpool.Pool) *PipelineRepo { return &PipelineRepo{pool: pool} }

func (r *PipelineRepo) CreateStep(ctx context.Context, step *repository.PipelineStep) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO pipeline_steps (id, ticket_id, phase, status, role, agent_id, started_at, completed_at, retry_count, context, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())`,
		step.ID, step.TicketID, string(step.Phase), step.Status, step.Role, step.AgentID,
		step.StartedAt, step.CompletedAt, step.RetryCount, step.Context)
	return err
}

func (r *PipelineRepo) GetStep(ctx context.Context, id string) (*repository.PipelineStep, error) {
	row := r.pool.QueryRow(ctx, `
		SELECT id, ticket_id, phase, status, role, agent_id, started_at, completed_at, retry_count, context, created_at
		FROM pipeline_steps WHERE id = $1`, id)
	return scanStep(row)
}

func (r *PipelineRepo) ListStepsByTicket(ctx context.Context, ticketID string) ([]*repository.PipelineStep, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, ticket_id, phase, status, role, agent_id, started_at, completed_at, retry_count, context, created_at
		FROM pipeline_steps WHERE ticket_id = $1 ORDER BY created_at ASC`, ticketID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var steps []*repository.PipelineStep
	for rows.Next() {
		s, err := scanStepRow(rows)
		if err != nil {
			return nil, err
		}
		steps = append(steps, s)
	}
	return steps, rows.Err()
}

func (r *PipelineRepo) UpdateStepStatus(ctx context.Context, id, status string) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE pipeline_steps SET status = $1, completed_at = CASE WHEN $1 IN ('completed','failed') THEN NOW() ELSE completed_at END
		WHERE id = $2`, status, id)
	return err
}

func (r *PipelineRepo) AdvancePhase(ctx context.Context, ticketID string, currentPhase repository.Phase) (repository.Phase, error) {
	next := nextPhase(currentPhase)
	if next == "" {
		return "", fmt.Errorf("no phase after %s", currentPhase)
	}
	return next, nil
}

func nextPhase(p repository.Phase) repository.Phase {
	switch p {
	case repository.PhaseWork:
		return repository.PhaseTest
	case repository.PhaseTest:
		return repository.PhaseReview
	case repository.PhaseReview:
		return repository.PhaseShip
	case repository.PhaseShip:
		return repository.PhaseListen
	default:
		return ""
	}
}

type rowScanner interface {
	Scan(dest ...interface{}) error
}

func scanStep(row rowScanner) (*repository.PipelineStep, error) {
	s := &repository.PipelineStep{}
	var phase string
	var startedAt, completedAt *time.Time
	err := row.Scan(&s.ID, &s.TicketID, &phase, &s.Status, &s.Role, &s.AgentID,
		&startedAt, &completedAt, &s.RetryCount, &s.Context, &s.CreatedAt)
	if err != nil {
		return nil, err
	}
	s.Phase = repository.Phase(phase)
	s.StartedAt = startedAt
	s.CompletedAt = completedAt
	return s, nil
}

type rowsScanner interface {
	Scan(dest ...interface{}) error
	Next() bool
	Err() error
}

func scanStepRow(rows rowsScanner) (*repository.PipelineStep, error) {
	return scanStep(rows)
}

// --- GroupRepository ---

// GroupRepo implements repository.GroupRepository.
type GroupRepo struct{ pool *pgxpool.Pool }

func NewGroupRepo(pool *pgxpool.Pool) *GroupRepo { return &GroupRepo{pool: pool} }

func (r *GroupRepo) CreateGroup(ctx context.Context, g *repository.Group) error {
	ticketIDs, _ := json.Marshal(g.TicketIDs)
	_, err := r.pool.Exec(ctx, `
		INSERT INTO ticket_groups (id, name, description, ticket_ids, created_at)
		VALUES ($1, $2, $3, $4, NOW())`,
		g.ID, g.Name, g.Description, string(ticketIDs))
	return err
}

func (r *GroupRepo) GetGroup(ctx context.Context, id string) (*repository.Group, error) {
	row := r.pool.QueryRow(ctx, `SELECT id, name, description, ticket_ids, created_at FROM ticket_groups WHERE id = $1`, id)
	g := &repository.Group{}
	var ticketIDs string
	err := row.Scan(&g.ID, &g.Name, &g.Description, &ticketIDs, &g.CreatedAt)
	if err != nil {
		return nil, err
	}
	_ = json.Unmarshal([]byte(ticketIDs), &g.TicketIDs)
	return g, nil
}

func (r *GroupRepo) ListGroups(ctx context.Context) ([]*repository.Group, error) {
	rows, err := r.pool.Query(ctx, `SELECT id, name, description, ticket_ids, created_at FROM ticket_groups ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var groups []*repository.Group
	for rows.Next() {
		g := &repository.Group{}
		var ticketIDs string
		if err := rows.Scan(&g.ID, &g.Name, &g.Description, &ticketIDs, &g.CreatedAt); err != nil {
			return nil, err
		}
		_ = json.Unmarshal([]byte(ticketIDs), &g.TicketIDs)
		groups = append(groups, g)
	}
	return groups, rows.Err()
}

func (r *GroupRepo) DeleteGroup(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM ticket_groups WHERE id = $1`, id)
	return err
}

func (r *GroupRepo) AddMessage(ctx context.Context, groupID, agentID, messageType, content string) error {
	id := fmt.Sprintf("msg-%s-%d", groupID, time.Now().UnixNano())
	_, err := r.pool.Exec(ctx, `
		INSERT INTO team_channel_messages (id, group_id, agent_id, content, message_type, created_at)
		VALUES ($1, $2, $3, $4, $5, NOW())`,
		id, groupID, agentID, content, messageType)
	return err
}

func (r *GroupRepo) ListMessages(ctx context.Context, groupID string) ([]*repository.GroupMessage, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, group_id, agent_id, content, message_type, created_at
		FROM team_channel_messages WHERE group_id = $1 ORDER BY created_at ASC`, groupID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var msgs []*repository.GroupMessage
	for rows.Next() {
		m := &repository.GroupMessage{}
		if err := rows.Scan(&m.ID, &m.GroupID, &m.AgentID, &m.Content, &m.MessageType, &m.CreatedAt); err != nil {
			return nil, err
		}
		msgs = append(msgs, m)
	}
	return msgs, rows.Err()
}

var (
	_ repository.PipelineRepository = (*PipelineRepo)(nil)
	_ repository.GroupRepository    = (*GroupRepo)(nil)
)