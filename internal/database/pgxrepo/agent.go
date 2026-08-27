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

	"github.com/maelklingler/hivemind/internal/models"
)

// --- AgentRepository ---

// AgentRepo implements repository.AgentRepository.
type AgentRepo struct{ pool *pgxpool.Pool }

func NewAgentRepo(pool *pgxpool.Pool) *AgentRepo { return &AgentRepo{pool: pool} }

func (r *AgentRepo) CreateAgent(ctx context.Context, a *models.Agent) error {
	now := time.Now().UTC()
	a.CreatedAt = now
	a.UpdatedAt = now
	if a.Status == "" {
		a.Status = models.AgentIdle
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO agents (id, name, status, current_task, progress, last_seen, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
		a.ID, a.Name, string(a.Status), a.CurrentTask, a.Progress, a.LastSeen, a.CreatedAt, a.UpdatedAt)
	return err
}

func (r *AgentRepo) GetAgent(ctx context.Context, id string) (*models.Agent, error) {
	row := r.pool.QueryRow(ctx, `
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

func (r *AgentRepo) ListAgents(ctx context.Context) ([]*models.Agent, error) {
	rows, err := r.pool.Query(ctx, `
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

func (r *AgentRepo) SetAgentStatus(ctx context.Context, id, status string) error {
	_, err := r.pool.Exec(ctx, `UPDATE agents SET status = $1, updated_at = NOW() WHERE id = $2`, status, id)
	return err
}

func (r *AgentRepo) SetAgentIdle(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE agents SET status = 'idle', current_task = '', progress = '', updated_at = NOW() WHERE id = $1`, id)
	return err
}

func (r *AgentRepo) UpdateAgentProgress(ctx context.Context, id, progress string) error {
	_, err := r.pool.Exec(ctx, `UPDATE agents SET progress = $1, updated_at = NOW() WHERE id = $2`, progress, id)
	return err
}

func (r *AgentRepo) DeleteAgent(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM agents WHERE id = $1`, id)
	return err
}

func (r *AgentRepo) EnsureAgentPool(ctx context.Context, maxAgents int) error {
	agents, err := r.ListAgents(ctx)
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
		if err := r.CreateAgent(ctx, a); err != nil {
			return err
		}
	}
	return nil
}

// --- AgentProfileRepository ---

// AgentProfileRepo implements repository.AgentProfileRepository.
type AgentProfileRepo struct{ pool *pgxpool.Pool }

func NewAgentProfileRepo(pool *pgxpool.Pool) *AgentProfileRepo { return &AgentProfileRepo{pool: pool} }

func (r *AgentProfileRepo) ListAgentProfiles(ctx context.Context) ([]*models.AgentProfile, error) {
	rows, err := r.pool.Query(ctx, `SELECT id, name, description, skills, instructions, memory_summary FROM agent_profiles ORDER BY name`)
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

func (r *AgentProfileRepo) GetAgentProfile(ctx context.Context, id string) (*models.AgentProfile, error) {
	row := r.pool.QueryRow(ctx, `SELECT id, name, description, skills, instructions, memory_summary FROM agent_profiles WHERE id = $1`, id)
	p := &models.AgentProfile{}
	err := row.Scan(&p.ID, &p.Name, &p.Description, &p.Skills, &p.Instructions, &p.MemorySummary)
	if err != nil {
		return nil, err
	}
	return p, nil
}

func (r *AgentProfileRepo) CreateAgentProfile(ctx context.Context, p *models.AgentProfile) error {
	_, err := r.pool.Exec(ctx, `
		INSERT INTO agent_profiles (id, name, description, skills, instructions, memory_summary)
		VALUES ($1, $2, $3, $4, $5, $6)`,
		p.ID, p.Name, p.Description, p.Skills, p.Instructions, p.MemorySummary)
	return err
}

func (r *AgentProfileRepo) UpdateAgentProfile(ctx context.Context, p *models.AgentProfile) error {
	_, err := r.pool.Exec(ctx, `
		UPDATE agent_profiles SET name = $2, description = $3, skills = $4, instructions = $5, memory_summary = $6
		WHERE id = $1`,
		p.ID, p.Name, p.Description, p.Skills, p.Instructions, p.MemorySummary)
	return err
}

func (r *AgentProfileRepo) DeleteAgentProfile(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM agent_profiles WHERE id = $1`, id)
	return err
}

// --- AgentSkillRepository ---

// AgentSkillRepo implements repository.AgentSkillRepository.
type AgentSkillRepo struct{ pool *pgxpool.Pool }

func NewAgentSkillRepo(pool *pgxpool.Pool) *AgentSkillRepo { return &AgentSkillRepo{pool: pool} }

func (r *AgentSkillRepo) ListSkills(ctx context.Context, agentID string) ([]string, error) {
	rows, err := r.pool.Query(ctx, `SELECT skill FROM agent_skills WHERE agent_id = $1 ORDER BY skill`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var skills []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		skills = append(skills, s)
	}
	return skills, rows.Err()
}

func (r *AgentSkillRepo) AddSkill(ctx context.Context, agentID, skill string) error {
	id := fmt.Sprintf("skl-%s-%s-%d", agentID, skill, time.Now().UnixNano())
	_, err := r.pool.Exec(ctx, `INSERT INTO agent_skills (id, agent_id, skill) VALUES ($1, $2, $3) ON CONFLICT (agent_id, skill) DO NOTHING`, id, agentID, skill)
	return err
}

func (r *AgentSkillRepo) RemoveSkill(ctx context.Context, agentID, skill string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM agent_skills WHERE agent_id = $1 AND skill = $2`, agentID, skill)
	return err
}

func (r *AgentSkillRepo) ListAffinities(ctx context.Context, agentID string) (map[string]int, error) {
	rows, err := r.pool.Query(ctx, `SELECT repo_name, weight FROM agent_repo_affinities WHERE agent_id = $1`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	affinities := map[string]int{}
	for rows.Next() {
		var name string
		var weight int
		if err := rows.Scan(&name, &weight); err != nil {
			return nil, err
		}
		affinities[name] = weight
	}
	return affinities, rows.Err()
}

func (r *AgentSkillRepo) SetAffinity(ctx context.Context, agentID, repoName string, weight int) error {
	id := fmt.Sprintf("aff-%s-%s-%d", agentID, repoName, time.Now().UnixNano())
	_, err := r.pool.Exec(ctx, `
		INSERT INTO agent_repo_affinities (id, agent_id, repo_name, weight)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (agent_id, repo_name) DO UPDATE SET weight = EXCLUDED.weight`,
		id, agentID, repoName, weight)
	return err
}

func (r *AgentSkillRepo) ListInstructionAssignments(ctx context.Context, agentID string) ([]string, error) {
	rows, err := r.pool.Query(ctx, `SELECT instruction_id FROM agent_instruction_assignments WHERE agent_id = $1`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

func (r *AgentSkillRepo) AssignInstruction(ctx context.Context, agentID, instructionID string) error {
	id := fmt.Sprintf("asg-%s-%s-%d", agentID, instructionID, time.Now().UnixNano())
	_, err := r.pool.Exec(ctx, `INSERT INTO agent_instruction_assignments (id, agent_id, instruction_id) VALUES ($1, $2, $3) ON CONFLICT (agent_id, instruction_id) DO NOTHING`, id, agentID, instructionID)
	return err
}

func (r *AgentSkillRepo) UnassignInstruction(ctx context.Context, agentID, instructionID string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM agent_instruction_assignments WHERE agent_id = $1 AND instruction_id = $2`, agentID, instructionID)
	return err
}