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

// MemoryBlock represents a single agent memory block.
type MemoryBlock struct {
	ID          string    `json:"id"`
	AgentID     string    `json:"agent_id"`
	Label       string    `json:"label"`
	Content     string    `json:"content"`
	Description string    `json:"description,omitempty"`
	ReadOnly    bool      `json:"read_only"`
	BlockLimit  int       `json:"block_limit,omitempty"`
	RepoName    string    `json:"repo_name,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// MemoryBlockInput is the input for creating/updating a memory block.
type MemoryBlockInput struct {
	Label       string `json:"label"`
	Content     string `json:"content"`
	Description string `json:"description,omitempty"`
	ReadOnly    bool   `json:"read_only"`
	BlockLimit  int    `json:"block_limit,omitempty"`
	RepoName    string `json:"repo_name,omitempty"`
}

// MemoryRepository defines agent memory block operations.
type MemoryRepository interface {
	ListBlocks(ctx context.Context, agentID string) ([]*MemoryBlock, error)
	GetBlock(ctx context.Context, agentID, label string) (*MemoryBlock, error)
	SetBlock(ctx context.Context, agentID string, in *MemoryBlockInput) error
	DeleteBlock(ctx context.Context, agentID, label string) error
	SeedDefaults(ctx context.Context, agentID string) error
}