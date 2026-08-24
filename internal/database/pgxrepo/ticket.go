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

// Package pgxrepo implements the repository interfaces using pgxpool.
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

// DB is the pgx-backed repository container. Each domain repository embeds the
// shared pool.
type DB struct {
	pool *pgxpool.Pool
}

// New creates a new pgx repository container.
func New(ctx context.Context, databaseURL string) (*DB, error) {
	config, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return nil, fmt.Errorf("parse database URL: %w", err)
	}
	config.MaxConns = 10
	config.MinConns = 2
	config.HealthCheckPeriod = 30 * time.Second

	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("connect to database: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping database: %w", err)
	}
	return &DB{pool: pool}, nil
}

// Close closes the pool.
func (db *DB) Close() {
	db.pool.Close()
}

// Pool returns the underlying pool (used by migration runner and tx helpers).
func (db *DB) Pool() *pgxpool.Pool {
	return db.pool
}

// --- TicketRepository ---

// TicketRepo implements repository.TicketRepository.
type TicketRepo struct{ pool *pgxpool.Pool }

func NewTicketRepo(pool *pgxpool.Pool) *TicketRepo { return &TicketRepo{pool: pool} }

func (r *TicketRepo) CreateTicket(ctx context.Context, t *repository.TicketInput) error {
	now := time.Now().UTC()
	status := "queued"
	labels, _ := json.Marshal(t.Labels)
	_, err := r.pool.Exec(ctx, `
		INSERT INTO tickets (id, title, description, labels, issue_type, priority, status,
			mr_status, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, 'none', $8, $8)`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority, status, now)
	return err
}

func (r *TicketRepo) CreateTicketAndEnqueue(ctx context.Context, t *repository.TicketInput) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback(ctx)

	now := time.Now().UTC()
	status := "queued"
	labels, _ := json.Marshal(t.Labels)

	_, err = tx.Exec(ctx, `
		INSERT INTO tickets (id, title, description, labels, issue_type, priority, status,
			mr_status, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, 'none', $8, $8)`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority, status, now)
	if err != nil {
		return fmt.Errorf("insert ticket: %w", err)
	}

	queueID := fmt.Sprintf("q-%s", t.ID)
	_, err = tx.Exec(ctx, `
		INSERT INTO queue (id, ticket_id, priority, created_at)
		VALUES ($1, $2, 0, NOW())`, queueID, t.ID)
	if err != nil {
		return fmt.Errorf("enqueue ticket: %w", err)
	}
	return tx.Commit(ctx)
}

func (r *TicketRepo) GetTicket(ctx context.Context, id string) (*models.Ticket, error) {
	row := r.pool.QueryRow(ctx, `
		SELECT id, title, description, labels, issue_type, priority, status,
			mr_status, mr_url, mr_project_path, mr_iid, review_status, review_notes,
			retry_count, workspace_path, agent_id, selected_repos, primary_repo,
			ai_planning, branch, model_used,
			llm_prompt_tokens, llm_completion_tokens, llm_total_cost_usd,
			review_cycle_count, first_pipeline_status, mr_pipeline_status,
			mr_conflict_status, mr_last_note_id,
			phase_work_started_at, phase_test_started_at, phase_ship_started_at,
			phase_listen_started_at, completed_at, merged_at,
			lines_added, lines_removed, files_changed,
			created_at, updated_at
		FROM tickets WHERE id = $1`, id)

	t := &models.Ticket{}
	var labels, selectedRepos string
	var status, mrStatus, reviewStatus string
	var phaseWork, phaseTest, phaseShip, phaseListen, completedAt, mergedAt *time.Time
	var mrIID *int

	err := row.Scan(&t.ID, &t.Title, &t.Description, &labels, &t.IssueType, &t.Priority,
		&status, &mrStatus, &t.MRURL, &t.MRProjectPath, &mrIID, &reviewStatus, &t.ReviewNotes,
		&t.RetryCount, &t.WorkspacePath, &t.AgentID, &selectedRepos, &t.PrimaryRepo,
		&t.AIPlanning, &t.Branch, &t.ModelUsed,
		&t.LLMPromptTokens, &t.LLMCompletionTokens, &t.LLMTotalCostUSD,
		&t.ReviewCycleCount, &t.FirstPipelineStatus, &t.MRPipelineStatus,
		&t.MRConflictStatus, &t.MRLastNoteID,
		&phaseWork, &phaseTest, &phaseShip, &phaseListen, &completedAt, &mergedAt,
		&t.LinesAdded, &t.LinesRemoved, &t.FilesChanged,
		&t.CreatedAt, &t.UpdatedAt)
	if err != nil {
		return nil, err
	}

	t.Status = models.TicketStatus(status)
	t.MRStatus = models.MRStatus(mrStatus)
	t.ReviewStatus = models.ReviewStatus(reviewStatus)
	t.MRIID = mrIID
	t.PhaseWorkStartedAt = phaseWork
	t.PhaseTestStartedAt = phaseTest
	t.PhaseShipStartedAt = phaseShip
	t.PhaseListenStartedAt = phaseListen
	t.CompletedAt = completedAt
	t.MergedAt = mergedAt
	_ = json.Unmarshal([]byte(labels), &t.Labels)
	_ = json.Unmarshal([]byte(selectedRepos), &t.SelectedRepos)
	return t, nil
}

