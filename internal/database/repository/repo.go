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

// RepoInput is the input for repo creation/update (API layer).
type RepoInput struct {
	Name        string   `json:"name"`
	URL         string   `json:"url"`
	Branch      string   `json:"branch"`
	Description string   `json:"description"`
	Tags        []string `json:"tags"`
	Active      bool     `json:"active"`
}

// RepoRepository defines repo persistence operations.
type RepoRepository interface {
	AddRepo(ctx context.Context, r *RepoInput) error
	GetRepo(ctx context.Context, name string) (*models.Repo, error)
	ListRepos(ctx context.Context, activeOnly bool) ([]*models.Repo, error)
	UpdateRepo(ctx context.Context, r *RepoInput) error
	PatchRepo(ctx context.Context, name string, patch map[string]interface{}) error
	BulkUpdateRepos(ctx context.Context, repos []*RepoInput) error
	SetRepoActive(ctx context.Context, name string, active bool) error
	DeleteRepo(ctx context.Context, name string) error
}

// QueueRepository defines queue operations.
type QueueRepository interface {
	EnqueueTicket(ctx context.Context, ticketID string, priority int) error
	GetQueue(ctx context.Context) ([]*models.QueueItem, error)
	DequeueItem(ctx context.Context, id string) error
	ClaimQueueItem(ctx context.Context, ticketID, agentID string) error
}