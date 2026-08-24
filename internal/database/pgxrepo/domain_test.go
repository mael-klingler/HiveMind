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
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/maelklingler/hivemind/internal/database/pgxrepo"
	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/testutil"
)

func TestPipelineRepo_CreateAndListSteps(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	ticketRepo := pgxrepo.NewTicketRepo(pool)
	pipelineRepo := pgxrepo.NewPipelineRepo(pool)

	require.NoError(t, ticketRepo.CreateTicketAndEnqueue(ctx, &repository.TicketInput{ID: "PIPE-1", Title: "Pipeline"}))

	step := &repository.PipelineStep{
		ID:       "step-1",
		TicketID: "PIPE-1",
		Phase:    repository.PhaseWork,
		Status:   "pending",
	}
	require.NoError(t, pipelineRepo.CreateStep(ctx, step))

	steps, err := pipelineRepo.ListStepsByTicket(ctx, "PIPE-1")
	require.NoError(t, err)
	require.Len(t, steps, 1)
	assert.Equal(t, repository.PhaseWork, steps[0].Phase)

	require.NoError(t, pipelineRepo.UpdateStepStatus(ctx, "step-1", "completed"))
	got, _ := pipelineRepo.GetStep(ctx, "step-1")
	assert.Equal(t, "completed", got.Status)
}

func TestPipelineRepo_AdvancePhase(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	pipelineRepo := pgxrepo.NewPipelineRepo(pool)

	next, err := pipelineRepo.AdvancePhase(ctx, "x", repository.PhaseWork)
	require.NoError(t, err)
	assert.Equal(t, repository.PhaseTest, next)

	next, err = pipelineRepo.AdvancePhase(ctx, "x", repository.PhaseShip)
	require.NoError(t, err)
	assert.Equal(t, repository.PhaseListen, next)

	_, err = pipelineRepo.AdvancePhase(ctx, "x", repository.PhaseListen)
	assert.Error(t, err)
}

func TestMemoryRepo_SetGetDeleteBlock(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewMemoryRepo(pool)

	in := &repository.MemoryBlockInput{
		Label:       "persona",
		Content:     "You are a tester.",
		Description: "Agent identity",
		ReadOnly:    true,
		BlockLimit:  3000,
	}
	require.NoError(t, repo.SetBlock(ctx, "agent-m1", in))

	got, err := repo.GetBlock(ctx, "agent-m1", "persona")
	require.NoError(t, err)
	assert.Equal(t, "You are a tester.", got.Content)
	assert.True(t, got.ReadOnly)
	assert.Equal(t, 3000, got.BlockLimit)

	blocks, err := repo.ListBlocks(ctx, "agent-m1")
	require.NoError(t, err)
	require.Len(t, blocks, 1)

	require.NoError(t, repo.DeleteBlock(ctx, "agent-m1", "persona"))
	_, err = repo.GetBlock(ctx, "agent-m1", "persona")
	assert.Error(t, err)
}

func TestMemoryRepo_SeedDefaults(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewMemoryRepo(pool)

	require.NoError(t, repo.SeedDefaults(ctx, "agent-m2"))
	blocks, err := repo.ListBlocks(ctx, "agent-m2")
	require.NoError(t, err)
	assert.Len(t, blocks, 3)
	labels := []string{blocks[0].Label, blocks[1].Label, blocks[2].Label}
	assert.Contains(t, labels, "persona")
	assert.Contains(t, labels, "human")
	assert.Contains(t, labels, "project")
}

func TestPluginRepo_CRUD(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewPluginRepo(pool)

	id, err := repo.CreatePlugin(ctx, &repository.PluginInput{Name: "myplugin", Package: "npm:myplugin", Enabled: true})
	require.NoError(t, err)
	assert.NotEmpty(t, id)

	plugins, err := repo.ListPlugins(ctx)
	require.NoError(t, err)
	require.Len(t, plugins, 1)
	assert.True(t, plugins[0].Enabled)

	require.NoError(t, repo.UpdatePlugin(ctx, id, &repository.PluginInput{Name: "myplugin", Enabled: false}))
	plugins, _ = repo.ListPlugins(ctx)
	assert.False(t, plugins[0].Enabled)

	require.NoError(t, repo.DeletePlugin(ctx, id))
	plugins, _ = repo.ListPlugins(ctx)
	assert.Empty(t, plugins)
}

