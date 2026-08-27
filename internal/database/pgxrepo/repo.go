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

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/models"
)

// --- RepoRepository ---

// RepoRepo implements repository.RepoRepository.
type RepoRepo struct{ pool *pgxpool.Pool }

func NewRepoRepo(pool *pgxpool.Pool) *RepoRepo { return &RepoRepo{pool: pool} }

func (r *RepoRepo) AddRepo(ctx context.Context, in *repository.RepoInput) error {
	now := time.Now().UTC()
	tags, _ := json.Marshal(in.Tags)
	active := 1
	if !in.Active {
		active = 0
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO repos (name, url, branch, description, tags, active, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (name) DO UPDATE SET
			url = EXCLUDED.url, branch = EXCLUDED.branch,
			description = EXCLUDED.description, tags = EXCLUDED.tags`,
		in.Name, in.URL, in.Branch, in.Description, string(tags), active, now)
	return err
}

func (r *RepoRepo) GetRepo(ctx context.Context, name string) (*models.Repo, error) {
	row := r.pool.QueryRow(ctx, `
		SELECT name, url, branch, description, tags, active, last_synced, created_at
		FROM repos WHERE name = $1`, name)
	repo := &models.Repo{}
	var tags string
	var active int
	var lastSynced *time.Time
	err := row.Scan(&repo.Name, &repo.URL, &repo.Branch, &repo.Description, &tags, &active, &lastSynced, &repo.CreatedAt)
	if err != nil {
		return nil, err
	}
	repo.Active = active == 1
	repo.LastSynced = lastSynced
	_ = json.Unmarshal([]byte(tags), &repo.Tags)
	return repo, nil
}

func (r *RepoRepo) ListRepos(ctx context.Context, activeOnly bool) ([]*models.Repo, error) {
	query := `SELECT name, url, branch, description, tags, active, last_synced, created_at FROM repos`
	if activeOnly {
		query += ` WHERE active = 1`
	}
	query += ` ORDER BY name`

	rows, err := r.pool.Query(ctx, query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var repos []*models.Repo
	for rows.Next() {
		repo := &models.Repo{}
		var tags string
		var active int
		var lastSynced *time.Time
		err := rows.Scan(&repo.Name, &repo.URL, &repo.Branch, &repo.Description, &tags, &active, &lastSynced, &repo.CreatedAt)
		if err != nil {
			return nil, err
		}
		repo.Active = active == 1
		repo.LastSynced = lastSynced
		_ = json.Unmarshal([]byte(tags), &repo.Tags)
		repos = append(repos, repo)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return repos, nil
}

func (r *RepoRepo) UpdateRepo(ctx context.Context, in *repository.RepoInput) error {
	tags, _ := json.Marshal(in.Tags)
	active := 0
	if in.Active {
		active = 1
	}
	_, err := r.pool.Exec(ctx, `
		UPDATE repos SET url = $2, branch = $3, description = $4, tags = $5, active = $6
		WHERE name = $1`,
		in.Name, in.URL, in.Branch, in.Description, string(tags), active)
	return err
}

func (r *RepoRepo) PatchRepo(ctx context.Context, name string, patch map[string]interface{}) error {
	if len(patch) == 0 {
		return nil
	}
	setClauses := ""
	args := []interface{}{name}
	argIdx := 2
	for k, v := range patch {
		if setClauses != "" {
			setClauses += ", "
		}
		setClauses += fmt.Sprintf("%s = $%d", k, argIdx)
		args = append(args, v)
		argIdx++
	}
	query := fmt.Sprintf(`UPDATE repos SET %s WHERE name = $1`, setClauses)
	_, err := r.pool.Exec(ctx, query, args...)
	return err
}

func (r *RepoRepo) BulkUpdateRepos(ctx context.Context, repos []*repository.RepoInput) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback(ctx)
	for _, in := range repos {
		tags, _ := json.Marshal(in.Tags)
		active := 0
		if in.Active {
			active = 1
		}
		_, err := tx.Exec(ctx, `
			UPDATE repos SET url = $2, branch = $3, description = $4, tags = $5, active = $6
			WHERE name = $1`,
			in.Name, in.URL, in.Branch, in.Description, string(tags), active)
		if err != nil {
			return fmt.Errorf("update repo %s: %w", in.Name, err)
		}
	}
	return tx.Commit(ctx)
}

func (r *RepoRepo) SetRepoActive(ctx context.Context, name string, active bool) error {
	v := 0
	if active {
		v = 1
	}
	_, err := r.pool.Exec(ctx, `UPDATE repos SET active = $1 WHERE name = $2`, v, name)
	return err
}

func (r *RepoRepo) DeleteRepo(ctx context.Context, name string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM repos WHERE name = $1`, name)
	return err
}

// --- QueueRepository ---

// QueueRepo implements repository.QueueRepository.
type QueueRepo struct{ pool *pgxpool.Pool }

func NewQueueRepo(pool *pgxpool.Pool) *QueueRepo { return &QueueRepo{pool: pool} }

func (r *QueueRepo) EnqueueTicket(ctx context.Context, ticketID string, priority int) error {
	id := fmt.Sprintf("q-%s", ticketID)
	_, err := r.pool.Exec(ctx, `
		INSERT INTO queue (id, ticket_id, priority, created_at)
		VALUES ($1, $2, $3, NOW())`, id, ticketID, priority)
	return err
}

func (r *QueueRepo) GetQueue(ctx context.Context) ([]*models.QueueItem, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, ticket_id, agent_id, priority, created_at
		FROM queue ORDER BY priority DESC, created_at ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var items []*models.QueueItem
	for rows.Next() {
		item := &models.QueueItem{}
		err := rows.Scan(&item.ID, &item.TicketID, &item.AgentID, &item.Priority, &item.CreatedAt)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return items, nil
}

func (r *QueueRepo) DequeueItem(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM queue WHERE id = $1`, id)
	return err
}

func (r *QueueRepo) ClaimQueueItem(ctx context.Context, ticketID, agentID string) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback(ctx)

	var queueID string
	err = tx.QueryRow(ctx, `
		SELECT id FROM queue
		WHERE ticket_id = $1
		ORDER BY priority DESC, created_at ASC
		LIMIT 1
		FOR UPDATE SKIP LOCKED`, ticketID).Scan(&queueID)
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil
		}
		return fmt.Errorf("find queue item: %w", err)
	}

	_, err = tx.Exec(ctx, `DELETE FROM queue WHERE id = $1`, queueID)
	if err != nil {
		return fmt.Errorf("dequeue item: %w", err)
	}

	_, err = tx.Exec(ctx, `UPDATE tickets SET status = 'running', agent_id = $1, updated_at = NOW() WHERE id = $2`,
		agentID, ticketID)
	if err != nil {
		return fmt.Errorf("update ticket status: %w", err)
	}

	_, err = tx.Exec(ctx, `UPDATE agents SET status = 'running', current_task = $1, updated_at = NOW() WHERE id = $2`,
		ticketID, agentID)
	if err != nil {
		return fmt.Errorf("update agent status: %w", err)
	}

	return tx.Commit(ctx)
}

var (
	_ repository.RepoRepository  = (*RepoRepo)(nil)
	_ repository.QueueRepository = (*QueueRepo)(nil)
)