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

	"github.com/maelklingler/hivemind/internal/models"
)

// AgentRepository defines agent persistence operations.
type AgentRepository interface {
	CreateAgent(ctx context.Context, a *models.Agent) error
	GetAgent(ctx context.Context, id string) (*models.Agent, error)
	ListAgents(ctx context.Context) ([]*models.Agent, error)
	SetAgentStatus(ctx context.Context, id, status string) error
	SetAgentIdle(ctx context.Context, id string) error
	UpdateAgentProgress(ctx context.Context, id, progress string) error
	DeleteAgent(ctx context.Context, id string) error
	EnsureAgentPool(ctx context.Context, maxAgents int) error
}

// AgentProfileRepository defines agent profile operations.
type AgentProfileRepository interface {
	ListAgentProfiles(ctx context.Context) ([]*models.AgentProfile, error)
	GetAgentProfile(ctx context.Context, id string) (*models.AgentProfile, error)
	CreateAgentProfile(ctx context.Context, p *models.AgentProfile) error
	UpdateAgentProfile(ctx context.Context, p *models.AgentProfile) error
	DeleteAgentProfile(ctx context.Context, id string) error
}

// AgentSkillRepository defines agent skill/affinity/instruction-assignment operations.
type AgentSkillRepository interface {
	ListSkills(ctx context.Context, agentID string) ([]string, error)
	AddSkill(ctx context.Context, agentID, skill string) error
	RemoveSkill(ctx context.Context, agentID, skill string) error
	ListAffinities(ctx context.Context, agentID string) (map[string]int, error)
	SetAffinity(ctx context.Context, agentID, repoName string, weight int) error
	ListInstructionAssignments(ctx context.Context, agentID string) ([]string, error)
	AssignInstruction(ctx context.Context, agentID, instructionID string) error
	UnassignInstruction(ctx context.Context, agentID, instructionID string) error
}