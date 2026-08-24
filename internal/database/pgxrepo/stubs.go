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

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/maelklingler/hivemind/internal/database/repository"
)

// --- PipelineRepository (stub — implemented in Phase 2 with migration 003) ---

// PipelineRepo implements repository.PipelineRepository.
type PipelineRepo struct{ pool *pgxpool.Pool }

func NewPipelineRepo(pool *pgxpool.Pool) *PipelineRepo { return &PipelineRepo{pool: pool} }

func (r *PipelineRepo) CreateStep(ctx context.Context, step *repository.PipelineStep) error {
	return fmt.Errorf("pipeline steps not implemented (migration 003 pending)")
}
func (r *PipelineRepo) GetStep(ctx context.Context, id string) (*repository.PipelineStep, error) {
	return nil, fmt.Errorf("pipeline steps not implemented (migration 003 pending)")
}
func (r *PipelineRepo) ListStepsByTicket(ctx context.Context, ticketID string) ([]*repository.PipelineStep, error) {
	return []*repository.PipelineStep{}, nil
}
func (r *PipelineRepo) UpdateStepStatus(ctx context.Context, id, status string) error {
	return fmt.Errorf("pipeline steps not implemented (migration 003 pending)")
}
func (r *PipelineRepo) AdvancePhase(ctx context.Context, ticketID string, currentPhase repository.Phase) (repository.Phase, error) {
	return "", fmt.Errorf("pipeline steps not implemented (migration 003 pending)")
}

// --- GroupRepository (stub — implemented in Phase 2 with migration 006) ---

// GroupRepo implements repository.GroupRepository.
type GroupRepo struct{ pool *pgxpool.Pool }

func NewGroupRepo(pool *pgxpool.Pool) *GroupRepo { return &GroupRepo{pool: pool} }

func (r *GroupRepo) CreateGroup(ctx context.Context, g *repository.Group) error {
	return fmt.Errorf("groups not implemented (migration 006 pending)")
}
func (r *GroupRepo) GetGroup(ctx context.Context, id string) (*repository.Group, error) {
	return nil, fmt.Errorf("groups not implemented (migration 006 pending)")
}
func (r *GroupRepo) ListGroups(ctx context.Context) ([]*repository.Group, error) {
	return []*repository.Group{}, nil
}
func (r *GroupRepo) DeleteGroup(ctx context.Context, id string) error {
	return fmt.Errorf("groups not implemented (migration 006 pending)")
}
func (r *GroupRepo) AddMessage(ctx context.Context, groupID, agentID, messageType, content string) error {
	return fmt.Errorf("groups not implemented (migration 006 pending)")
}
func (r *GroupRepo) ListMessages(ctx context.Context, groupID string) ([]*repository.GroupMessage, error) {
	return []*repository.GroupMessage{}, nil
}

// --- MemoryRepository (stub — implemented in Phase 2 with migration 005) ---

// MemoryRepo implements repository.MemoryRepository.
type MemoryRepo struct{ pool *pgxpool.Pool }

func NewMemoryRepo(pool *pgxpool.Pool) *MemoryRepo { return &MemoryRepo{pool: pool} }

func (r *MemoryRepo) ListBlocks(ctx context.Context, agentID string) ([]*repository.MemoryBlock, error) {
	return []*repository.MemoryBlock{}, nil
}
func (r *MemoryRepo) GetBlock(ctx context.Context, agentID, label string) (*repository.MemoryBlock, error) {
	return nil, fmt.Errorf("memory blocks not implemented (migration 005 pending)")
}
func (r *MemoryRepo) SetBlock(ctx context.Context, agentID string, in *repository.MemoryBlockInput) error {
	return fmt.Errorf("memory blocks not implemented (migration 005 pending)")
}
func (r *MemoryRepo) DeleteBlock(ctx context.Context, agentID, label string) error {
	return fmt.Errorf("memory blocks not implemented (migration 005 pending)")
}
func (r *MemoryRepo) SeedDefaults(ctx context.Context, agentID string) error {
	return fmt.Errorf("memory blocks not implemented (migration 005 pending)")
}

// --- PluginRepository (stub — implemented in Phase 2 with migration 004) ---

// PluginRepo implements repository.PluginRepository.
type PluginRepo struct{ pool *pgxpool.Pool }

func NewPluginRepo(pool *pgxpool.Pool) *PluginRepo { return &PluginRepo{pool: pool} }

func (r *PluginRepo) ListPlugins(ctx context.Context) ([]*repository.Plugin, error) {
	return []*repository.Plugin{}, nil
}
func (r *PluginRepo) CreatePlugin(ctx context.Context, in *repository.PluginInput) (string, error) {
	return "", fmt.Errorf("plugins not implemented (migration 004 pending)")
}
func (r *PluginRepo) UpdatePlugin(ctx context.Context, id string, in *repository.PluginInput) error {
	return fmt.Errorf("plugins not implemented (migration 004 pending)")
}
func (r *PluginRepo) DeletePlugin(ctx context.Context, id string) error {
	return fmt.Errorf("plugins not implemented (migration 004 pending)")
}

// --- InstructionRepository (stub — implemented in Phase 2 with migration 004) ---

// InstructionRepo implements repository.InstructionRepository.
type InstructionRepo struct{ pool *pgxpool.Pool }

func NewInstructionRepo(pool *pgxpool.Pool) *InstructionRepo { return &InstructionRepo{pool: pool} }

func (r *InstructionRepo) ListInstructions(ctx context.Context) ([]*repository.Instruction, error) {
	return []*repository.Instruction{}, nil
}
func (r *InstructionRepo) CreateInstruction(ctx context.Context, in *repository.InstructionInput) (string, error) {
	return "", fmt.Errorf("instructions not implemented (migration 004 pending)")
}
func (r *InstructionRepo) UpdateInstruction(ctx context.Context, id string, in *repository.InstructionInput) error {
	return fmt.Errorf("instructions not implemented (migration 004 pending)")
}
func (r *InstructionRepo) DeleteInstruction(ctx context.Context, id string) error {
	return fmt.Errorf("instructions not implemented (migration 004 pending)")
}

var (
	_ repository.PipelineRepository      = (*PipelineRepo)(nil)
	_ repository.GroupRepository         = (*GroupRepo)(nil)
	_ repository.MemoryRepository        = (*MemoryRepo)(nil)
	_ repository.PluginRepository        = (*PluginRepo)(nil)
	_ repository.InstructionRepository   = (*InstructionRepo)(nil)
)