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

	_, err = tx.Exec(ctx, `
		INSERT INTO tickets (id, title, description, labels, issue_type, priority, status,
			mr_status, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, 'none', $8, $8)`,
		t.ID, t.Title, t.Description, string(labels), t.IssueType, t.Priority,
		status, now)
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

func (db *DB) ListTickets(ctx context.Context, status string) ([]*models.Ticket, error) {
	query := `SELECT id, title, description, labels, status, mr_status, mr_url, agent_id,
		retry_count, created_at, updated_at FROM tickets`
	args := []interface{}{}
	if status != "" {
		query += ` WHERE status = $1`
		args = append(args, status)
	}
	query += ` ORDER BY created_at DESC`

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
	t, err := db.GetTicket(ctx, id)
	if err != nil {
		return err
	}
	if t.RetryCount >= maxRetries {
		_, err = db.pool.Exec(ctx, `UPDATE tickets SET status = 'failed', updated_at = NOW() WHERE id = $1`, id)
		return err
	}
	_, err = db.pool.Exec(ctx, `
		UPDATE tickets SET status = 'queued', retry_count = retry_count + 1,
			mr_status = 'none', review_status = 'pending', agent_id = '', updated_at = NOW()
		WHERE id = $1`, id)
	return err
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