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

package database

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/models"
)

//go:embed all:migrations
var migrationFS embed.FS

// MigrationsDir returns the path to SQL migration files.
// In development it uses the local source tree; in production containers
// it writes the embedded files to a temp directory and returns that path.
func MigrationsDir() (string, error) {
	local := filepath.Join("internal", "database", "migrations")
	if _, err := os.Stat(local); err == nil {
		return local, nil
	}

	container := "/app/migrations"
	if _, err := os.Stat(container); err == nil {
		return container, nil
	}

	tmpDir, err := os.MkdirTemp("", "hivemind-migrations")
	if err != nil {
		return "", fmt.Errorf("cannot create temp migrations dir: %w", err)
	}
	entries, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return "", fmt.Errorf("cannot read embedded migrations: %w", err)
	}
	for _, entry := range entries {
		data, err := migrationFS.ReadFile(filepath.Join("migrations", entry.Name()))
		if err != nil {
			return "", fmt.Errorf("cannot read embedded migration %s: %w", entry.Name(), err)
		}
		if err := os.WriteFile(filepath.Join(tmpDir, entry.Name()), data, 0644); err != nil {
			return "", fmt.Errorf("cannot write migration %s: %w", entry.Name(), err)
		}
	}
	return tmpDir, nil
}

type DB struct {
	pool *pgxpool.Pool
}

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

func (db *DB) Close() {
	db.pool.Close()
}

// Pool returns the underlying pgxpool for use by components that need direct pool access
// (e.g. mnesis.ProceduralMemory).
func (db *DB) Pool() *pgxpool.Pool {
	return db.pool
}

// --- Ticket operations ---

func (db *DB) CreateTicket(ctx context.Context, t *TicketInput) error {
	now := time.Now().UTC()
	status := "queued"
	labels, _ := json.Marshal(t.Labels)
	_, err := db.pool.Exec(ctx, `
		INSERT INTO tickets (id, title, description, labels, issue_type, priority, status,
			mr_status, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, 'none', $8, $8)`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority,
		status, now)
	return err
}

