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

package api

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/vcs"
	"github.com/maelklingler/hivemind/internal/vcs/gitlab"
	"github.com/maelklingler/hivemind/internal/vcs/github"
)

var (
	webhookDedup   = make(map[string]time.Time)
	webhookDedupMu sync.Mutex
	webhookDedupTTL = 5 * time.Minute
)

func startDedupCleaner() {
	go func() {
		for {
			time.Sleep(webhookDedupTTL)
			webhookDedupMu.Lock()
			now := time.Now()
			for k, t := range webhookDedup {
				if now.Sub(t) > webhookDedupTTL {
					delete(webhookDedup, k)
				}
			}
			webhookDedupMu.Unlock()
		}
	}()
}

func init() {
	startDedupCleaner()
}

// isDuplicateWebhook checks the Redis dedup repo if available, otherwise
// falls back to the in-memory map.
func (s *Server) isDuplicateWebhook(eventID string) bool {
	if s.Dedup != nil {
		dup, err := s.Dedup.IsDuplicate(context.Background(), eventID, webhookDedupTTL)
		if err != nil {
			slog.Warn("redis dedup failed, falling back to in-memory", "error", err)
		} else {
			return dup
		}
	}
	return inMemoryIsDuplicate(eventID)
}

func inMemoryIsDuplicate(eventID string) bool {
	webhookDedupMu.Lock()
	defer webhookDedupMu.Unlock()
	if _, exists := webhookDedup[eventID]; exists {
		return true
	}
	webhookDedup[eventID] = time.Now()
	return false
}

func (s *Server) gitlabWebhook(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	eventType := r.Header.Get("X-Gitlab-Event")
	signature := r.Header.Get("X-Gitlab-Token")

	if s.Config.GitLabWebhookSecret != "" && !verifyGitlabWebhook(body, signature, s.Config.GitLabWebhookSecret) {
		writeError(w, http.StatusUnauthorized, "invalid webhook signature")
		return
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(body, &payload); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON")
		return
	}

	eventUUID := r.Header.Get("X-Gitlab-Event-UUID")
	if eventUUID != "" && s.isDuplicateWebhook(eventUUID) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "duplicate"})
		return
	}

	provider := gitlab.New(s.Config.GitLabHost, s.Config.GitLabToken)
	event := provider.ParseWebhookEvent(payload, map[string]string{"X-Gitlab-Event": eventType})
	if event == nil {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ignored"})
		return
	}

	ctx := r.Context()

	switch event.Type {
	case "issue":
		result := s.handleIssueWebhook(ctx, event, "GL")
		s.Broadcaster.Broadcast("ticket_created", result)
		writeJSON(w, http.StatusOK, result)

	case "merge_request":
		result := s.handleMRWebhook(ctx, event, provider)
		writeJSON(w, http.StatusOK, result)

	default:
		writeJSON(w, http.StatusOK, map[string]string{"status": "ignored", "event": eventType})
	}
}

func (s *Server) githubWebhook(w http.ResponseWriter, r *http.Request) {
	body := readBody(r)
	eventType := r.Header.Get("X-GitHub-Event")
	signature := r.Header.Get("X-Hub-Signature-256")

	githubSecret := s.Config.GitHubWebhookSecret
	if githubSecret != "" {
		if !verifyGitHubWebhook(body, signature, githubSecret) {
			writeError(w, http.StatusUnauthorized, "invalid webhook signature")
			return
		}
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(body, &payload); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON")
		return
	}

	deliveryID := r.Header.Get("X-GitHub-Delivery")
	if deliveryID != "" && s.isDuplicateWebhook(deliveryID) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "duplicate"})
		return
	}

	provider := github.New(s.Config.GitHubHost, s.Config.GitHubToken)
	event := provider.ParseWebhookEvent(payload, map[string]string{"X-GitHub-Event": eventType})
	if event == nil {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ignored"})
		return
	}

	ctx := r.Context()

	switch event.Type {
	case "issue":
		result := s.handleIssueWebhook(ctx, event, "GH")
		s.Broadcaster.Broadcast("ticket_created", result)
		writeJSON(w, http.StatusOK, result)

	case "merge_request":
		result := s.handleMRWebhook(ctx, event, provider)
		writeJSON(w, http.StatusOK, result)

	default:
		writeJSON(w, http.StatusOK, map[string]string{"status": "ignored", "event": eventType})
	}
}

func (s *Server) handleIssueWebhook(ctx context.Context, event *vcs.WebhookEvent, prefix string) map[string]interface{} {
	ticketID := fmt.Sprintf("%s-%v", prefix, event.IID)

	existing, err := s.DB.GetTicket(ctx, ticketID)
	if err == nil && existing != nil {
		return map[string]interface{}{"ok": true, "id": ticketID, "status": "already_exists"}
	}

	labels := event.Labels
	if labels == nil {
		labels = []string{}
	}

	input := &database.TicketInput{
		ID:          ticketID,
		Title:       event.Title,
		Description: event.Description,
		Labels:      labels,
		IssueType:   "Task",
		Priority:    "Medium",
	}

	if err := s.DB.CreateTicketAndEnqueue(ctx, input); err != nil {
		slog.Error("failed to create+enqueue ticket from webhook", "error", err)
		return map[string]interface{}{"ok": false, "error": err.Error()}
	}

	slog.Info("webhook created ticket", "id", ticketID, "title", event.Title, "source", prefix)
	return map[string]interface{}{"ok": true, "id": ticketID, "status": "queued", "title": event.Title}
}

