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

package repository

import (
	"context"
	"time"
)

// Phase represents a pipeline phase definition.
type Phase string

const (
	PhaseWork   Phase = "work"
	PhaseTest   Phase = "test"
	PhaseReview Phase = "review"
	PhaseShip   Phase = "ship"
	PhaseListen Phase = "listen"
)

// PipelineStep represents a single step in a ticket's pipeline.
type PipelineStep struct {
	ID          string     `json:"id"`
	TicketID    string     `json:"ticket_id"`
	Phase       Phase      `json:"phase"`
	Status      string     `json:"status"`
	Role        string     `json:"role,omitempty"`
	AgentID     string     `json:"agent_id,omitempty"`
	StartedAt   *time.Time `json:"started_at,omitempty"`
	CompletedAt *time.Time `json:"completed_at,omitempty"`
	RetryCount  int        `json:"retry_count"`
	Context     string     `json:"context,omitempty"`
	CreatedAt   time.Time  `json:"created_at"`
}

// PipelineRepository defines pipeline step operations.
type PipelineRepository interface {
	CreateStep(ctx context.Context, step *PipelineStep) error
	GetStep(ctx context.Context, id string) (*PipelineStep, error)
	ListStepsByTicket(ctx context.Context, ticketID string) ([]*PipelineStep, error)
	UpdateStepStatus(ctx context.Context, id, status string) error
	AdvancePhase(ctx context.Context, ticketID string, currentPhase Phase) (Phase, error)
}

// Group represents a ticket group for team collaboration.
type Group struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description,omitempty"`
	TicketIDs   []string  `json:"ticket_ids"`
	CreatedAt   time.Time `json:"created_at"`
}

// GroupMessage represents a message in a group channel.
type GroupMessage struct {
	ID          string    `json:"id"`
	GroupID     string    `json:"group_id"`
	AgentID     string    `json:"agent_id,omitempty"`
	Content     string    `json:"content"`
	MessageType string    `json:"message_type"`
	CreatedAt   time.Time `json:"created_at"`
}

// GroupRepository defines group + team-channel operations.
type GroupRepository interface {
	CreateGroup(ctx context.Context, g *Group) error
	GetGroup(ctx context.Context, id string) (*Group, error)
	ListGroups(ctx context.Context) ([]*Group, error)
	DeleteGroup(ctx context.Context, id string) error
	AddMessage(ctx context.Context, groupID, agentID, messageType, content string) error
	ListMessages(ctx context.Context, groupID string) ([]*GroupMessage, error)
}