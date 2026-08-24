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

// MCPServerInput is the input for MCP server create/update.
type MCPServerInput struct {
	Name        string            `json:"name"`
	Command     string            `json:"command"`
	Args        string            `json:"args,omitempty"`
	Env         string            `json:"env,omitempty"`
	ServerType  string            `json:"server_type"`
	Enabled     bool              `json:"enabled"`
	Description string            `json:"description,omitempty"`
}

// MCPRepository defines MCP server persistence operations.
type MCPRepository interface {
	ListMCPServers(ctx context.Context) ([]*models.MCPServer, error)
	GetEnabledMCPServers(ctx context.Context) ([]*models.MCPServer, error)
	CreateMCPServer(ctx context.Context, in *MCPServerInput) (string, error)
	UpdateMCPServer(ctx context.Context, id string, in *MCPServerInput) error
	DeleteMCPServer(ctx context.Context, id string) error
}

// Plugin represents an opencode plugin row.
type Plugin struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Package     string `json:"package,omitempty"`
	Enabled     bool   `json:"enabled"`
	Description string `json:"description,omitempty"`
	Config      string `json:"config,omitempty"`
}

// PluginInput is the input for plugin create/update.
type PluginInput struct {
	Name        string `json:"name"`
	Package     string `json:"package,omitempty"`
	Enabled     bool   `json:"enabled"`
	Description string `json:"description,omitempty"`
	Config      string `json:"config,omitempty"`
}

// PluginRepository defines plugin persistence operations.
type PluginRepository interface {
	ListPlugins(ctx context.Context) ([]*Plugin, error)
	CreatePlugin(ctx context.Context, in *PluginInput) (string, error)
	UpdatePlugin(ctx context.Context, id string, in *PluginInput) error
	DeletePlugin(ctx context.Context, id string) error
}

// Instruction represents an agent instruction row.
type Instruction struct {
	ID          string `json:"id"`
	Name        string `json:"name"`
	Content     string `json:"content"`
	Description string `json:"description,omitempty"`
	Enabled     bool   `json:"enabled"`
}

// InstructionInput is the input for instruction create/update.
type InstructionInput struct {
	Name        string `json:"name"`
	Content     string `json:"content"`
	Description string `json:"description,omitempty"`
	Enabled     bool   `json:"enabled"`
}

// InstructionRepository defines agent instruction persistence operations.
type InstructionRepository interface {
	ListInstructions(ctx context.Context) ([]*Instruction, error)
	CreateInstruction(ctx context.Context, in *InstructionInput) (string, error)
	UpdateInstruction(ctx context.Context, id string, in *InstructionInput) error
	DeleteInstruction(ctx context.Context, id string) error
}