func (db *DB) CreateTicketAndEnqueue(ctx context.Context, t *TicketInput) error {
	tx, err := db.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback(ctx)

	now := time.Now().UTC()
	status := "queued"
	labels, _ := json.Marshal(t.Labels)
	ticketType := t.TicketType
	if ticketType == "" {
		ticketType = "task"
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO tickets (id, title, description, labels, issue_type, priority, status,
			mr_status, created_at, updated_at, ticket_type)
		VALUES ($1, $2, $3, $4, $5, $6, $7, 'none', $8, $8, $9)`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority,
		status, now, ticketType)
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

func (db *DB) GetTicket(ctx context.Context, id string) (*models.Ticket, error) {
	row := db.pool.QueryRow(ctx, `
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
			created_at, updated_at,
			parent_id, ticket_type, approval_status, approval_feedback, approval_required
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
		&t.CreatedAt, &t.UpdatedAt,
		&t.ParentID, &t.Type, &t.ApprovalStatus, &t.ApprovalFeedback, &t.ApprovalRequired)
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

func (db *DB) ListTickets(ctx context.Context, status string) ([]*models.Ticket, error) {
	return db.ListTicketsPaged(ctx, status, 0, 0)
}

// ListTicketsPaged returns tickets filtered by status with optional limit/offset.
// limit <= 0 means no limit.
func (db *DB) ListTicketsPaged(ctx context.Context, status string, limit, offset int) ([]*models.Ticket, error) {
	query := `SELECT id, title, description, labels, status, mr_status, mr_url, agent_id,
		retry_count, ticket_type, parent_id, created_at, updated_at FROM tickets`
	args := []interface{}{}
	if status != "" {
		query += ` WHERE status = $1`
		args = append(args, status)
	}
	query += ` ORDER BY created_at DESC`
	if limit > 0 {
		if len(args) == 0 {
			query += fmt.Sprintf(` LIMIT %d OFFSET %d`, limit, offset)
		} else {
			query += fmt.Sprintf(` LIMIT %d OFFSET %d`, limit, offset)
		}
	}

	rows, err := db.pool.Query(ctx, query, args...)
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
			&t.AgentID, &t.RetryCount, &t.Type, &t.ParentID, &t.CreatedAt, &t.UpdatedAt)
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

func (db *DB) UpdateTicketStatus(ctx context.Context, id string, status string) error {
	_, err := db.pool.Exec(ctx, `UPDATE tickets SET status = $1, updated_at = NOW() WHERE id = $2`,
		string(status), id)
	return err
}

func (db *DB) UpdateTicket(ctx context.Context, t *models.Ticket) error {
	labels, _ := json.Marshal(t.Labels)
	selectedRepos, _ := json.Marshal(t.SelectedRepos)
	_, err := db.pool.Exec(ctx, `
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

func (db *DB) DeleteTicket(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM tickets WHERE id = $1`, id)
	return err
}

func (db *DB) SetTicketAIPlanning(ctx context.Context, id string, planning string) error {
	_, err := db.pool.Exec(ctx, `UPDATE tickets SET ai_planning = $1, updated_at = NOW() WHERE id = $2`,
		planning, id)
	return err
}

func (db *DB) SetTicketMRURL(ctx context.Context, id, mrURL string) error {
	_, err := db.pool.Exec(ctx, `UPDATE tickets SET mr_url = $1, updated_at = NOW() WHERE id = $2`,
		mrURL, id)
	return err
}

func (db *DB) RequeueTicket(ctx context.Context, id string, maxRetries int) error {
	tx, err := db.pool.Begin(ctx)
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
	queueID := fmt.Sprintf("q-%s-retry-%d", id, retryCount+1)
	_, err = tx.Exec(ctx, `
		INSERT INTO queue (id, ticket_id, priority, created_at)
		VALUES ($1, $2, 0, NOW())
		ON CONFLICT (id) DO NOTHING`, queueID, id)
	if err != nil {
		return fmt.Errorf("re-enqueue ticket: %w", err)
	}
	return tx.Commit(ctx)
}

// --- Agent operations ---

func (db *DB) CreateAgent(ctx context.Context, a *models.Agent) error {
	now := time.Now().UTC()
	a.CreatedAt = now
	a.UpdatedAt = now
	if a.Status == "" {
		a.Status = models.AgentIdle
	}
	_, err := db.pool.Exec(ctx, `
		INSERT INTO agents (id, name, status, current_task, progress, last_seen, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		a.ID, a.Name, string(a.Status), a.CurrentTask, a.Progress, a.LastSeen, a.CreatedAt, a.UpdatedAt)
	return err
}

func (db *DB) GetAgent(ctx context.Context, id string) (*models.Agent, error) {
	row := db.pool.QueryRow(ctx, `
		SELECT id, name, status, current_task, progress, last_seen, created_at, updated_at
		FROM agents WHERE id = $1`, id)
	a := &models.Agent{}
	var status string
	err := row.Scan(&a.ID, &a.Name, &status, &a.CurrentTask, &a.Progress, &a.LastSeen, &a.CreatedAt, &a.UpdatedAt)
	if err != nil {
		return nil, err
	}
	a.Status = models.AgentStatus(status)
	return a, nil
}

func (db *DB) ListAgents(ctx context.Context) ([]*models.Agent, error) {
	rows, err := db.pool.Query(ctx, `
		SELECT id, name, status, current_task, progress, last_seen, created_at, updated_at
		FROM agents ORDER BY created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var agents []*models.Agent
	for rows.Next() {
		a := &models.Agent{}
		var status string
		err := rows.Scan(&a.ID, &a.Name, &status, &a.CurrentTask, &a.Progress, &a.LastSeen, &a.CreatedAt, &a.UpdatedAt)
		if err != nil {
			return nil, err
		}
		a.Status = models.AgentStatus(status)
		agents = append(agents, a)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return agents, nil
}

func (db *DB) SetAgentStatus(ctx context.Context, id string, status string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE agents SET status = $1, updated_at = NOW() WHERE id = $2`,
		string(status), id)
	return err
}

func (db *DB) SetAgentIdle(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE agents SET status = 'idle', current_task = '', progress = '', updated_at = NOW() WHERE id = $1`,
		id)
	return err
}

func (db *DB) UpdateAgentProgress(ctx context.Context, id string, progress string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE agents SET progress = $1, updated_at = NOW() WHERE id = $2`,
		progress, id)
	return err
}

func (db *DB) DeleteAgent(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM agents WHERE id = $1`, id)
	return err
}

func (db *DB) EnsureAgentPool(ctx context.Context, maxAgents int) error {
	agents, err := db.ListAgents(ctx)
	if err != nil {
		return err
	}
	current := len(agents)
	if current >= maxAgents {
		return nil
	}
	for i := current; i < maxAgents; i++ {
		a := &models.Agent{
			ID:   fmt.Sprintf("agent-%d", i+1),
			Name: fmt.Sprintf("Agent %d", i+1),
		}
		if err := db.CreateAgent(ctx, a); err != nil {
			return err
		}
	}
	return nil
}

// --- Repo operations ---

func (db *DB) AddRepo(ctx context.Context, r *RepoInput) error {
	now := time.Now().UTC()
	tags, _ := json.Marshal(r.Tags)
	active := 1
	if !r.Active {
		active = 0
	}
	_, err := db.pool.Exec(ctx, `
		INSERT INTO repos (name, url, branch, description, tags, active, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (name) DO UPDATE SET
			url = EXCLUDED.url, branch = EXCLUDED.branch,
			description = EXCLUDED.description, tags = EXCLUDED.tags`,
		r.Name, r.URL, r.Branch, r.Description, string(tags), active, now)
	return err
}

func (db *DB) GetRepo(ctx context.Context, name string) (*models.Repo, error) {
	row := db.pool.QueryRow(ctx, `
		SELECT name, url, branch, description, tags, active, last_synced, created_at
		FROM repos WHERE name = $1`, name)
	r := &models.Repo{}
	var tags string
	var active int
	var lastSynced *time.Time
	err := row.Scan(&r.Name, &r.URL, &r.Branch, &r.Description, &tags, &active, &lastSynced, &r.CreatedAt)
	if err != nil {
		return nil, err
	}
	r.Active = active == 1
	r.LastSynced = lastSynced
	_ = json.Unmarshal([]byte(tags), &r.Tags)
	return r, nil
}

func (db *DB) ListRepos(ctx context.Context, activeOnly bool) ([]*models.Repo, error) {
	query := `SELECT name, url, branch, description, tags, active, last_synced, created_at FROM repos`
	if activeOnly {
		query += ` WHERE active = 1`
	}
	query += ` ORDER BY name`

	rows, err := db.pool.Query(ctx, query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var repos []*models.Repo
	for rows.Next() {
		r := &models.Repo{}
		var tags string
		var active int
		var lastSynced *time.Time
		err := rows.Scan(&r.Name, &r.URL, &r.Branch, &r.Description, &tags, &active, &lastSynced, &r.CreatedAt)
		if err != nil {
			return nil, err
		}
		r.Active = active == 1
		r.LastSynced = lastSynced
		_ = json.Unmarshal([]byte(tags), &r.Tags)
		repos = append(repos, r)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return repos, nil
}

func (db *DB) DeleteRepo(ctx context.Context, name string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM repos WHERE name = $1`, name)
	return err
}

func (db *DB) UpdateRepoDB(ctx context.Context, r *RepoInput) error {
	tags, _ := json.Marshal(r.Tags)
	active := 0
	if r.Active {
		active = 1
	}
	_, err := db.pool.Exec(ctx, `
		UPDATE repos SET url = $2, branch = $3, description = $4, tags = $5, active = $6
		WHERE name = $1`,
		r.Name, r.URL, r.Branch, r.Description, string(tags), active)
	return err
}

func (db *DB) PatchRepoDB(ctx context.Context, name string, patch map[string]interface{}) error {
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
	_, err := db.pool.Exec(ctx, query, args...)
	return err
}

func (db *DB) SetRepoActiveDB(ctx context.Context, name string, active bool) error {
	v := 0
	if active {
		v = 1
	}
	_, err := db.pool.Exec(ctx, `UPDATE repos SET active = $1 WHERE name = $2`, v, name)
	return err
}

// --- Queue operations ---

func (db *DB) EnqueueTicket(ctx context.Context, ticketID string, priority int) error {
	id := fmt.Sprintf("q-%s", ticketID)
	_, err := db.pool.Exec(ctx, `
		INSERT INTO queue (id, ticket_id, priority, created_at)
		VALUES ($1, $2, $3, NOW())`, id, ticketID, priority)
	return err
}

func (db *DB) GetQueue(ctx context.Context) ([]*models.QueueItem, error) {
	rows, err := db.pool.Query(ctx, `
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

func (db *DB) DequeueItem(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM queue WHERE id = $1`, id)
	return err
}

func (db *DB) DequeueByTicketID(ctx context.Context, ticketID string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM queue WHERE ticket_id = $1`, ticketID)
	return err
}

func (db *DB) ClaimQueueItem(ctx context.Context, ticketID string, agentID string) error {
	tx, err := db.pool.Begin(ctx)
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

// --- Settings operations ---

func (db *DB) GetSetting(ctx context.Context, key string) (string, error) {
	var value string
	err := db.pool.QueryRow(ctx, `SELECT value FROM settings WHERE key = $1`, key).Scan(&value)
	if err != nil {
		return "", nil
	}
	return value, nil
}

func (db *DB) SetSetting(ctx context.Context, key, value string) error {
	_, err := db.pool.Exec(ctx, `
		INSERT INTO settings (key, value) VALUES ($1, $2)
		ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value`, key, value)
	return err
}

func (db *DB) GetAllSettings(ctx context.Context) (map[string]string, error) {
	rows, err := db.pool.Query(ctx, `SELECT key, value FROM settings ORDER BY key`)
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

// --- MCP Server operations ---

func (db *DB) ListMCPServers(ctx context.Context) ([]*models.MCPServer, error) {
	rows, err := db.pool.Query(ctx, `
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

func (db *DB) GetEnabledMCPServers(ctx context.Context) ([]*models.MCPServer, error) {
	rows, err := db.pool.Query(ctx, `
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

// --- Metrics summary ---

type MetricsSummary struct {
	TotalTickets        int     `json:"total_tickets"`
	CompletedTickets    int     `json:"completed_tickets"`
	FailedTickets       int     `json:"failed_tickets"`
	MergedTickets       int     `json:"merged_tickets"`
	TotalRetries        int     `json:"total_retries"`
	AvgReviewCycles     float64 `json:"avg_review_cycles"`
	TotalPromptTokens   int64   `json:"total_prompt_tokens"`
	TotalCompletionTokens int64 `json:"total_completion_tokens"`
	TotalLLMCostUSD     float64 `json:"total_llm_cost_usd"`
}

func (db *DB) GetMetricsSummary(ctx context.Context) (*MetricsSummary, error) {
	row := db.pool.QueryRow(ctx, `
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
	m := &MetricsSummary{}
	err := row.Scan(&m.TotalTickets, &m.CompletedTickets, &m.FailedTickets, &m.MergedTickets,
		&m.TotalRetries, &m.AvgReviewCycles, &m.TotalPromptTokens, &m.TotalCompletionTokens, &m.TotalLLMCostUSD)
	if err != nil {
		return nil, err
	}
	return m, nil
}

// --- Import repos from config ---

func (db *DB) ImportReposFromConfig(ctx context.Context, configPath string, repos []struct {
	Name        string `json:"name"`
	URL         string `json:"url"`
	Branch      string `json:"branch"`
	Description string `json:"description"`
	Tags        []string `json:"tags"`
}) error {
	for _, r := range repos {
		repo := &RepoInput{
			Name:        r.Name,
			URL:         r.URL,
			Branch:      r.Branch,
			Description: r.Description,
			Tags:        r.Tags,
			Active:      true,
		}
		if err := db.AddRepo(ctx, repo); err != nil {
			return err
		}
	}
	return nil
}

// --- Input types for API layer ---

type TicketInput struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	Description string   `json:"description"`
	Labels      []string `json:"labels"`
	IssueType   string   `json:"issue_type"`
	Priority    string   `json:"priority"`
	TicketType  string   `json:"ticket_type"`
}

type RepoInput struct {
	Name        string   `json:"name"`
	URL         string   `json:"url"`
	Branch      string   `json:"branch"`
	Description string   `json:"description"`
	Tags        []string `json:"tags"`
	Active      bool     `json:"active"`
}


// TicketFull is the full ticket struct returned by GetTicket (used by background processors)
type TicketFull = models.Ticket

func (db *DB) SetTicketReviewStatus(ctx context.Context, id, status, notes string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET review_status = $1, review_notes = $2, updated_at = NOW() WHERE id = $3`,
		status, notes, id)
	return err
}

func (db *DB) SetTicketCompletedAt(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET completed_at = NOW(), updated_at = NOW() WHERE id = $1`, id)
	return err
}

func (db *DB) SetTicketMRLastNoteID(ctx context.Context, id string, noteID int) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET mr_last_note_id = $1, updated_at = NOW() WHERE id = $2`,
		noteID, id)
	return err
}

func (db *DB) AddTicketComment(ctx context.Context, ticketID, author, commentType, content string) error {
	id := fmt.Sprintf("cmt-%s-%d", ticketID, time.Now().UnixNano())
	_, err := db.pool.Exec(ctx, `
		INSERT INTO ticket_comments (id, ticket_id, author, comment_type, content, created_at)
		VALUES ($1, $2, $3, $4, $5, NOW())`, id, ticketID, author, commentType, content)
	return err
}

func (db *DB) ListTicketComments(ctx context.Context, ticketID string) ([]*models.TicketComment, error) {
	rows, err := db.pool.Query(ctx, `
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

func (db *DB) ListOpenMRTickets(ctx context.Context) ([]*models.Ticket, error) {
	rows, err := db.pool.Query(ctx, `
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

// Status constants matching Python models
const (
	MRNone     = "none"
	MROpen     = "open"
	MRMerged   = "merged"
	MRRejected = "rejected"

	ReviewPending           = "pending"
	ReviewApproved          = "approved"
	ReviewChangesRequested = "changes_requested"
)

// --- Pipeline step helpers (delegate to pgxpool) ---

func (db *DB) CreatePipelineStep(ctx context.Context, step *repository.PipelineStep) error {
	_, err := db.pool.Exec(ctx, `
		INSERT INTO pipeline_steps (id, ticket_id, phase, status, role, agent_id, started_at, completed_at, retry_count, context, created_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())`,
		step.ID, step.TicketID, string(step.Phase), step.Status, step.Role, step.AgentID,
		step.StartedAt, step.CompletedAt, step.RetryCount, step.Context)
	return err
}

func (db *DB) ListPipelineSteps(ctx context.Context, ticketID string) ([]*repository.PipelineStep, error) {
	rows, err := db.pool.Query(ctx, `
		SELECT id, ticket_id, phase, status, role, agent_id, started_at, completed_at, retry_count, context, created_at
		FROM pipeline_steps WHERE ticket_id = $1 ORDER BY created_at ASC`, ticketID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var steps []*repository.PipelineStep
	for rows.Next() {
		s := &repository.PipelineStep{}
		var phase string
		var startedAt, completedAt *time.Time
		if err := rows.Scan(&s.ID, &s.TicketID, &phase, &s.Status, &s.Role, &s.AgentID,
			&startedAt, &completedAt, &s.RetryCount, &s.Context, &s.CreatedAt); err != nil {
			return nil, err
		}
		s.Phase = repository.Phase(phase)
		s.StartedAt = startedAt
		s.CompletedAt = completedAt
		steps = append(steps, s)
	}
	return steps, rows.Err()
}

func (db *DB) UpdateTicketPhaseTimestamp(ctx context.Context, id, phase string) error {
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
	_, err := db.pool.Exec(ctx, fmt.Sprintf(`UPDATE tickets SET %s = NOW(), updated_at = NOW() WHERE id = $1`, col), id)
	return err
}

// --- Group / team channel helpers ---

func (db *DB) AddGroupMessage(ctx context.Context, groupID, agentID, messageType, content string) error {
	id := fmt.Sprintf("msg-%s-%d", groupID, time.Now().UnixNano())
	_, err := db.pool.Exec(ctx, `
		INSERT INTO team_channel_messages (id, group_id, agent_id, content, message_type, created_at)
		VALUES ($1, $2, $3, $4, $5, NOW())`,
		id, groupID, agentID, content, messageType)
	return err
}

func (db *DB) ListGroupMessages(ctx context.Context, groupID string) ([]*repository.GroupMessage, error) {
	rows, err := db.pool.Query(ctx, `
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

// --- Agent profile helpers ---

func (db *DB) ListAgentProfilesDB(ctx context.Context) ([]*models.AgentProfile, error) {
	rows, err := db.pool.Query(ctx, `SELECT id, name, description, skills, instructions, memory_summary FROM agent_profiles ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var profiles []*models.AgentProfile
	for rows.Next() {
		p := &models.AgentProfile{}
		if err := rows.Scan(&p.ID, &p.Name, &p.Description, &p.Skills, &p.Instructions, &p.MemorySummary); err != nil {
			return nil, err
		}
		profiles = append(profiles, p)
	}
	return profiles, rows.Err()
}

func (db *DB) CreateAgentProfileDB(ctx context.Context, p *models.AgentProfile) error {
	_, err := db.pool.Exec(ctx, `
		INSERT INTO agent_profiles (id, name, description, skills, instructions, memory_summary)
		VALUES ($1, $2, $3, $4, $5, $6)
		ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
			skills = EXCLUDED.skills, instructions = EXCLUDED.instructions, memory_summary = EXCLUDED.memory_summary`,
		p.ID, p.Name, p.Description, p.Skills, p.Instructions, p.MemorySummary)
	return err
}

// --- Memory block helpers ---

func (db *DB) ListMemoryBlocksDB(ctx context.Context, agentID string) ([]*repository.MemoryBlock, error) {
	rows, err := db.pool.Query(ctx, `
		SELECT id, agent_id, label, content, description, read_only, block_limit, repo_name, created_at, updated_at
		FROM agent_memory_blocks WHERE agent_id = $1 ORDER BY label ASC`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var blocks []*repository.MemoryBlock
	for rows.Next() {
		b := &repository.MemoryBlock{}
		var readOnly int
		if err := rows.Scan(&b.ID, &b.AgentID, &b.Label, &b.Content, &b.Description, &readOnly, &b.BlockLimit, &b.RepoName, &b.CreatedAt, &b.UpdatedAt); err != nil {
			return nil, err
		}
		b.ReadOnly = readOnly == 1
		blocks = append(blocks, b)
	}
	return blocks, rows.Err()
}

func (db *DB) SetMemoryBlockDB(ctx context.Context, agentID string, in *repository.MemoryBlockInput) error {
	id := fmt.Sprintf("mem-%s-%s-%d", agentID, in.Label, time.Now().UnixNano())
	readOnly := 0
	if in.ReadOnly {
		readOnly = 1
	}
	limit := in.BlockLimit
	if limit == 0 {
		limit = 5000
	}
	_, err := db.pool.Exec(ctx, `
		INSERT INTO agent_memory_blocks (id, agent_id, label, content, description, read_only, block_limit, repo_name, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
		ON CONFLICT (agent_id, label) DO UPDATE SET
			content = EXCLUDED.content, description = EXCLUDED.description, read_only = EXCLUDED.read_only,
			block_limit = EXCLUDED.block_limit, repo_name = EXCLUDED.repo_name, updated_at = NOW()`,
		id, agentID, in.Label, in.Content, in.Description, readOnly, limit, in.RepoName)
	return err
}

func (db *DB) DeleteMemoryBlockDB(ctx context.Context, agentID, label string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM agent_memory_blocks WHERE agent_id = $1 AND label = $2`, agentID, label)
	return err
}

// --- MCP server CRUD helpers ---

func (db *DB) CreateMCPServerDB(ctx context.Context, in *repository.MCPServerInput) (string, error) {
	id := fmt.Sprintf("mcp-%d", time.Now().UnixNano())
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := db.pool.Exec(ctx, `
		INSERT INTO mcp_servers (id, name, command, args, env, server_type, enabled, description)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		id, in.Name, in.Command, in.Args, in.Env, in.ServerType, enabled, in.Description)
	if err != nil {
		return "", err
	}
	return id, nil
}

func (db *DB) UpdateMCPServerDB(ctx context.Context, id string, in *repository.MCPServerInput) error {
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := db.pool.Exec(ctx, `
		UPDATE mcp_servers SET name = $2, command = $3, args = $4, env = $5, server_type = $6, enabled = $7, description = $8
		WHERE id = $1`,
		id, in.Name, in.Command, in.Args, in.Env, in.ServerType, enabled, in.Description)
	return err
}

func (db *DB) DeleteMCPServerDB(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM mcp_servers WHERE id = $1`, id)
	return err
}

// --- Plugin CRUD helpers ---

func (db *DB) ListPluginsDB(ctx context.Context) ([]*repository.Plugin, error) {
	rows, err := db.pool.Query(ctx, `SELECT id, name, package, enabled, description, config FROM opencode_plugins ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var plugins []*repository.Plugin
	for rows.Next() {
		p := &repository.Plugin{}
		var enabled int
		if err := rows.Scan(&p.ID, &p.Name, &p.Package, &enabled, &p.Description, &p.Config); err != nil {
			return nil, err
		}
		p.Enabled = enabled == 1
		plugins = append(plugins, p)
	}
	return plugins, rows.Err()
}

func (db *DB) CreatePluginDB(ctx context.Context, in *repository.PluginInput) (string, error) {
	id := fmt.Sprintf("plg-%d", time.Now().UnixNano())
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	config := in.Config
	if config == "" {
		config = "{}"
	}
	_, err := db.pool.Exec(ctx, `
		INSERT INTO opencode_plugins (id, name, package, enabled, description, config, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())`,
		id, in.Name, in.Package, enabled, in.Description, config)
	if err != nil {
		return "", err
	}
	return id, nil
}

func (db *DB) UpdatePluginDB(ctx context.Context, id string, in *repository.PluginInput) error {
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := db.pool.Exec(ctx, `
		UPDATE opencode_plugins SET name = $2, package = $3, enabled = $4, description = $5, config = $6, updated_at = NOW()
		WHERE id = $1`,
		id, in.Name, in.Package, enabled, in.Description, in.Config)
	return err
}

func (db *DB) DeletePluginDB(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM opencode_plugins WHERE id = $1`, id)
	return err
}

// --- Instruction CRUD helpers ---

func (db *DB) ListInstructionsDB(ctx context.Context) ([]*repository.Instruction, error) {
	rows, err := db.pool.Query(ctx, `SELECT id, name, content, description, enabled FROM agent_instructions ORDER BY name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var instructions []*repository.Instruction
	for rows.Next() {
		i := &repository.Instruction{}
		var enabled int
		if err := rows.Scan(&i.ID, &i.Name, &i.Content, &i.Description, &enabled); err != nil {
			return nil, err
		}
		i.Enabled = enabled == 1
		instructions = append(instructions, i)
	}
	return instructions, rows.Err()
}

func (db *DB) CreateInstructionDB(ctx context.Context, in *repository.InstructionInput) (string, error) {
	id := fmt.Sprintf("ins-%d", time.Now().UnixNano())
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := db.pool.Exec(ctx, `
		INSERT INTO agent_instructions (id, name, content, description, enabled, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, NOW(), NOW())`,
		id, in.Name, in.Content, in.Description, enabled)
	if err != nil {
		return "", err
	}
	return id, nil
}

func (db *DB) UpdateInstructionDB(ctx context.Context, id string, in *repository.InstructionInput) error {
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := db.pool.Exec(ctx, `
		UPDATE agent_instructions SET name = $2, content = $3, description = $4, enabled = $5, updated_at = NOW()
		WHERE id = $1`,
		id, in.Name, in.Content, in.Description, enabled)
	return err
}

func (db *DB) DeleteInstructionDB(ctx context.Context, id string) error {
	_, err := db.pool.Exec(ctx, `DELETE FROM agent_instructions WHERE id = $1`, id)
	return err
}

// --- Telemetry helpers ---

func (db *DB) SetTicketLLMUsageDB(ctx context.Context, id string, promptTokens, completionTokens int, totalCostUSD float64) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET llm_prompt_tokens = llm_prompt_tokens + $1,
			llm_completion_tokens = llm_completion_tokens + $2,
			llm_total_cost_usd = llm_total_cost_usd + $3, updated_at = NOW() WHERE id = $4`,
		promptTokens, completionTokens, totalCostUSD, id)
	return err
}

func (db *DB) SetTicketLineStatsDB(ctx context.Context, id string, added, removed, filesChanged int) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET lines_added = $1, lines_removed = $2, files_changed = $3, updated_at = NOW() WHERE id = $4`,
		added, removed, filesChanged, id)
	return err
}

// --- Ticket hierarchy helpers ---

func (db *DB) CreateTicketAndEnqueueWithParent(ctx context.Context, t *TicketInput, parentID string) error {
	tx, err := db.pool.Begin(ctx)
	if err != nil {
		return fmt.Errorf("begin transaction: %w", err)
	}
	defer tx.Rollback(ctx)

	now := time.Now().UTC()
	status := "queued"
	labels, _ := json.Marshal(t.Labels)
	ticketType := "task"
	_, err = tx.Exec(ctx, `
		INSERT INTO tickets (id, title, description, labels, issue_type, priority, status,
			mr_status, created_at, updated_at, parent_id, ticket_type)
		VALUES ($1, $2, $3, $4, $5, $6, $7, 'none', $8, $8, $9, $10)`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority, status, now, parentID, ticketType)
	if err != nil {
		return fmt.Errorf("insert child ticket: %w", err)
	}
	queueID := fmt.Sprintf("q-%s", t.ID)
	_, err = tx.Exec(ctx, `INSERT INTO queue (id, ticket_id, priority, created_at) VALUES ($1, $2, 0, NOW())`, queueID, t.ID)
	if err != nil {
		return fmt.Errorf("enqueue child ticket: %w", err)
	}
	return tx.Commit(ctx)
}

func (db *DB) ListChildren(ctx context.Context, parentID string) ([]*models.Ticket, error) {
	rows, err := db.pool.Query(ctx, `
		SELECT id, title, description, labels, status, mr_status, mr_url, agent_id,
			retry_count, parent_id, ticket_type, approval_status, created_at, updated_at
		FROM tickets WHERE parent_id = $1 ORDER BY created_at ASC`, parentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var tickets []*models.Ticket
	for rows.Next() {
		t := &models.Ticket{}
		var labels, status, mrStatus string
		if err := rows.Scan(&t.ID, &t.Title, &t.Description, &labels, &status, &mrStatus,
			&t.MRURL, &t.AgentID, &t.RetryCount, &t.ParentID, &t.Type, &t.ApprovalStatus,
			&t.CreatedAt, &t.UpdatedAt); err != nil {
			return nil, err
		}
		t.Status = models.TicketStatus(status)
		t.MRStatus = models.MRStatus(mrStatus)
		_ = json.Unmarshal([]byte(labels), &t.Labels)
		tickets = append(tickets, t)
	}
	return tickets, rows.Err()
}

func (db *DB) ApproveTicket(ctx context.Context, id, feedback string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET approval_status = 'approved', approval_feedback = $2, status = 'queued', updated_at = NOW() WHERE id = $1`,
		id, feedback)
	return err
}

func (db *DB) RejectTicket(ctx context.Context, id, feedback string) error {
	_, err := db.pool.Exec(ctx, `
		UPDATE tickets SET approval_status = 'rejected', approval_feedback = $2, status = 'failed', updated_at = NOW() WHERE id = $1`,
		id, feedback)
	return err
}

func (db *DB) SetApprovalRequired(ctx context.Context, id string, required bool) error {
	v := 0
	if required {
		v = 1
	}
	_, err := db.pool.Exec(ctx, `UPDATE tickets SET approval_required = $1, approval_status = 'pending', updated_at = NOW() WHERE id = $2`, v, id)
	return err
}

func (db *DB) ListPendingApprovals(ctx context.Context) ([]*models.Ticket, error) {
	rows, err := db.pool.Query(ctx, `
		SELECT id, title, description, status, approval_status, approval_feedback, parent_id, ticket_type, created_at, updated_at
		FROM tickets WHERE approval_status = 'pending' ORDER BY created_at ASC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var tickets []*models.Ticket
	for rows.Next() {
		t := &models.Ticket{}
		var status string
		if err := rows.Scan(&t.ID, &t.Title, &t.Description, &status, &t.ApprovalStatus,
			&t.ApprovalFeedback, &t.ParentID, &t.Type, &t.CreatedAt, &t.UpdatedAt); err != nil {
			return nil, err
		}
		t.Status = models.TicketStatus(status)
		tickets = append(tickets, t)
	}
	return tickets, rows.Err()
}

func (db *DB) AreAllChildrenCompleted(ctx context.Context, parentID string) (bool, error) {
	var count int
	err := db.pool.QueryRow(ctx, `
		SELECT COUNT(*) FROM tickets WHERE parent_id = $1 AND status NOT IN ('completed', 'merged', 'failed')`, parentID).Scan(&count)
	if err != nil {
		return false, err
	}
	return count == 0, nil
}

func (db *DB) HasChildren(ctx context.Context, parentID string) (bool, error) {
	var count int
	err := db.pool.QueryRow(ctx, `SELECT COUNT(*) FROM tickets WHERE parent_id = $1`, parentID).Scan(&count)
	if err != nil {
		return false, err
	}
	return count > 0, nil
} 