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

// --- PluginRepository ---

// PluginRepo implements repository.PluginRepository.
type PluginRepo struct{ pool *pgxpool.Pool }

func NewPluginRepo(pool *pgxpool.Pool) *PluginRepo { return &PluginRepo{pool: pool} }

func (r *PluginRepo) ListPlugins(ctx context.Context) ([]*repository.Plugin, error) {
	rows, err := r.pool.Query(ctx, `SELECT id, name, package, enabled, description, config FROM opencode_plugins ORDER BY name`)
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

func (r *PluginRepo) CreatePlugin(ctx context.Context, in *repository.PluginInput) (string, error) {
	id := fmt.Sprintf("plg-%d", time.Now().UnixNano())
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO opencode_plugins (id, name, package, enabled, description, config, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())`,
		id, in.Name, in.Package, enabled, in.Description, in.Config)
	if err != nil {
		return "", err
	}
	return id, nil
}

func (r *PluginRepo) UpdatePlugin(ctx context.Context, id string, in *repository.PluginInput) error {
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := r.pool.Exec(ctx, `
		UPDATE opencode_plugins SET name = $2, package = $3, enabled = $4, description = $5, config = $6, updated_at = NOW()
		WHERE id = $1`,
		id, in.Name, in.Package, enabled, in.Description, in.Config)
	return err
}

func (r *PluginRepo) DeletePlugin(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM opencode_plugins WHERE id = $1`, id)
	return err
}

// --- InstructionRepository ---

// InstructionRepo implements repository.InstructionRepository.
type InstructionRepo struct{ pool *pgxpool.Pool }

func NewInstructionRepo(pool *pgxpool.Pool) *InstructionRepo { return &InstructionRepo{pool: pool} }

func (r *InstructionRepo) ListInstructions(ctx context.Context) ([]*repository.Instruction, error) {
	rows, err := r.pool.Query(ctx, `SELECT id, name, content, description, enabled FROM agent_instructions ORDER BY name`)
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

func (r *InstructionRepo) CreateInstruction(ctx context.Context, in *repository.InstructionInput) (string, error) {
	id := fmt.Sprintf("ins-%d", time.Now().UnixNano())
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := r.pool.Exec(ctx, `
		INSERT INTO agent_instructions (id, name, content, description, enabled, created_at, updated_at)
		VALUES ($1, $2, $3, $4, $5, NOW(), NOW())`,
		id, in.Name, in.Content, in.Description, enabled)
	if err != nil {
		return "", err
	}
	return id, nil
}

func (r *InstructionRepo) UpdateInstruction(ctx context.Context, id string, in *repository.InstructionInput) error {
	enabled := 0
	if in.Enabled {
		enabled = 1
	}
	_, err := r.pool.Exec(ctx, `
		UPDATE agent_instructions SET name = $2, content = $3, description = $4, enabled = $5, updated_at = NOW()
		WHERE id = $1`,
		id, in.Name, in.Content, in.Description, enabled)
	return err
}

func (r *InstructionRepo) DeleteInstruction(ctx context.Context, id string) error {
	_, err := r.pool.Exec(ctx, `DELETE FROM agent_instructions WHERE id = $1`, id)
	return err
}

var (
	_ repository.PluginRepository      = (*PluginRepo)(nil)
	_ repository.InstructionRepository = (*InstructionRepo)(nil)
)