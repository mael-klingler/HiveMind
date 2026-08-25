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

// Package workspace builds agent workspaces: runs LLM repo analysis,
// generates the assignment prompt, and prepares pod spawn parameters.
package workspace

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/maelklingler/hivemind/internal/llm/provider"
	"github.com/maelklingler/hivemind/internal/models"
)

// Builder orchestrates LLM repo analysis and assignment-prompt generation.
type Builder struct {
	LLM provider.Provider
}

// NewBuilder creates a new workspace builder.
func NewBuilder(llm provider.Provider) *Builder {
	return &Builder{LLM: llm}
}

// RepoRef describes a repo for spawn parameters.
type RepoRef struct {
	Name   string `json:"name"`
	URL    string `json:"url"`
	Branch string `json:"branch"`
}

// AnalysisRequest is the input for AnalyzeReposForTicket.
type AnalysisRequest struct {
	Ticket       *models.Ticket
	AvailableRepo []RepoRef
}

// AnalysisOutput is the result of LLM repo analysis + prompt generation.
type AnalysisOutput struct {
	SelectedRepos  []RepoRef
	PrimaryRepo    string
	Complexity     string
	Reasoning      string
	AssignmentMD   string
	AIPlanning     string
}

// Analyze runs the LLM repo analysis and builds the assignment prompt.
func (b *Builder) Analyze(ctx context.Context, req AnalysisRequest) (*AnalysisOutput, error) {
	if b.LLM == nil || !b.LLM.IsAvailable(ctx) {
		return b.fallbackAnalysis(req), nil
	}

	repoContexts := make([]provider.RepoContext, 0, len(req.AvailableRepo))
	for _, r := range req.AvailableRepo {
		repoContexts = append(repoContexts, provider.RepoContext{
			Name:      r.Name,
			Available: true,
		})
	}

	result, err := b.LLM.AnalyzeRepos(ctx, req.Ticket.ID, req.Ticket.Title, req.Ticket.Description, repoContexts)
	if err != nil {
		return b.fallbackAnalysis(req), nil
	}

	selectedMap := make(map[string]bool)
	for _, name := range result.SelectedRepos {
		selectedMap[name] = true
	}

	selectedRepos := make([]RepoRef, 0)
	for _, r := range req.AvailableRepo {
		if len(result.SelectedRepos) == 0 || selectedMap[r.Name] {
			selectedRepos = append(selectedRepos, r)
		}
	}
	if len(selectedRepos) == 0 && len(req.AvailableRepo) > 0 {
		selectedRepos = []RepoRef{req.AvailableRepo[0]}
	}

	primary := result.PrimaryRepo
	if primary == "" && len(selectedRepos) > 0 {
		primary = selectedRepos[0].Name
	}

	planning, _ := json.Marshal(map[string]interface{}{
		"selected_repos": result.SelectedRepos,
		"primary_repo":   primary,
		"complexity":     result.Complexity,
		"reasoning":      result.Reasoning,
	})

	assignment := GenerateAssignmentPrompt(req.Ticket, result.SelectedRepos, primary, result.Reasoning)

	return &AnalysisOutput{
		SelectedRepos: selectedRepos,
		PrimaryRepo:   primary,
		Complexity:    result.Complexity,
		Reasoning:     result.Reasoning,
		AssignmentMD:  assignment,
		AIPlanning:    string(planning),
	}, nil
}

func (b *Builder) fallbackAnalysis(req AnalysisRequest) *AnalysisOutput {
	return b.AnalyzeFallback(req.Ticket, req.AvailableRepo)
}

// AnalyzeFallback returns a non-LLM analysis. It selects only the first
// (most relevant) repo as primary and selected, keeping the agent focused.
func (b *Builder) AnalyzeFallback(ticket *models.Ticket, availableRepo []RepoRef) *AnalysisOutput {
	if len(availableRepo) == 0 {
		return &AnalysisOutput{
			SelectedRepos: []RepoRef{},
			PrimaryRepo:   "",
			Complexity:    "Medium",
			AssignmentMD:  GenerateAssignmentPrompt(ticket, []string{}, "", ""),
			AIPlanning:    "",
		}
	}
	primary := availableRepo[0].Name
	selectedRepos := []RepoRef{availableRepo[0]}
	selectedNames := []string{primary}
	return &AnalysisOutput{
		SelectedRepos: selectedRepos,
		PrimaryRepo:   primary,
		Complexity:    "Medium",
		AssignmentMD:  GenerateAssignmentPrompt(ticket, selectedNames, primary, ""),
		AIPlanning:    "",
	}
}

// GenerateAssignmentPrompt builds the task.md content for the agent pod.
func GenerateAssignmentPrompt(ticket *models.Ticket, selectedRepos []string, primaryRepo, reasoning string) string {
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("# Task: %s – %s\n\n", ticket.ID, ticket.Title))
	sb.WriteString(ticket.Description)
	sb.WriteString("\n\n## Selected Repositories\n")
	sb.WriteString(strings.Join(selectedRepos, ", "))
	sb.WriteString("\n\n## Primary Repository\n")
	sb.WriteString(primaryRepo)
	if reasoning != "" {
		sb.WriteString("\n\n## Analysis Reasoning\n")
		sb.WriteString(reasoning)
	}
	sb.WriteString("\n\n## Instructions\n")
	sb.WriteString("Please implement the changes described above. Follow project conventions. Write tests.\n")
	return sb.String()
}