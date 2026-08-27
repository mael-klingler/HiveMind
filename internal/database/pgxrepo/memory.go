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

	"github.com/maelklingler/hivemind/internal/database/repository"
)

// --- MemoryRepository ---

// MemoryRepo implements repository.MemoryRepository.
type MemoryRepo struct{ pool *pgxpool.Pool }

func NewMemoryRepo(pool *pgxpool.Pool) *MemoryRepo { return &MemoryRepo{pool: pool} }

func (r *MemoryRepo) ListBlocks(ctx context.Context, agentID string) ([]*repository.MemoryBlock, error) {
	rows, err := r.pool.Query(ctx, `
		SELECT id, agent_id, label, content, description, read_only, block_limit, repo_name, created_at, updated_at
		FROM agent_memory_blocks WHERE agent_id = $1 ORDER BY label ASC`, agentID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var blocks []*repository.MemoryBlock
	for rows.Next() {
		b, err := scanBlock(rows)
		if err != nil {
			return nil, err
		}
		blocks = append(blocks, b)
	}
	return blocks, rows.Err()
}

func (r *MemoryRepo) GetBlock(ctx context.Context, agentID, label string) (*repository.MemoryBlock, error) {
	row := r.pool.QueryRow(ctx, `
		SELECT id, agent_id, label, content, description, read_only, block_limit, repo_name, created_at, updated_at
		FROM agent_memory_blocks WHERE agent_id = $1 AND label = $2`, agentID, label)
	return scanBlock(row)
}

func (r *MemoryRepo) SetBlock(ctx context.Context, agentID string, in *repository.MemoryBlockInput) error {
	id := fmt.Sprintf("mem-%s-%s-%d", agentID, in.Label, time.Now().UnixNano())
	readOnly := 0
	if in.ReadOnly {
		readOnly = 1
	}
	limit := in.BlockLimit
	if limit == 0 {
		limit = 5000
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO agent_memory_blocks (id, agent_id, label, content, description, read_only, block_limit, repo_name, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW(), NOW())
		ON CONFLICT (agent_id, label) DO UPDATE SET
			content = EXCLUDED.content, description = EXCLUDED.description, read_only = EXCLUDED.read_only,
			block_limit = EXCLUDED.block_limit, repo_name = EXCLUDED.repo_name, updated_at = NOW()`,
		id, agentID, in.Label, in.Content, in.Description, readOnly, limit, in.RepoName)
	return err
}

func (r *MemoryRepo) DeleteBlock(ctx context.Context, agentID, label string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM agent_memory_blocks WHERE agent_id = $1 AND label = $2`, agentID, label)
	return err
}

func (r *MemoryRepo) SeedDefaults(ctx context.Context, agentID string) error {
	defaults := []repository.MemoryBlockInput{
		{Label: "persona", Content: "You are an autonomous software developer. Work carefully and methodically.", Description: "Agent identity and behavior"},
		{Label: "human", Content: "Prefer English UI language. Use Conventional Commits. Tests are mandatory.", Description: "Operator preferences"},
		{Label: "project", Content: "Tech-Stack: Go Backend, Vue 3 Frontend. Tests: go test ./... , pnpm test.", Description: "Project conventions and architecture"},
	}
	for _, d := range defaults {
		if err := r.SetBlock(ctx, agentID, &d); err != nil {
			return err
		}
	}
	return nil
}

type blockScanner interface {
	Scan(dest ...interface{}) error
}

func scanBlock(row blockScanner) (*repository.MemoryBlock, error) {
	b := &repository.MemoryBlock{}
	var readOnly int
	err := row.Scan(&b.ID, &b.AgentID, &b.Label, &b.Content, &b.Description, &readOnly, &b.BlockLimit, &b.RepoName, &b.CreatedAt, &b.UpdatedAt)
	if err != nil {
		return nil, err
	}
	b.ReadOnly = readOnly == 1
	return b, nil
}

var _ repository.MemoryRepository = (*MemoryRepo)(nil)