func TestInstructionRepo_CRUD(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewInstructionRepo(pool)

	id, err := repo.CreateInstruction(ctx, &repository.InstructionInput{Name: "code-style", Content: "Use tabs.", Enabled: true})
	require.NoError(t, err)

	instructions, err := repo.ListInstructions(ctx)
	require.NoError(t, err)
	require.Len(t, instructions, 1)
	assert.Equal(t, "Use tabs.", instructions[0].Content)

	require.NoError(t, repo.UpdateInstruction(ctx, id, &repository.InstructionInput{Name: "code-style", Content: "Use spaces.", Enabled: true}))
	instructions, _ = repo.ListInstructions(ctx)
	assert.Equal(t, "Use spaces.", instructions[0].Content)

	require.NoError(t, repo.DeleteInstruction(ctx, id))
	instructions, _ = repo.ListInstructions(ctx)
	assert.Empty(t, instructions)
}

func TestMCPRepo_CRUD(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewMCPRepo(pool)

	id, err := repo.CreateMCPServer(ctx, &repository.MCPServerInput{
		Name: "test-mcp", Command: "npx", ServerType: "local", Enabled: true,
	})
	require.NoError(t, err)

	servers, err := repo.ListMCPServers(ctx)
	require.NoError(t, err)
	require.Len(t, servers, 1)
	assert.True(t, servers[0].Enabled)

	enabled, err := repo.GetEnabledMCPServers(ctx)
	require.NoError(t, err)
	assert.Len(t, enabled, 1)

	require.NoError(t, repo.UpdateMCPServer(ctx, id, &repository.MCPServerInput{Name: "test-mcp", Command: "npx", Enabled: false}))
	enabled, _ = repo.GetEnabledMCPServers(ctx)
	assert.Empty(t, enabled)

	require.NoError(t, repo.DeleteMCPServer(ctx, id))
	servers, _ = repo.ListMCPServers(ctx)
	assert.Empty(t, servers)
}

func TestGroupRepo_CreateAddMessageList(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewGroupRepo(pool)

	g := &repository.Group{ID: "grp-1", Name: "Team A", TicketIDs: []string{"T-1"}}
	require.NoError(t, repo.CreateGroup(ctx, g))

	got, err := repo.GetGroup(ctx, "grp-1")
	require.NoError(t, err)
	assert.Equal(t, "Team A", got.Name)

	require.NoError(t, repo.AddMessage(ctx, "grp-1", "agent-1", "message", "hello team"))
	msgs, err := repo.ListMessages(ctx, "grp-1")
	require.NoError(t, err)
	require.Len(t, msgs, 1)
	assert.Equal(t, "hello team", msgs[0].Content)

	groups, err := repo.ListGroups(ctx)
	require.NoError(t, err)
	assert.Len(t, groups, 1)

	require.NoError(t, repo.DeleteGroup(ctx, "grp-1"))
	groups, _ = repo.ListGroups(ctx)
	assert.Empty(t, groups)
}

func TestSettingsRepo_SetGetAll(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	repo := pgxrepo.NewSettingsRepo(pool)

	require.NoError(t, repo.SetSetting(ctx, "key1", "val1"))
	require.NoError(t, repo.SetSetting(ctx, "key2", "val2"))

	val, err := repo.GetSetting(ctx, "key1")
	require.NoError(t, err)
	assert.Equal(t, "val1", val)

	all, err := repo.GetAllSettings(ctx)
	require.NoError(t, err)
	assert.Len(t, all, 2)
}

func TestMetricRepo_RecordAndGetSummary(t *testing.T) {
	pool := testutil.PostgresFixture(t)
	ctx := context.Background()
	ticketRepo := pgxrepo.NewTicketRepo(pool)
	repo := pgxrepo.NewMetricRepo(pool)

	require.NoError(t, ticketRepo.CreateTicketAndEnqueue(ctx, &repository.TicketInput{ID: "METRIC-1", Title: "M"}))
	require.NoError(t, repo.RecordMetricEvent(ctx, &repository.MetricEventInput{
		EventType: "phase_complete", TicketID: "METRIC-1", Phase: "work", DurationSeconds: 42.5,
	}))

	summary, err := repo.GetMetricsSummary(ctx)
	require.NoError(t, err)
	assert.Equal(t, 1, summary.TotalTickets)
}