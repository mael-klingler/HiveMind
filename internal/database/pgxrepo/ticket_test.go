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

package pgxrepo_test

import (
	"context"
	"fmt"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/maelklingler/hivemind/internal/database/pgxrepo"
	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/models"
	"github.com/maelklingler/hivemind/internal/testutil"
)

func TestTicketRepo_CreateAndGet(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewTicketRepo(pool)

	in := &repository.TicketInput{
		ID:          "TEST-1",
		Title:       "Test ticket",
		Description: "A test",
		Labels:      []string{"bug"},
		IssueType:   "Bug",
		Priority:    "High",
	}
	require.NoError(t, repo.CreateTicketAndEnqueue(ctx, in))

	got, err := repo.GetTicket(ctx, "TEST-1")
	require.NoError(t, err)
	assert.Equal(t, "TEST-1", got.ID)
	assert.Equal(t, "Test ticket", got.Title)
	assert.Equal(t, models.TicketQueued, got.Status)
	assert.Equal(t, []string{"bug"}, got.Labels)
}

func TestTicketRepo_RequeueTicket(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewTicketRepo(pool)

	in := &repository.TicketInput{ID: "TEST-2", Title: "Requeue test"}
	require.NoError(t, repo.CreateTicketAndEnqueue(ctx, in))

	require.NoError(t, repo.UpdateTicketStatus(ctx, "TEST-2", "running"))
	require.NoError(t, repo.RequeueTicket(ctx, "TEST-2", 3))

	got, _ := repo.GetTicket(ctx, "TEST-2")
	assert.Equal(t, models.TicketQueued, got.Status)
	assert.Equal(t, 1, got.RetryCount)
}

func TestTicketRepo_RequeueExceedsMaxRetries(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewTicketRepo(pool)

	in := &repository.TicketInput{ID: "TEST-3", Title: "Max retries"}
	require.NoError(t, repo.CreateTicketAndEnqueue(ctx, in))

	for i := 0; i < 4; i++ {
		require.NoError(t, repo.RequeueTicket(ctx, "TEST-3", 3))
	}
	got, _ := repo.GetTicket(ctx, "TEST-3")
	assert.Equal(t, models.TicketFailed, got.Status)
}

func TestTicketRepo_SetLLMUsage(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewTicketRepo(pool)

	in := &repository.TicketInput{ID: "TEST-4", Title: "LLM usage"}
	require.NoError(t, repo.CreateTicketAndEnqueue(ctx, in))

	require.NoError(t, repo.SetTicketLLMUsage(ctx, "TEST-4", 100, 50, 0.01))
	got, _ := repo.GetTicket(ctx, "TEST-4")
	assert.Equal(t, 100, got.LLMPromptTokens)
	assert.Equal(t, 50, got.LLMCompletionTokens)
}

func TestCommentRepo_AddAndList(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	ticketRepo := pgxrepo.NewTicketRepo(pool)
	commentRepo := pgxrepo.NewCommentRepo(pool)

	require.NoError(t, ticketRepo.CreateTicketAndEnqueue(ctx, &repository.TicketInput{ID: "TEST-5", Title: "Comments"}))
	require.NoError(t, commentRepo.AddTicketComment(ctx, "TEST-5", "alice", "comment", "hello"))
	require.NoError(t, commentRepo.AddTicketComment(ctx, "TEST-5", "bob", "comment", "world"))

	comments, err := commentRepo.ListTicketComments(ctx, "TEST-5")
	require.NoError(t, err)
	assert.Len(t, comments, 2)
	assert.Equal(t, "hello", comments[0].Content)
}

func TestAgentRepo_CreateListAndPool(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewAgentRepo(pool)

	require.NoError(t, repo.CreateAgent(ctx, &models.Agent{ID: "agent-a", Name: "Agent A"}))
	require.NoError(t, repo.CreateAgent(ctx, &models.Agent{ID: "agent-b", Name: "Agent B"}))

	agents, err := repo.ListAgents(ctx)
	require.NoError(t, err)
	assert.Len(t, agents, 2)

	require.NoError(t, repo.EnsureAgentPool(ctx, 5))
	agents, _ = repo.ListAgents(ctx)
	assert.Len(t, agents, 5)
}

