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

package models

import "time"

type TicketStatus string

const (
	TicketQueued    TicketStatus = "queued"
	TicketRunning   TicketStatus = "running"
	TicketCompleted TicketStatus = "completed"
	TicketFailed    TicketStatus = "failed"
	TicketMerged    TicketStatus = "merged"
	TicketStopped   TicketStatus = "stopped"
)

type MRStatus string

const (
	MRNone     MRStatus = "none"
	MROpen     MRStatus = "open"
	MRMerged   MRStatus = "merged"
	MRRejected MRStatus = "rejected"
)

type ReviewStatus string

const (
	ReviewPending           ReviewStatus = "pending"
	ReviewApproved          ReviewStatus = "approved"
	ReviewChangesRequested ReviewStatus = "changes_requested"
)

type Ticket struct {
	ID                  string       `json:"id"`
	Title               string       `json:"title"`
	Description         string       `json:"description,omitempty"`
	Labels              []string     `json:"labels,omitempty"`
	IssueType           string       `json:"issue_type,omitempty"`
	Priority            string       `json:"priority,omitempty"`
	Status              TicketStatus `json:"status"`
	MRStatus            MRStatus     `json:"mr_status"`
	MRURL               string       `json:"mr_url,omitempty"`
	MRProjectPath       string       `json:"mr_project_path,omitempty"`
	MRIID               *int         `json:"mr_iid,omitempty"`
	ReviewStatus        ReviewStatus `json:"review_status"`
	ReviewNotes         string       `json:"review_notes,omitempty"`
	RetryCount          int          `json:"retry_count"`
	WorkspacePath       string       `json:"workspace_path,omitempty"`
	AgentID             string       `json:"agent_id,omitempty"`
	SelectedRepos       []string     `json:"selected_repos,omitempty"`
	PrimaryRepo         string       `json:"primary_repo,omitempty"`
	AIPlanning          string       `json:"ai_planning,omitempty"`
	Branch              string       `json:"branch,omitempty"`
	ModelUsed           string       `json:"model_used,omitempty"`
	LLMPromptTokens       int          `json:"llm_prompt_tokens"`
	LLMCompletionTokens   int          `json:"llm_completion_tokens"`
	LLMTotalCostUSD       float64      `json:"llm_total_cost_usd"`
	ReviewCycleCount      int          `json:"review_cycle_count"`
	FirstPipelineStatus   string       `json:"first_pipeline_status,omitempty"`
	MRPipelineStatus      string       `json:"mr_pipeline_status,omitempty"`
	MRConflictStatus      string       `json:"mr_conflict_status,omitempty"`
	MRLastNoteID          int          `json:"mr_last_note_id"`
	LinesAdded            int          `json:"lines_added"`
	LinesRemoved          int          `json:"lines_removed"`
	FilesChanged          int          `json:"files_changed"`
	PhaseWorkStartedAt    *time.Time   `json:"phase_work_started_at,omitempty"`
	PhaseTestStartedAt    *time.Time   `json:"phase_test_started_at,omitempty"`
	PhaseShipStartedAt    *time.Time   `json:"phase_ship_started_at,omitempty"`
	PhaseListenStartedAt  *time.Time   `json:"phase_listen_started_at,omitempty"`
	CompletedAt           *time.Time   `json:"completed_at,omitempty"`
	MergedAt              *time.Time   `json:"merged_at,omitempty"`
	CreatedAt             time.Time    `json:"created_at"`
	UpdatedAt             time.Time    `json:"updated_at"`
}

type AgentStatus string

const (
	AgentIdle    AgentStatus = "idle"
	AgentRunning AgentStatus = "running"
	AgentError   AgentStatus = "error"
	AgentStopped AgentStatus = "stopped"
)

type Agent struct {
	ID          string       `json:"id"`
	Name        string       `json:"name"`
	Status      AgentStatus  `json:"status"`
	CurrentTask string       `json:"current_task,omitempty"`
	Progress    string       `json:"progress,omitempty"`
	LastSeen    *time.Time   `json:"last_seen,omitempty"`
	CreatedAt   time.Time    `json:"created_at"`
	UpdatedAt   time.Time    `json:"updated_at"`
}

type Repo struct {
	Name        string   `json:"name"`
	URL         string   `json:"url"`
	Branch      string   `json:"branch"`
	Description string   `json:"description,omitempty"`
	Tags        []string `json:"tags,omitempty"`
	Active      bool     `json:"active"`
	LastSynced  *time.Time `json:"last_synced,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
}

type QueueItem struct {
	ID        string    `json:"id"`
	TicketID  string    `json:"ticket_id"`
	AgentID   string    `json:"agent_id,omitempty"`
	Priority  int       `json:"priority"`
	CreatedAt time.Time `json:"created_at"`
}

type MCPServer struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Command     string `json:"command"`
	Args         string `json:"args,omitempty"`
	Env         string `json:"env,omitempty"`
	ServerType  string `json:"server_type"`
	Enabled     bool   `json:"enabled"`
	Description string `json:"description,omitempty"`
}

type AgentProfile struct {
	ID            string `json:"id"`
	Name          string `json:"name"`
	Description   string `json:"description,omitempty"`
	Skills        string `json:"skills,omitempty"`
	Instructions  string `json:"instructions,omitempty"`
	MemorySummary string `json:"memory_summary,omitempty"`
}

type Setting struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

type MetricEvent struct {
	ID              string    `json:"id"`
	EventType       string    `json:"event_type"`
	TicketID        string    `json:"ticket_id,omitempty"`
	AgentID         string    `json:"agent_id,omitempty"`
	Phase           string    `json:"phase,omitempty"`
	DurationSeconds float64   `json:"duration_seconds,omitempty"`
	Labels          string    `json:"labels,omitempty"`
	Value           float64   `json:"value,omitempty"`
	CreatedAt       time.Time `json:"created_at"`
}

type TicketComment struct {
	ID        string    `json:"id"`
	TicketID  string    `json:"ticket_id"`
	Author    string    `json:"author"`
	Content   string    `json:"content"`
	CreatedAt time.Time `json:"created_at"`
}