func (s *Server) handleMRWebhook(ctx context.Context, event *vcs.WebhookEvent, provider vcs.VCSProvider) map[string]interface{} {
	sourceBranch := event.SourceBranch
	if sourceBranch == "" {
		return map[string]interface{}{"ok": false, "error": "no source branch"}
	}

	ticketID := extractTicketIDFromBranch(sourceBranch)
	if ticketID == "" {
		return map[string]interface{}{"ok": false, "error": "could not extract ticket from branch"}
	}

	ticket, err := s.DB.GetTicket(ctx, ticketID)
	if err != nil || ticket == nil {
		return map[string]interface{}{"ok": false, "error": "ticket not found: " + ticketID}
	}

	mrURL := event.URL
	if mrURL != "" {
		if err := s.DB.SetTicketMRURL(ctx, ticketID, mrURL); err != nil {
			slog.Error("failed to set ticket MR URL", "ticket_id", ticketID, "error", err)
		}
	}

	switch event.Action {
	case "merge", "close":
		if event.Action == "merge" || event.State == "merged" {
			if err := s.DB.UpdateTicketStatus(ctx, ticketID, "merged"); err != nil {
				slog.Error("failed to update ticket status to merged", "ticket_id", ticketID, "error", err)
			}
			if err := s.DB.SetTicketReviewStatus(ctx, ticketID, "approved", "MR merged: "+mrURL); err != nil {
				slog.Error("failed to set ticket review status", "ticket_id", ticketID, "error", err)
			}
			slog.Info("MR merged → ticket completed", "ticket_id", ticketID)
			s.Broadcaster.Broadcast("ticket_merged", map[string]string{"ticket_id": ticketID, "mr_url": mrURL})
			if s.K8s != nil {
				go s.K8s.CleanupAgentResources(context.Background(), ticketID)
			}
		} else {
			if err := s.DB.SetTicketReviewStatus(ctx, ticketID, "changes_requested", "MR closed without merge"); err != nil {
				slog.Error("failed to set review status", "ticket_id", ticketID, "error", err)
			}
			if err := s.DB.RequeueTicket(ctx, ticketID, s.Config.AgentMaxRetries); err != nil {
				slog.Error("failed to requeue ticket", "ticket_id", ticketID, "error", err)
			}
			slog.Info("MR closed → ticket re-queued", "ticket_id", ticketID)
			s.Broadcaster.Broadcast("ticket_requeued", map[string]string{"ticket_id": ticketID, "reason": "MR closed"})
		}

	case "reopen":
		if err := s.DB.SetTicketReviewStatus(ctx, ticketID, "changes_requested", "MR reopened: "+mrURL); err != nil {
			slog.Error("failed to set review status on reopen", "ticket_id", ticketID, "error", err)
		}
		if err := s.DB.UpdateTicketStatus(ctx, ticketID, "queued"); err != nil {
			slog.Error("failed to update ticket status on reopen", "ticket_id", ticketID, "error", err)
		}
		slog.Info("MR reopened → ticket re-queued", "ticket_id", ticketID)
		s.Broadcaster.Broadcast("ticket_requeued", map[string]string{"ticket_id": ticketID, "mr_url": mrURL})
	}

	return map[string]interface{}{"ok": true, "ticket_id": ticketID, "action": event.Action}
}

var ticketIDPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)PROJ-\d+`),
	regexp.MustCompile(`(?i)BUG-\d+`),
	regexp.MustCompile(`(?i)TASK-\d+`),
	regexp.MustCompile(`(?i)GL-\d+`),
	regexp.MustCompile(`(?i)GH-\d+`),
}

func extractTicketIDFromBranch(branch string) string {
	cleaned := strings.TrimPrefix(branch, "feature/")
	cleaned = strings.TrimPrefix(cleaned, "fix/")
	cleaned = strings.TrimPrefix(cleaned, "bugfix/")

	for _, re := range ticketIDPatterns {
		if m := re.FindString(cleaned); m != "" {
			return strings.ToUpper(m)
		}
	}
	return ""
}

func verifyGitlabWebhook(body []byte, signature string, secret string) bool {
	if secret == "" {
		return true
	}
	if signature == "" {
		return false
	}
	return hmac.Equal([]byte(signature), []byte(secret))
}

func verifyGitHubWebhook(body []byte, signature, secret string) bool {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	expected := "sha256=" + hex.EncodeToString(mac.Sum(nil))
	return hmac.Equal([]byte(signature), []byte(expected))
}

func readBody(r *http.Request) []byte {
	body := make([]byte, 0)
	if r.Body != nil {
		buf := make([]byte, 4096)
		for {
			n, err := r.Body.Read(buf)
			if n > 0 {
				body = append(body, buf[:n]...)
			}
			if err != nil {
				break
			}
		}
	}
	return body
}