func (r *TicketRepo) ListTickets(ctx context.Context, status string, limit, offset int) ([]*models.Ticket, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	if offset < 0 {
		offset = 0
	}
	query := `SELECT id, title, description, labels, status, mr_status, mr_url, agent_id,
		retry_count, created_at, updated_at FROM tickets`
	args := []interface{}{}
	if status != "" {
		query += ` WHERE status = $1`
		args = append(args, status)
		query += fmt.Sprintf(` ORDER BY created_at DESC LIMIT %d OFFSET %d`, limit, offset)
	} else {
		query += fmt.Sprintf(` ORDER BY created_at DESC LIMIT %d OFFSET %d`, limit, offset)
	}

	rows, err := r.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var tickets []*models.Ticket
	for rows.Next() {
		t := &models.Ticket{}
		var labels string
		var s, ms string
		err := rows.Scan(&t.ID, &t.Title, &t.Description, &labels, &s, &ms, &t.MRURL,
			&t.AgentID, &t.RetryCount, &t.CreatedAt, &t.UpdatedAt)
		if err != nil {
			return nil, err
		}
		t.Status = models.TicketStatus(s)
		t.MRStatus = models.MRStatus(ms)
		_ = json.Unmarshal([]byte(labels), &t.Labels)
		tickets = append(tickets, t)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return tickets, nil
}

func (r *TicketRepo) UpdateTicketStatus(ctx context.Context, id, status string) error {
	_, err := r.pool.Exec(ctx, `UPDATE tickets SET status = $1, updated_at = NOW() WHERE id = $2`,
		status, id)
	return err
}

func (r *TicketRepo) UpdateTicket(ctx context.Context, t *models.Ticket) error {
	labels, _ := json.Marshal(t.Labels)
	selectedRepos, _ := json.Marshal(t.SelectedRepos)
	_, err := r.pool.Exec(ctx, `
		UPDATE tickets SET
			title = $2, description = $3, labels = $4, issue_type = $5, priority = $6,
			status = $7, mr_status = $8, mr_url = $9, mr_project_path = $10, mr_iid = $11,
			review_status = $12, review_notes = $13, retry_count = $14, workspace_path = $15,
			agent_id = $16, selected_repos = $17, primary_repo = $18, ai_planning = $19,
			branch = $20, model_used = $21, updated_at = NOW()
		WHERE id = $1`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority,
		string(t.Status), string(t.MRStatus), t.MRURL, t.MRProjectPath, t.MRIID,
		string(t.ReviewStatus), t.ReviewNotes, t.RetryCount, t.WorkspacePath,
		t.AgentID, string(selectedRepos), t.PrimaryRepo, t.AIPlanning,
		t.Branch, t.ModelUsed)
	return err
}

func (r *TicketRepo) DeleteTicket(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM tickets WHERE id = $1`, id)
	return err
}

func (r *TicketRepo) SetTicketAIPlanning(ctx context.Context, id, planning string) error {
	_, err := r.pool.Exec(ctx, `UPDATE tickets SET ai_planning = $1, updated_at = NOW() WHERE id = $2`,
		planning, id)
	return err
}

func (r *TicketRepo) SetTicketMRURL(ctx context.Context, id, mrURL string) error {
	_, err := r.pool.Exec(ctx, `UPDATE tickets SET mr_url = $1, updated_at = NOW() WHERE id = $2`,
		mrURL, id)
	return err
}

func (r *TicketRepo) SetTicketReviewStatus(ctx context.Context, id, status, notes string) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE tickets SET review_status = $1, review_notes = $2, updated_at = NOW() WHERE id = $3`,
		status, notes, id)
	return err
}

func (r *TicketRepo) SetTicketCompletedAt(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `UPDATE tickets SET completed_at = NOW(), updated_at = NOW() WHERE id = $1`, id)
	return err
}

func (r *TicketRepo) SetTicketMRLastNoteID(ctx context.Context, id string, noteID int) error {
	_, err := r.pool.Exec(ctx, `UPDATE tickets SET mr_last_note_id = $1, updated_at = NOW() WHERE id = $2`,
		noteID, id)
	return err
}

func (r *TicketRepo) SetTicketLLMUsage(ctx context.Context, id string, promptTokens, completionTokens int, totalCostUSD float64) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE tickets SET llm_prompt_tokens = llm_prompt_tokens + $1,
			llm_completion_tokens = llm_completion_tokens + $2,
			llm_total_cost_usd = llm_total_cost_usd + $3, updated_at = NOW() WHERE id = $4`,
		promptTokens, completionTokens, totalCostUSD, id)
	return err
}

func (r *TicketRepo) SetTicketLineStats(ctx context.Context, id string, added, removed, filesChanged int) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE tickets SET lines_added = $1, lines_removed = $2, files_changed = $3, updated_at = NOW() WHERE id = $4`,
		added, removed, filesChanged, id)
	return err
}

