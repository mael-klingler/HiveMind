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
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/k8s"
	"github.com/maelklingler/hivemind/internal/llm"
	"github.com/maelklingler/hivemind/internal/models"
)

type QueueProcessor struct {
	Config *config.Config
	DB     *database.DB
	K8s    *k8s.Client
	LLM    *llm.LLMClient
	stopCh chan struct{}
}

func NewQueueProcessor(cfg *config.Config, db *database.DB, k8sClient *k8s.Client, llmClient *llm.LLMClient) *QueueProcessor {
	return &QueueProcessor{
		Config: cfg,
		DB:     db,
		K8s:    k8sClient,
		LLM:    llmClient,
		stopCh: make(chan struct{}),
	}
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
		case <-qp.stopCh:
			slog.Info("queue processor stopped")
			return nil
		case <-ticker.C:
			if err := qp.processQueue(ctx); err != nil {
				slog.Error("queue processing error", "error", err)
			}
		}
	}
}

func (qp *QueueProcessor) Stop() {
	close(qp.stopCh)
}

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

		if len(idleAgents) == 0 {
			break
		}
		agent := idleAgents[0]
		idleAgents = idleAgents[1:]

		slog.Info("assigning ticket to agent", "ticket_id", ticket.ID, "agent_id", agent.ID)

		if err := qp.spawnAgentForTicket(ctx, ticket, agent); err != nil {
			slog.Error("failed to spawn agent", "ticket_id", ticket.ID, "error", err)
			continue
		}

		if err := qp.DB.ClaimQueueItem(ctx, ticket.ID, agent.ID); err != nil {
			slog.Error("failed to claim queue item atomically", "ticket_id", ticket.ID, "error", err)
		}
	}
	return nil
}

func (qp *QueueProcessor) spawnAgentForTicket(ctx context.Context, ticket *models.Ticket, agent *models.Agent) error {
	repos, err := qp.DB.ListRepos(ctx, true)
	if err != nil {
		return fmt.Errorf("list repos: %w", err)
	}

	repoRefs := make([]k8s.RepoRef, 0, len(repos))
	selectedRepos := make([]string, 0)
	primaryRepo := ""

	if ticket.AIPlanning != "" {
		var planning map[string]interface{}
		if err := jsonUnmarshal(ticket.AIPlanning, &planning); err == nil {
			if sr, ok := planning["selected_repos"].([]interface{}); ok {
				for _, r := range sr {
					if name, ok := r.(string); ok {
						selectedRepos = append(selectedRepos, name)
					}
				}
			}
			if pr, ok := planning["primary_repo"].(string); ok {
				primaryRepo = pr
			}
		}
	}

	for _, repo := range repos {
		if len(selectedRepos) == 0 || contains(selectedRepos, repo.Name) {
			repoRefs = append(repoRefs, k8s.RepoRef{
				Name:   repo.Name,
				URL:    repo.URL,
				Branch: repo.Branch,
			})
		}
	}
	if len(repoRefs) == 0 && len(repos) > 0 {
		repoRefs = append(repoRefs, k8s.RepoRef{
			Name:   repos[0].Name,
			URL:    repos[0].URL,
			Branch: repos[0].Branch,
		})
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

	assignment := fmt.Sprintf("# Task: %s – %s\n\n%s\n\n## Selected Repositories\n%s\n\n## Instructions\nPlease implement the changes described above.",
		ticket.ID, ticket.Title, ticket.Description, strings.Join(selectedRepos, ", "))

	params := k8s.PodSpecParams{
		TicketID:           ticket.ID,
		TicketTitle:        ticket.Title,
		Repos:              repoRefs,
		AssignmentMD:       assignment,
		Analysis:           map[string]interface{}{"primary_repo": primaryRepo, "selected_repos": selectedRepos},
		AgentID:            agent.ID,
		GitLabHost:         qp.Config.GitLabHost,
		GitUser:            qp.Config.GitUser,
		GitLabToken:        qp.Config.GitLabToken,
		GitHubToken:        qp.Config.GitHubToken,
		GitHubHost:         qp.Config.GitHubHost,
		OllamaBaseURL:      qp.Config.OllamaBaseURL,
		OpencodeModel:      qp.Config.OpencodeModel,
		OllamaCloudAPIKey:  qp.Config.OllamaCloudAPIKey,
		MCPServers:         mcpRefs,
		Branch:             branch,
		GitSSLNoVerify:     qp.Config.GitSSLNoVerify,
		PermissionWrite:     "allow",
		PermissionBash:      "allow",
		PermissionExtDir:    "allow",
		PermissionDoomLoop: "deny",
	}

	result, err := k8s.SpawnAgentPod(ctx, qp.K8s, params)
	if err != nil {
		return fmt.Errorf("spawn agent pod: %w", err)
	}

	slog.Info("agent pod spawned", "ticket_id", ticket.ID, "pod", result.PodName)
	return nil
}

func contains(slice []string, item string) bool {
	for _, s := range slice {
		if s == item {
			return true
		}
	}
	return false
}

func jsonUnmarshal(data string, v interface{}) error {
	return json.Unmarshal([]byte(data), v)
}