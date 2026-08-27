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
	"log/slog"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/k8s"
	"github.com/maelklingler/hivemind/internal/sse"
	"github.com/maelklingler/hivemind/internal/vcs"
	"github.com/maelklingler/hivemind/internal/vcs/gitlab"
	"github.com/maelklingler/hivemind/internal/vcs/github"
)

type ReviewMonitor struct {
	Config      *config.Config
	DB         *database.DB
	K8s        *k8s.Client
	Broadcaster *sse.Broadcaster
}

func NewReviewMonitor(cfg *config.Config, db *database.DB, k8sClient *k8s.Client, b *sse.Broadcaster) *ReviewMonitor {
	return &ReviewMonitor{Config: cfg, DB: db, K8s: k8sClient, Broadcaster: b}
}

func (rm *ReviewMonitor) Run(ctx context.Context) error {
	slog.Info("review lifecycle monitor started")
	time.Sleep(15 * time.Second)

	ticker := time.NewTicker(time.Duration(rm.Config.ReviewPollInterval) * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			slog.Info("review lifecycle monitor stopping")
			return nil
		case <-ticker.C:
			if err := rm.checkOpenMRs(ctx); err != nil {
				slog.Error("review monitor error", "error", err)
			}
		}
	}
}

func (rm *ReviewMonitor) Stop() {}