func (r *TicketRepo) UpdateTicketPhaseTimestamp(ctx context.Context, id, phase string) error {
	col := ""
	switch phase {
	case "work":
		col = "phase_work_started_at"
	case "test":
		col = "phase_test_started_at"
	case "ship":
		col = "phase_ship_started_at"
	case "listen":
		col = "phase_listen_started_at"
	default:
		return fmt.Errorf("unknown phase: %s", phase)
	}
	_, err := r.pool.Exec(ctx, fmt.Sprintf(`UPDATE tickets SET %s = NOW(), updated_at = NOW() WHERE id = $1`, col), id)
	return err
}

func (r *TicketRepo) IncrementReviewCycleCount(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `UPDATE tickets SET review_cycle_count = review_cycle_count + 1, updated_at = NOW() WHERE id = $1`, id)
	return err
}

func (r *TicketRepo) RequeueTicket(ctx context.Context, id string, maxRetries int) error {
	tx, err := r.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback(ctx)

	var retryCount int
	err = tx.QueryRow(ctx, `SELECT retry_count FROM tickets WHERE id = $1 FOR UPDATE`, id).Scan(&retryCount)
	if err != nil {
		return fmt.Errorf("select ticket for requeue: %w", err)
	}
	if retryCount >= maxRetries {
		_, err = tx.Exec(ctx, `UPDATE tickets SET status = 'failed', updated_at = NOW() WHERE id = $1`, id)
		if err != nil {
			return err
		}
		return tx.Commit(ctx)
	}
	_, err = tx.Exec(ctx, `
		UPDATE tickets SET status = 'queued', retry_count = retry_count + 1,
			mr_status = 'none', review_status = 'pending', agent_id = '', updated_at = NOW()
		WHERE id = $1`, id)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (r *TicketRepo) ListOpenMRTickets(ctx context.Context) ([]*models.Ticket, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, title, description, labels, status, mr_status, mr_url, mr_project_path, mr_iid,
			review_status, review_notes, retry_count, agent_id, selected_repos, primary_repo,
			mr_pipeline_status, mr_conflict_status, mr_last_note_id,
			created_at, updated_at
		FROM tickets
		WHERE mr_status = 'open' AND status IN ('running', 'queued')
		ORDER BY created_at ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var tickets []*models.Ticket
	for rows.Next() {
		t := &models.Ticket{}
		var labels, selectedRepos, status, mrStatus, reviewStatus string
		var mrIID *int
		var mrPipelineStatus, mrConflictStatus string
		var mrLastNoteID int
		err := rows.Scan(&t.ID, &t.Title, &t.Description, &labels, &status, &mrStatus, &t.MRURL,
			&t.MRProjectPath, &mrIID, &reviewStatus, &t.ReviewNotes, &t.RetryCount,
			&t.AgentID, &selectedRepos, &t.PrimaryRepo, &mrPipelineStatus, &mrConflictStatus,
			&mrLastNoteID, &t.CreatedAt, &t.UpdatedAt)
		if err != nil {
			return nil, err
		}
		t.Status = models.TicketStatus(status)
		t.MRStatus = models.MRStatus(mrStatus)
		t.ReviewStatus = models.ReviewStatus(reviewStatus)
		t.MRIID = mrIID
		t.MRPipelineStatus = mrPipelineStatus
		t.MRConflictStatus = mrConflictStatus
		t.MRLastNoteID = mrLastNoteID
		_ = json.Unmarshal([]byte(labels), &t.Labels)
		_ = json.Unmarshal([]byte(selectedRepos), &t.SelectedRepos)
		tickets = append(tickets, t)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return tickets, nil
}

// --- CommentRepository ---

// CommentRepo implements repository.CommentRepository.
type CommentRepo struct{ pool *pgxpool.Pool }

func NewCommentRepo(pool *pgxpool.Pool) *CommentRepo { return &CommentRepo{pool: pool} }

func (r *CommentRepo) AddTicketComment(ctx context.Context, ticketID, author, commentType, content string) error {
	id := fmt.Sprintf("cmt-%s-%d", ticketID, time.Now().UnixNano())
	_, err := r.pool.Exec(ctx, `
		INSERT INTO ticket_comments (id, ticket_id, author, comment_type, content, created_at)
		VALUES ($1, $2, $3, $4, $5, NOW())`, id, ticketID, author, commentType, content)
	return err
}

func (r *CommentRepo) ListTicketComments(ctx context.Context, ticketID string) ([]*models.TicketComment, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, ticket_id, author, content, created_at
		FROM ticket_comments WHERE ticket_id = $1 ORDER BY created_at ASC`, ticketID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var comments []*models.TicketComment
	for rows.Next() {
		c := &models.TicketComment{}
		if err := rows.Scan(&c.ID, &c.TicketID, &c.Author, &c.Content, &c.CreatedAt); err != nil {
			return nil, err
		}
		comments = append(comments, c)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return comments, nil
}

var (
	_ repository.TicketRepository  = (*TicketRepo)(nil)
	_ repository.CommentRepository = (*CommentRepo)(nil)
	_ = pgx.ErrNoRows
)