func TestAgentRepo_SetIdle(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewAgentRepo(pool)

	require.NoError(t, repo.CreateAgent(ctx, &models.Agent{ID: "agent-x", Name: "X"}))
	require.NoError(t, repo.SetAgentStatus(ctx, "agent-x", "running"))
	require.NoError(t, repo.SetAgentIdle(ctx, "agent-x"))

	got, _ := repo.GetAgent(ctx, "agent-x")
	assert.Equal(t, models.AgentIdle, got.Status)
	assert.Empty(t, got.CurrentTask)
}

func TestQueueRepo_ClaimQueueItem(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	ticketRepo := pgxrepo.NewTicketRepo(pool)
	queueRepo := pgxrepo.NewQueueRepo(pool)
	agentRepo := pgxrepo.NewAgentRepo(pool)

	require.NoError(t, agentRepo.CreateAgent(ctx, &models.Agent{ID: "agent-q", Name: "Q"}))
	require.NoError(t, ticketRepo.CreateTicketAndEnqueue(ctx, &repository.TicketInput{ID: "TEST-Q1", Title: "Queue"}))

	items, err := queueRepo.GetQueue(ctx)
	require.NoError(t, err)
	require.Len(t, items, 1)

	require.NoError(t, queueRepo.ClaimQueueItem(ctx, "TEST-Q1", "agent-q"))

	items, _ = queueRepo.GetQueue(ctx)
	assert.Empty(t, items)

	ticket, _ := ticketRepo.GetTicket(ctx, "TEST-Q1")
	assert.Equal(t, models.TicketRunning, ticket.Status)
	assert.Equal(t, "agent-q", ticket.AgentID)
}

func TestRepoRepo_AddGetUpdateDelete(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewRepoRepo(pool)

	in := &repository.RepoInput{Name: "myrepo", URL: "https://gitlab.com/x/y", Branch: "main", Active: true}
	require.NoError(t, repo.AddRepo(ctx, in))

	got, err := repo.GetRepo(ctx, "myrepo")
	require.NoError(t, err)
	assert.Equal(t, "main", got.Branch)
	assert.True(t, got.Active)

	require.NoError(t, repo.UpdateRepo(ctx, &repository.RepoInput{Name: "myrepo", URL: "https://gitlab.com/x/y", Branch: "dev", Active: false}))
	got, _ = repo.GetRepo(ctx, "myrepo")
	assert.Equal(t, "dev", got.Branch)
	assert.False(t, got.Active)

	require.NoError(t, repo.SetRepoActive(ctx, "myrepo", true))
	got, _ = repo.GetRepo(ctx, "myrepo")
	assert.True(t, got.Active)

	require.NoError(t, repo.DeleteRepo(ctx, "myrepo"))
	_, err = repo.GetRepo(ctx, "myrepo")
	assert.Error(t, err)
}

func TestRepoRepo_ListActiveOnly(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewRepoRepo(pool)

	require.NoError(t, repo.AddRepo(ctx, &repository.RepoInput{Name: "active1", URL: "u1", Active: true}))
	require.NoError(t, repo.AddRepo(ctx, &repository.RepoInput{Name: "inactive1", URL: "u2", Active: false}))

	active, err := repo.ListRepos(ctx, true)
	require.NoError(t, err)
	assert.Len(t, active, 1)
	assert.Equal(t, "active1", active[0].Name)

	all, err := repo.ListRepos(ctx, false)
	require.NoError(t, err)
	assert.Len(t, all, 2)
}

func TestTicketRepo_ListTicketsPaged(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewTicketRepo(pool)

	for i := 0; i < 5; i++ {
		require.NoError(t, repo.CreateTicketAndEnqueue(ctx, &repository.TicketInput{
			ID:    fmt.Sprintf("PAGE-%d", i),
			Title: fmt.Sprintf("ticket %d", i),
		}))
	}

	all, err := repo.ListTickets(ctx, "", 0, 0)
	require.NoError(t, err)
	assert.Len(t, all, 5)

	limited, err := repo.ListTickets(ctx, "", 2, 0)
	require.NoError(t, err)
	assert.Len(t, limited, 2)

	queued, err := repo.ListTickets(ctx, "queued", 0, 0)
	require.NoError(t, err)
	assert.Len(t, queued, 5)
}