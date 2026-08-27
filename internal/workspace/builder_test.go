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

package workspace_test

import (
	"context"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/maelklingler/hivemind/internal/models"
	"github.com/maelklingler/hivemind/internal/workspace"
)

func TestGenerateAssignmentPrompt(t *testing.T) {
	ticket := &models.Ticket{ID: "T-1", Title: "Fix bug", Description: "There is a crash."}
	prompt := workspace.GenerateAssignmentPrompt(ticket, []string{"repo-a", "repo-b"}, "repo-a", "simple bug")
	assert.Contains(t, prompt, "T-1")
	assert.Contains(t, prompt, "Fix bug")
	assert.Contains(t, prompt, "There is a crash.")
	assert.Contains(t, prompt, "repo-a, repo-b")
	assert.Contains(t, prompt, "repo-a")
	assert.Contains(t, prompt, "simple bug")
}

func TestBuilder_AnalyzeFallback(t *testing.T) {
	b := &workspace.Builder{}
	ticket := &models.Ticket{ID: "T-2", Title: "Test"}
	repos := []workspace.RepoRef{{Name: "r1", URL: "u1"}, {Name: "r2", URL: "u2"}}

	out := b.AnalyzeFallback(ticket, repos)
	require.NoError(t, nil)
	assert.Len(t, out.SelectedRepos, 1)
	assert.Equal(t, "r1", out.PrimaryRepo)
	assert.Equal(t, "Medium", out.Complexity)
	assert.Contains(t, out.AssignmentMD, "T-2")
}

func TestBuilder_Analyze_NilLLM(t *testing.T) {
	b := workspace.NewBuilder(nil)
	ticket := &models.Ticket{ID: "T-3", Title: "No LLM"}
	repos := []workspace.RepoRef{{Name: "r1", URL: "u1"}}

	out, err := b.Analyze(context.Background(), workspace.AnalysisRequest{Ticket: ticket, AvailableRepo: repos})
	require.NoError(t, err)
	require.NotNil(t, out)
	assert.Equal(t, "r1", out.PrimaryRepo)
}