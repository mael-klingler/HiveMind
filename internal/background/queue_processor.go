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

package background

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/k8s"
	"github.com/maelklingler/hivemind/internal/llm"
	"github.com/maelklingler/hivemind/internal/models"
	"github.com/maelklingler/hivemind/internal/workspace"
)

type QueueProcessor struct {
	Config *config.Config
	DB     *database.DB
	K8s    *k8s.Client
	LLM    *llm.LLMClient
	WS     *workspace.Builder
}

func NewQueueProcessor(cfg *config.Config, db *database.DB, k8sClient *k8s.Client, llmClient *llm.LLMClient, wsBuilder *workspace.Builder) *QueueProcessor {
	return &QueueProcessor{Config: cfg, DB: db, K8s: k8sClient, LLM: llmClient, WS: wsBuilder}
}

func (qp *QueueProcessor) liveConfig() *config.Config {
	if qp.DB == nil {
		return qp.Config
	}
	return config.LoadFromDB(func(key string) (string, error) {
		return qp.DB.GetSetting(context.Background(), key)
	})
}

func (qp *QueueProcessor) Run(ctx context.Context) error {
	slog.Info("queue processor started")
	ticker := time.NewTicker(time.Duration(qp.Config.QueuePollInterval) * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			slog.Info("queue processor stopping")
			return nil
		case <-ticker.C:
			if err := qp.processQueue(ctx); err != nil {
				slog.Error("queue processing error", "error", err)
			}
		}
	}
}

func (qp *QueueProcessor) Stop() {}

func (qp *QueueProcessor) processQueue(ctx context.Context) error {
	queue, err := qp.DB.GetQueue(ctx)
	if err != nil {
		return err
	}
	if len(queue) == 0 {
		return nil
	}

	agents, err := qp.DB.ListAgents(ctx)
	if err != nil {
		return err
	}

	idleAgents := make([]*models.Agent, 0)
	for _, a := range agents {
		if a.Status == "idle" {
			idleAgents = append(idleAgents, a)
		}
	}
	if len(idleAgents) == 0 {
		return nil
	}

	for _, item := range queue {
		ticket, err := qp.DB.GetTicket(ctx, item.TicketID)
		if err != nil {
			slog.Warn("ticket not found in queue", "ticket_id", item.TicketID, "error", err)
			if err := qp.DB.DequeueItem(ctx, item.ID); err != nil {
				slog.Error("failed to dequeue missing ticket", "item_id", item.ID, "error", err)
			}
			continue
		}
		if ticket.Status != "queued" {
			if err := qp.DB.DequeueItem(ctx, item.ID); err != nil {
				slog.Error("failed to dequeue non-queued ticket", "item_id", item.ID, "error", err)
			}
			continue
		}

		// Idea tickets are handled by the Planner, not the QueueProcessor.
		// Skip them here so the Planner can decompose them into sub-tasks.
		if ticket.Type == "idea" {
			continue
		}

		if len(idleAgents) == 0 {
			break
		}
		agent := idleAgents[0]
		idleAgents = idleAgents[1:]

		slog.Info("assigning ticket to agent", "ticket_id", ticket.ID, "agent_id", agent.ID)

		// Atomically claim the queue item BEFORE spawning the pod, so a
		// spawn failure leaves the ticket requeueable instead of orphaned.
		if err := qp.DB.ClaimQueueItem(ctx, ticket.ID, agent.ID); err != nil {
			slog.Error("failed to claim queue item atomically", "ticket_id", ticket.ID, "error", err)
			continue
		}

		if err := qp.spawnAgentForTicket(ctx, ticket, agent); err != nil {
			slog.Error("failed to spawn agent, requeueing ticket", "ticket_id", ticket.ID, "error", err)
			if qp.Config.AgentRetryDelay > 0 {
				slog.Info("applying retry backoff", "ticket_id", ticket.ID, "delay_seconds", qp.Config.AgentRetryDelay)
				select {
				case <-time.After(time.Duration(qp.Config.AgentRetryDelay) * time.Second):
				case <-ctx.Done():
					return ctx.Err()
				}
			}
			if err := qp.DB.RequeueTicket(ctx, ticket.ID, qp.Config.AgentMaxRetries); err != nil {
				slog.Error("failed to requeue ticket after spawn failure", "ticket_id", ticket.ID, "error", err)
			}
			continue
		}
	}
	return nil
}