func (rm *ReviewMonitor) checkOpenMRs(ctx context.Context) error {
	tickets, err := rm.DB.ListOpenMRTickets(ctx)
	if err != nil || len(tickets) == 0 {
		return err
	}

	var provider vcs.VCSProvider
	if rm.Config.VCSProvider == "github" {
		provider = github.New(rm.Config.GitHubHost, rm.Config.GitHubToken)
	} else {
		provider = gitlab.New(rm.Config.GitLabHost, rm.Config.GitLabToken)
	}

	for _, ticket := range tickets {
		mrURL := ticket.MRURL
		if mrURL == "" {
			continue
		}

		mrData, err := provider.FetchMR(ctx, mrURL)
		if err != nil {
			slog.Warn("failed to fetch MR", "ticket_id", ticket.ID, "mr_url", mrURL, "error", err)
			continue
		}
		if mrData == nil {
			continue
		}

		mrState, _ := mrData["state"].(string)

		if mrState == "merged" {
			if err := rm.DB.UpdateTicketStatus(ctx, ticket.ID, "merged"); err != nil {
				slog.Error("failed to update ticket status to merged", "ticket_id", ticket.ID, "error", err)
			}
			if err := rm.DB.SetTicketReviewStatus(ctx, ticket.ID, "approved", "MR merged: "+mrURL); err != nil {
				slog.Error("failed to set ticket review status", "ticket_id", ticket.ID, "error", err)
			}
			rm.K8s.CleanupAgentResources(ctx, ticket.ID)
			rm.Broadcaster.Broadcast("ticket_merged", map[string]string{"ticket_id": ticket.ID})
			slog.Info("MR merged → ticket completed", "ticket_id", ticket.ID)
			continue
		}

		if mrState == "closed" {
			if err := rm.DB.SetTicketReviewStatus(ctx, ticket.ID, "changes_requested", "MR closed without merge"); err != nil {
				slog.Error("failed to set ticket review status", "ticket_id", ticket.ID, "error", err)
			}
			if err := rm.DB.RequeueTicket(ctx, ticket.ID, rm.Config.AgentMaxRetries); err != nil {
				slog.Error("failed to requeue ticket", "ticket_id", ticket.ID, "error", err)
			}
			rm.Broadcaster.Broadcast("ticket_requeued", map[string]string{"ticket_id": ticket.ID, "reason": "MR closed"})
			slog.Info("MR closed → ticket re-queued", "ticket_id", ticket.ID)
			continue
		}

		if mrState == "opened" {
			projectPath, mrIID := provider.ParseMRURL(mrURL)
			if projectPath == "" {
				continue
			}

			mergeStatus, _ := mrData["merge_status"].(string)
			hasConflicts, _ := mrData["has_conflicts"].(bool)
			if mergeStatus == "cannot_be_merged" || hasConflicts {
				if err := rm.DB.SetTicketReviewStatus(ctx, ticket.ID, "changes_requested",
					"Merge conflict detected on branch"); err != nil {
					slog.Error("failed to set review status for conflict", "ticket_id", ticket.ID, "error", err)
				}
				if err := rm.DB.RequeueTicket(ctx, ticket.ID, rm.Config.AgentMaxRetries); err != nil {
					slog.Error("failed to requeue ticket for conflict", "ticket_id", ticket.ID, "error", err)
				}
				rm.Broadcaster.Broadcast("ticket_requeued", map[string]string{
					"ticket_id": ticket.ID, "reason": "Merge conflict detected",
				})
				slog.Warn("merge conflict detected", "ticket_id", ticket.ID)
				continue
			}

			pipeline, _ := mrData["head_pipeline"].(map[string]interface{})
			if pipeline == nil {
				pipeline, _ = mrData["pipeline"].(map[string]interface{})
			}
			if pipeline != nil {
				pipelineStatus, _ := pipeline["status"].(string)
				if pipelineStatus == "failed" {
					if err := rm.DB.SetTicketReviewStatus(ctx, ticket.ID, "changes_requested", "Pipeline failed"); err != nil {
						slog.Error("failed to set review status for pipeline failure", "ticket_id", ticket.ID, "error", err)
					}
					if err := rm.DB.RequeueTicket(ctx, ticket.ID, rm.Config.AgentMaxRetries); err != nil {
						slog.Error("failed to requeue ticket for pipeline failure", "ticket_id", ticket.ID, "error", err)
					}
					rm.Broadcaster.Broadcast("ticket_requeued", map[string]string{
						"ticket_id": ticket.ID, "reason": "Pipeline failed",
					})
					slog.Info("pipeline failed → re-queue", "ticket_id", ticket.ID)
				}
			}

			comments, err := provider.FetchMRComments(ctx, projectPath, mrIID)
			if err != nil {
				slog.Warn("failed to fetch MR comments", "error", err)
				continue
			}
			if comments == nil {
				continue
			}

			lastNoteID := ticket.MRLastNoteID
			for _, comment := range comments {
				noteID, _ := comment["id"].(float64)
				if int(noteID) <= lastNoteID {
					continue
				}
				isSystem, _ := comment["system"].(bool)
				if isSystem {
					continue
				}
				author, _ := comment["author"].(map[string]interface{})
				username, _ := author["username"].(string)
				botUsername := "hivemind"
				if username == botUsername {
					continue
				}

				body, _ := comment["body"].(string)
				lowerBody := strings.ToLower(body)
				isChangesRequested := strings.Contains(lowerBody, "changes requested") ||
					strings.Contains(lowerBody, "rework") ||
					strings.Contains(lowerBody, "fix") ||
					strings.Contains(lowerBody, "please fix") ||
					strings.Contains(lowerBody, "not ok") ||
					strings.Contains(lowerBody, "failing") ||
					strings.Contains(lowerBody, "typecheck")

				if isChangesRequested {
					if err := rm.DB.SetTicketReviewStatus(ctx, ticket.ID, "changes_requested", body); err != nil {
						slog.Error("failed to set review status for feedback", "ticket_id", ticket.ID, "error", err)
					}
					if err := rm.DB.RequeueTicket(ctx, ticket.ID, rm.Config.AgentMaxRetries); err != nil {
						slog.Error("failed to requeue ticket for feedback", "ticket_id", ticket.ID, "error", err)
					}
					rm.Broadcaster.Broadcast("ticket_requeued", map[string]string{
						"ticket_id": ticket.ID, "reason": "Review feedback",
					})
					slog.Info("review feedback → re-queue", "ticket_id", ticket.ID, "author", username)
					break
				}

				if strings.Contains(lowerBody, "approved") || strings.Contains(lowerBody, "lgtm") {
					if err := rm.DB.SetTicketReviewStatus(ctx, ticket.ID, "approved", body); err != nil {
						slog.Error("failed to set review status to approved", "ticket_id", ticket.ID, "error", err)
					}
					rm.Broadcaster.Broadcast("ticket_reviewed", map[string]string{
						"ticket_id": ticket.ID, "status": "approved",
					})
					slog.Info("review approved", "ticket_id", ticket.ID)
					break
				}
			}

			if len(comments) > 0 {
				maxID := 0
				for _, c := range comments {
					if id, ok := c["id"].(float64); ok && int(id) > maxID {
						maxID = int(id)
					}
				}
				if err := rm.DB.SetTicketMRLastNoteID(ctx, ticket.ID, maxID); err != nil {
					slog.Error("failed to update MR last note ID", "ticket_id", ticket.ID, "error", err)
				}
			}
		}
	}
	return nil
}