func (qp *QueueProcessor) spawnAgentForTicket(ctx context.Context, ticket *models.Ticket, agent *models.Agent) error {
	lc := qp.liveConfig()
	repos, err := qp.DB.ListRepos(ctx, true)
	if err != nil {
		return fmt.Errorf("list repos: %w", err)
	}

	availableRepos := make([]workspace.RepoRef, 0, len(repos))
	for _, repo := range repos {
		availableRepos = append(availableRepos, workspace.RepoRef{
			Name:   repo.Name,
			URL:    repo.URL,
			Branch: repo.Branch,
		})
	}

	// If the user manually selected repos, use only those — skip LLM analysis.
	var analysis *workspace.AnalysisOutput
	if len(ticket.SelectedRepos) > 0 {
		selectedSet := make(map[string]bool, len(ticket.SelectedRepos))
		for _, name := range ticket.SelectedRepos {
			selectedSet[name] = true
		}
		selectedRepoRefs := make([]workspace.RepoRef, 0, len(ticket.SelectedRepos))
		for _, r := range availableRepos {
			if selectedSet[r.Name] {
				selectedRepoRefs = append(selectedRepoRefs, r)
			}
		}
		if len(selectedRepoRefs) == 0 && len(availableRepos) > 0 {
			selectedRepoRefs = []workspace.RepoRef{availableRepos[0]}
		}
		primary := selectedRepoRefs[0].Name
		selectedNames := make([]string, 0, len(selectedRepoRefs))
		for _, r := range selectedRepoRefs {
			selectedNames = append(selectedNames, r.Name)
		}
		analysis = &workspace.AnalysisOutput{
			SelectedRepos: selectedRepoRefs,
			PrimaryRepo:   primary,
			Complexity:    "Medium",
			AssignmentMD:  workspace.GenerateAssignmentPrompt(ticket, selectedNames, primary, ""),
			AIPlanning:    "",
		}
		slog.Info("using manually selected repos", "ticket_id", ticket.ID, "repos", selectedNames, "primary", primary)
	} else if qp.WS != nil {
		analysis, err = qp.WS.Analyze(ctx, workspace.AnalysisRequest{
			Ticket:        ticket,
			AvailableRepo: availableRepos,
		})
		if err != nil {
			slog.Warn("LLM analysis failed, using fallback", "ticket_id", ticket.ID, "error", err)
		}
	}
	if analysis == nil {
		analysis = (&workspace.Builder{}).AnalyzeFallback(ticket, availableRepos)
	}

	if analysis.AIPlanning != "" {
		if err := qp.DB.SetTicketAIPlanning(ctx, ticket.ID, analysis.AIPlanning); err != nil {
			slog.Warn("failed to persist AI planning", "ticket_id", ticket.ID, "error", err)
		}
	}

	repoRefs := make([]k8s.RepoRef, 0, len(analysis.SelectedRepos))
	for _, r := range analysis.SelectedRepos {
		repoRefs = append(repoRefs, k8s.RepoRef{
			Name:   r.Name,
			URL:    r.URL,
			Branch: r.Branch,
		})
	}
	selectedRepos := make([]string, 0, len(repoRefs))
	for _, r := range repoRefs {
		selectedRepos = append(selectedRepos, r.Name)
	}

	branch := ticket.Branch
	if branch == "" {
		branch = fmt.Sprintf("feature/%s", strings.ToLower(ticket.ID))
	}

	mcpServers, _ := qp.DB.GetEnabledMCPServers(ctx)

	mcpRefs := make([]k8s.MCPServerRef, 0, len(mcpServers))
	for _, srv := range mcpServers {
		mcpRefs = append(mcpRefs, k8s.MCPServerRef{
			Name:       srv.Name,
			Command:    srv.Command,
			ServerType: srv.ServerType,
			Enabled:    srv.Enabled,
		})
	}

	params := k8s.PodSpecParams{
		TicketID:           ticket.ID,
		TicketTitle:        ticket.Title,
		Repos:              repoRefs,
		AssignmentMD:       analysis.AssignmentMD,
		Analysis:           map[string]interface{}{"primary_repo": analysis.PrimaryRepo, "selected_repos": selectedRepos, "complexity": analysis.Complexity},
		AgentID:            agent.ID,
		GitLabHost:         lc.GitLabHost,
		GitUser:            lc.GitUser,
		GitLabToken:        lc.GitLabToken,
		GitHubToken:        lc.GitHubToken,
		GitHubHost:         lc.GitHubHost,
		OllamaBaseURL:      lc.OllamaBaseURL,
		OpencodeModel:      lc.OpencodeModel,
		OllamaCloudAPIKey:  lc.OllamaCloudAPIKey,
		MCPServers:         mcpRefs,
		Branch:             branch,
		GitSSLNoVerify:     qp.Config.GitSSLNoVerify,
		PermissionWrite:    qp.Config.AgentPermWrite,
		PermissionBash:     qp.Config.AgentPermBash,
		PermissionExtDir:   qp.Config.AgentPermExtDir,
		PermissionDoomLoop: qp.Config.AgentPermDoomLoop,
	}

	result, err := k8s.SpawnAgentPod(ctx, qp.K8s, params)
	if err != nil {
		return fmt.Errorf("spawn agent pod: %w", err)
	}

	slog.Info("agent pod spawned", "ticket_id", ticket.ID, "pod", result.PodName, "complexity", analysis.Complexity, "primary", analysis.PrimaryRepo, "repos", len(repoRefs))
	return nil
}

