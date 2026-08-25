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
	"encoding/json"
	"fmt"
	"io/fs"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	chiMiddleware "github.com/go-chi/chi/v5/middleware"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/k8s"
	"github.com/maelklingler/hivemind/internal/middleware"
	"github.com/maelklingler/hivemind/internal/models"
	"github.com/maelklingler/hivemind/internal/sse"
	"github.com/maelklingler/hivemind/internal/vcs"
	"github.com/maelklingler/hivemind/internal/vcs/github"
	"github.com/maelklingler/hivemind/internal/vcs/gitlab"
)

type Server struct {
	Config         *config.Config
	DB             *database.DB
	K8s            *k8s.Client
	Router         *chi.Mux
	Broadcaster    *sse.Broadcaster
	Dedup          repository.DedupRepository
	RateLimiter    repository.RateLimitRepository
	PipelineEngine PipelineEngineInterface
	Shutdown       context.CancelFunc
	staticFS       fs.FS
}

func (s *Server) k8sOrError(w http.ResponseWriter) *k8s.Client {
	if s.K8s == nil {
		writeError(w, http.StatusServiceUnavailable, "kubernetes not available")
		return nil
	}
	return s.K8s
}

func NewServer(cfg *config.Config, db *database.DB, k8sClient *k8s.Client, broadcaster *sse.Broadcaster, dedup repository.DedupRepository, rateLimiter repository.RateLimitRepository, pipelineEngine PipelineEngineInterface, staticFS fs.FS) *Server {
	s := &Server{
		Config:         cfg,
		DB:             db,
		K8s:            k8sClient,
		Broadcaster:    broadcaster,
		Dedup:          dedup,
		RateLimiter:    rateLimiter,
		PipelineEngine: pipelineEngine,
		staticFS:       staticFS,
	}

	r := chi.NewRouter()
	r.Use(chiMiddleware.RequestID)
	r.Use(chiMiddleware.RealIP)
	r.Use(chiMiddleware.Logger)
	r.Use(chiMiddleware.Recoverer)
	r.Use(chiMiddleware.Timeout(60 * time.Second))
	r.Use(middleware.CORS(cfg.CORSOrigins))
	r.Use(middleware.APIKeyAuth(cfg.HivemindAPIKey))
	r.Use(middleware.RateLimitWithRedis(cfg.RateLimitPerMinute, s.RateLimiter))
	r.Use(middleware.MaxBodySize(10 * 1024 * 1024))

	r.Get("/healthz", s.healthz)
	r.Get("/readyz", s.readyz)
	r.Get("/metrics", s.prometheusMetrics)
	r.Get("/api/metrics", s.metrics)

	r.Route("/api", func(r chi.Router) {
		// Tickets
		r.Get("/tickets", s.listTickets)
		r.Post("/tickets", s.createTicket)
		r.Post("/tickets/preview", s.previewTicket)
		r.Get("/tickets/{id}", s.getTicket)
		r.Patch("/tickets/{id}", s.updateTicket)
		r.Post("/tickets/{id}/reopen", s.reopenTicket)
		r.Post("/tickets/{id}/stop", s.stopTicket)
		r.Post("/tickets/{id}/review", s.submitReview)
		r.Post("/tickets/{id}/mr", s.setTicketMR)
		r.Get("/tickets/{id}/logs", s.getTicketLogs)
		r.Get("/tickets/{id}/comments", s.listTicketComments)
		r.Post("/tickets/{id}/comments", s.createTicketComment)

		// Agents
		r.Get("/agents", s.listAgents)
		r.Post("/agents/{id}/progress", s.updateAgentProgress)
		r.Post("/agents/{id}/complete", s.completeAgentTask)

		// Agent profiles
		r.Get("/agent-profiles", s.listAgentProfiles)
		r.Post("/agent-profiles", s.createAgentProfile)

		// Agent memory
		r.Get("/agent-memory/{id}", s.getAgentMemory)
		r.Post("/agent-memory/{id}", s.updateAgentMemory)
		r.Delete("/agent-memory/{id}", s.deleteAgentMemory)

		// Queue
		r.Get("/queue", s.getQueue)

		// Repos
		r.Get("/repos", s.listRepos)
		r.Post("/repos", s.addRepo)
		r.Patch("/repos", s.bulkUpdateRepos)
		r.Get("/repos/gitlab-projects", s.listGitLabProjects)
		r.Post("/repos/import-selected", s.importSelectedRepos)
		r.Post("/repos/init-from-gitlab", s.initFromGitLab)
		r.Get("/repos/{name}", s.getRepo)
		r.Put("/repos/{name}", s.updateRepo)
		r.Patch("/repos/{name}", s.patchRepo)
		r.Delete("/repos/{name}", s.deleteRepo)
		r.Get("/repos/{name}/branches", s.listRepoBranches)
		r.Post("/repos/{name}/activate", s.activateRepo)
		r.Post("/repos/{name}/deactivate", s.deactivateRepo)

		r.Get("/settings", s.getSettings)
		r.Post("/settings", s.updateSettings)

		// Config
		r.Get("/config", s.getConfig)

		// Repo names
		r.Get("/repo-names", s.getRepoNames)

		// Steps
		r.Get("/steps", s.getSteps)

		// Agent instructions
		r.Get("/agent-instructions", s.listAgentInstructions)
		r.Post("/agent-instructions", s.createAgentInstruction)
		r.Patch("/agent-instructions/{id}", s.updateAgentInstruction)
		r.Delete("/agent-instructions/{id}", s.deleteAgentInstruction)

		// MCP Servers
		r.Get("/mcp-servers", s.listMCPServers)
		r.Post("/mcp-servers", s.createMCPServer)
		r.Patch("/mcp-servers/{id}", s.updateMCPServer)
		r.Delete("/mcp-servers/{id}", s.deleteMCPServer)

		// Plugins
		r.Get("/plugins", s.listPlugins)
		r.Post("/plugins", s.createPlugin)
		r.Patch("/plugins/{id}", s.updatePlugin)
		r.Delete("/plugins/{id}", s.deletePlugin)

		// Agent error reporting
		r.Post("/agents/{id}/error", s.reportAgentError)

		// Stream (delegates to Broadcaster.ServeHTTP)
		r.Get("/stream", s.Broadcaster.ServeHTTP)

		// Pipeline
		r.Get("/phases", s.listPhases)
		r.Get("/roles", s.listRoles)
		r.Get("/roles/{role}/instruction", s.getRoleInstruction)
		r.Get("/tickets/{id}/pipeline", s.getTicketPipeline)
		r.Post("/agents/{id}/phase_complete", s.phaseComplete)
		r.Post("/agents/{id}/phase_fail", s.phaseFail)

		// Ticket hierarchy + approval
		r.Post("/tickets/{id}/decompose", s.decomposeTicket)
		r.Post("/tickets/{id}/approve", s.approveTicket)
		r.Post("/tickets/{id}/reject", s.rejectTicket)
		r.Get("/tickets/{id}/children", s.listChildren)
		r.Get("/approvals/pending", s.listPendingApprovals)

		// Groups (team channel)
		r.Get("/groups/{id}/messages", s.listGroupMessages)
		r.Post("/groups/{id}/messages", s.addGroupMessage)
	})

	// VCS Webhooks
	r.Post("/webhooks/gitlab", s.gitlabWebhook)
	r.Post("/webhooks/github", s.githubWebhook)

	// Agent session proxy (HTTP reverse proxy to agent pod)
	r.Handle("/agent-session/{ticketID}/*", http.HandlerFunc(s.agentSessionProxy))

	// Static assets (CSS, JS)
	r.Handle("/static/*", http.StripPrefix("/static/", http.FileServer(http.FS(s.staticFS))))

	// Favicon (empty to prevent 404)
	r.Get("/favicon.ico", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "image/x-icon")
		w.WriteHeader(http.StatusNoContent)
	})

	// SPA fallback — serves index.html for all non-API, non-static routes
	r.Get("/*", s.serveSPA)

	s.Router = r
	return s
}

func (s *Server) healthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if _, err := s.DB.ListAgents(ctx); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "not ready"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) metrics(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	m, err := s.DB.GetMetricsSummary(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to get metrics: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, m)
}

func (s *Server) prometheusMetrics(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	m, err := s.DB.GetMetricsSummary(ctx)
	if err != nil {
		http.Error(w, "failed to get metrics", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	fmt.Fprintf(w, "# HELP hivemind_total_tickets Total number of tickets\n")
	fmt.Fprintf(w, "# TYPE hivemind_total_tickets gauge\n")
	fmt.Fprintf(w, "hivemind_total_tickets %d\n", m.TotalTickets)
	fmt.Fprintf(w, "# HELP hivemind_completed_tickets Completed tickets\n")
	fmt.Fprintf(w, "# TYPE hivemind_completed_tickets gauge\n")
	fmt.Fprintf(w, "hivemind_completed_tickets %d\n", m.CompletedTickets)
	fmt.Fprintf(w, "# HELP hivemind_failed_tickets Failed tickets\n")
	fmt.Fprintf(w, "# TYPE hivemind_failed_tickets gauge\n")
	fmt.Fprintf(w, "hivemind_failed_tickets %d\n", m.FailedTickets)
	fmt.Fprintf(w, "# HELP hivemind_merged_tickets Merged tickets\n")
	fmt.Fprintf(w, "# TYPE hivemind_merged_tickets gauge\n")
	fmt.Fprintf(w, "hivemind_merged_tickets %d\n", m.MergedTickets)
	fmt.Fprintf(w, "# HELP hivemind_total_retries Total retries\n")
	fmt.Fprintf(w, "# TYPE hivemind_total_retries gauge\n")
	fmt.Fprintf(w, "hivemind_total_retries %d\n", m.TotalRetries)
	fmt.Fprintf(w, "# HELP hivemind_avg_review_cycles Average review cycles\n")
	fmt.Fprintf(w, "# TYPE hivemind_avg_review_cycles gauge\n")
	fmt.Fprintf(w, "hivemind_avg_review_cycles %.2f\n", m.AvgReviewCycles)
	fmt.Fprintf(w, "# HELP hivemind_total_prompt_tokens Total LLM prompt tokens\n")
	fmt.Fprintf(w, "# TYPE hivemind_total_prompt_tokens gauge\n")
	fmt.Fprintf(w, "hivemind_total_prompt_tokens %d\n", m.TotalPromptTokens)
	fmt.Fprintf(w, "# HELP hivemind_total_completion_tokens Total LLM completion tokens\n")
	fmt.Fprintf(w, "# TYPE hivemind_total_completion_tokens gauge\n")
	fmt.Fprintf(w, "hivemind_total_completion_tokens %d\n", m.TotalCompletionTokens)
	fmt.Fprintf(w, "# HELP hivemind_total_llm_cost_usd Total LLM cost in USD\n")
	fmt.Fprintf(w, "# TYPE hivemind_total_llm_cost_usd gauge\n")
	fmt.Fprintf(w, "hivemind_total_llm_cost_usd %.6f\n", m.TotalLLMCostUSD)
}

func (s *Server) listTickets(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	status := r.URL.Query().Get("status")
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	if offset < 0 {
		offset = 0
	}
	tickets, err := s.DB.ListTickets(ctx, status)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list tickets: "+err.Error())
		return
	}
	if offset > len(tickets) {
		offset = len(tickets)
	}
	end := offset + limit
	if end > len(tickets) {
		end = len(tickets)
	}
	writeJSON(w, http.StatusOK, tickets[offset:end])
}

func (s *Server) createTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req struct {
		ID          string   `json:"id"`
		Title       string   `json:"title"`
		Description string   `json:"description"`
		Labels      []string `json:"labels"`
		IssueType   string   `json:"issue_type"`
		Priority    string   `json:"priority"`
		TicketType  string   `json:"ticket_type"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.ID == "" || req.Title == "" {
		writeError(w, http.StatusBadRequest, "id and title are required")
		return
	}
	if len(req.ID) > 128 || len(req.Title) > 512 {
		writeError(w, http.StatusBadRequest, "id max 128 chars, title max 512 chars")
		return
	}
	validPriorities := map[string]bool{"": true, "Low": true, "Medium": true, "High": true, "Critical": true}
	if !validPriorities[req.Priority] {
		writeError(w, http.StatusBadRequest, "invalid priority")
		return
	}
	validIssueTypes := map[string]bool{"": true, "Task": true, "Bug": true, "Feature": true, "Improvement": true}
	if !validIssueTypes[req.IssueType] {
		writeError(w, http.StatusBadRequest, "invalid issue_type")
		return
	}
	ticket := &database.TicketInput{
		ID:          req.ID,
		Title:       req.Title,
		Description: req.Description,
		Labels:      req.Labels,
		IssueType:   req.IssueType,
		Priority:    req.Priority,
		TicketType:  req.TicketType,
	}
	if err := s.DB.CreateTicketAndEnqueue(ctx, ticket); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create ticket: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"id": req.ID, "status": "queued"})
}

func (s *Server) previewTicket(w http.ResponseWriter, r *http.Request) {
	// Dry-run: LLM analysis without spawning pod
	writeJSON(w, http.StatusOK, map[string]string{"status": "preview_not_implemented"})
}

func (s *Server) getTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	ticket, err := s.DB.GetTicket(ctx, id)
	if err != nil {
		writeError(w, http.StatusNotFound, "ticket not found")
		return
	}
	writeJSON(w, http.StatusOK, ticket)
}

func (s *Server) updateTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	ticket, err := s.DB.GetTicket(ctx, id)
	if err != nil {
		writeError(w, http.StatusNotFound, "ticket not found")
		return
	}
	if status, ok := req["status"].(string); ok {
		if err := s.DB.UpdateTicketStatus(ctx, id, status); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to update status: "+err.Error())
			return
		}
		s.Broadcaster.Broadcast("queue_updated", nil)
	}
	_ = ticket
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "updated"})
}

func (s *Server) reopenTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	if err := s.DB.UpdateTicketStatus(ctx, id, "queued"); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to reopen ticket: "+err.Error())
		return
	}
	s.Broadcaster.Broadcast("ticket_requeued", map[string]string{"ticket_id": id, "reason": "Manually reopened"})
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "queued"})
}

func (s *Server) stopTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	if err := s.DB.UpdateTicketStatus(ctx, id, "stopped"); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to stop ticket: "+err.Error())
		return
	}
	kc := s.k8sOrError(w)
	if kc != nil {
		podName := "agent-worker-" + strings.ToLower(id)
		kc.DeletePod(ctx, podName)
		kc.CleanupAgentResources(ctx, id)
	}
	s.Broadcaster.Broadcast("ticket_stopped", map[string]string{"ticket_id": id})
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "stopped"})
}

func (s *Server) submitReview(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		Status string `json:"status"`
		Notes  string `json:"notes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := s.DB.SetTicketReviewStatus(ctx, id, req.Status, req.Notes); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to submit review: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "review_status": req.Status})
}

func (s *Server) setTicketMR(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		MRURL string `json:"mr_url"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := s.DB.SetTicketMRURL(ctx, id, req.MRURL); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to set MR URL: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "mr_url": req.MRURL})
}

func (s *Server) getTicketLogs(w http.ResponseWriter, r *http.Request) {
	kc := s.k8sOrError(w)
	if kc == nil {
		return
	}
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	tailLines := int64(100)
	if t := r.URL.Query().Get("tail"); t != "" {
		if v, err := strconv.ParseInt(t, 10, 64); err == nil {
			tailLines = v
		}
	}
	podName := "agent-worker-" + strings.ToLower(id)
	logs, err := kc.GetPodLogs(ctx, podName, tailLines)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{"logs": "", "status": "error", "pod": podName})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"logs": logs, "status": "ok", "pod": podName})
}

func (s *Server) listTicketComments(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	comments, err := s.DB.ListTicketComments(ctx, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list comments: "+err.Error())
		return
	}
	if comments == nil {
		comments = []*models.TicketComment{}
	}
	writeJSON(w, http.StatusOK, comments)
}

func (s *Server) createTicketComment(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		Author  string `json:"author"`
		Content string `json:"content"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.Content == "" {
		writeError(w, http.StatusBadRequest, "content is required")
		return
	}
	if err := s.DB.AddTicketComment(ctx, id, req.Author, "comment", req.Content); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create comment: "+err.Error())
		return
	}
	s.Broadcaster.Broadcast("ticket_comment", map[string]string{"ticket_id": id, "author": req.Author})
	writeJSON(w, http.StatusCreated, map[string]string{"ticket_id": id, "status": "created"})
}

func (s *Server) listAgents(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	agents, err := s.DB.ListAgents(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list agents: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, agents)
}

var validAgentTransitions = map[string][]string{
	"idle":    {"running"},
	"running": {"idle", "completed", "failed"},
	"completed": {"idle"},
	"failed":    {"idle"},
}

func isValidAgentTransition(from, to string) bool {
	allowed, ok := validAgentTransitions[from]
	if !ok {
		return true
	}
	for _, t := range allowed {
		if t == to {
			return true
		}
	}
	return false
}

func (s *Server) updateAgentProgress(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		Progress string `json:"progress"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := s.DB.UpdateAgentProgress(ctx, id, req.Progress); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to update agent progress: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "updated"})
}

func (s *Server) completeAgentTask(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		TicketID           string  `json:"ticket_id"`
		Status             string  `json:"status"`
		LLMPromptTokens    int     `json:"llm_prompt_tokens"`
		LLMCompletionTokens int    `json:"llm_completion_tokens"`
		LLMTotalCostUSD    float64 `json:"llm_total_cost_usd"`
		LinesAdded         int     `json:"lines_added"`
		LinesRemoved       int     `json:"lines_removed"`
		FilesChanged       int     `json:"files_changed"`
		ModelUsed          string  `json:"model_used"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}

	agent, err := s.DB.GetAgent(ctx, id)
	if err != nil || agent == nil {
		writeError(w, http.StatusNotFound, "agent not found")
		return
	}

	newStatus := "idle"
	if req.Status != "" {
		newStatus = req.Status
	}
	if !isValidAgentTransition(string(agent.Status), newStatus) {
		writeError(w, http.StatusBadRequest, fmt.Sprintf("invalid transition from %s to %s", agent.Status, newStatus))
		return
	}

	if req.TicketID != "" {
		if err := s.DB.CompleteAgentTaskTx(ctx, id, req.TicketID, newStatus,
			req.LLMPromptTokens, req.LLMCompletionTokens, req.LLMTotalCostUSD,
			req.LinesAdded, req.LinesRemoved, req.FilesChanged); err != nil {
			slog.Error("failed to complete agent task transactionally", "ticket_id", req.TicketID, "error", err)
		} else {
			s.Broadcaster.Broadcast("ticket_completed", map[string]string{"ticket_id": req.TicketID})
		}
	} else {
		if err := s.DB.SetAgentStatus(ctx, id, newStatus); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to set agent status: "+err.Error())
			return
		}
	}
	kc := s.k8sOrError(w)
	if kc != nil && req.TicketID != "" {
		podName := "agent-worker-" + strings.ToLower(req.TicketID)
		kc.DeletePod(ctx, podName)
		kc.CleanupAgentResources(ctx, req.TicketID)
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": newStatus})
}

func (s *Server) getConfig(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{
		"version":       "0.1.0",
		"vcs_provider":  s.Config.VCSProvider,
		"agent_image":   s.Config.AgentImage,
		"namespace":     s.Config.AgentNamespace,
		"model_routing": fmt.Sprintf("%v", s.Config.ModelRoutingEnabled),
		"simple_model":  s.Config.SimpleModel,
		"complex_model": s.Config.ComplexModel,
	})
}

func (s *Server) getRepoNames(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	repos, err := s.DB.ListRepos(ctx, false)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list repos: "+err.Error())
		return
	}
	names := make([]string, 0, len(repos))
	for _, repo := range repos {
		names = append(names, repo.Name)
	}
	writeJSON(w, http.StatusOK, names)
}

func (s *Server) getSteps(w http.ResponseWriter, r *http.Request) {
	steps := []map[string]interface{}{
		{"id": "plan", "name": "Plan", "order": 1},
		{"id": "work", "name": "Work", "order": 2},
		{"id": "review", "name": "Review", "order": 3},
		{"id": "ship", "name": "Ship", "order": 4},
	}
	writeJSON(w, http.StatusOK, steps)
}

func (s *Server) listAgentInstructions(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	instructions, err := s.DB.ListInstructionsDB(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list instructions: "+err.Error())
		return
	}
	if instructions == nil {
		instructions = []*repository.Instruction{}
	}
	writeJSON(w, http.StatusOK, instructions)
}

func (s *Server) createAgentInstruction(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in repository.InstructionInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if in.Name == "" || in.Content == "" {
		writeError(w, http.StatusBadRequest, "name and content are required")
		return
	}
	id, err := s.DB.CreateInstructionDB(ctx, &in)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create instruction: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"id": id, "status": "created"})
}

func (s *Server) updateAgentInstruction(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var in repository.InstructionInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := s.DB.UpdateInstructionDB(ctx, id, &in); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to update instruction: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "updated"})
}

func (s *Server) deleteAgentInstruction(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	if err := s.DB.DeleteInstructionDB(ctx, id); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to delete instruction: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "deleted"})
}

func (s *Server) reportAgentError(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	agentID := chi.URLParam(r, "id")
	var req struct {
		TicketID string `json:"ticket_id"`
		Error    string `json:"error"`
		Phase    string `json:"phase"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.TicketID == "" {
		writeError(w, http.StatusBadRequest, "ticket_id is required")
		return
	}
	slog.Warn("agent reported error", "agent_id", agentID, "ticket_id", req.TicketID, "error", req.Error, "phase", req.Phase)
	if err := s.DB.SetAgentStatus(ctx, agentID, "error"); err != nil {
		slog.Error("failed to set agent error status", "agent_id", agentID, "error", err)
	}
	if err := s.DB.RequeueTicket(ctx, req.TicketID, s.Config.AgentMaxRetries); err != nil {
		slog.Error("failed to requeue ticket after agent error", "ticket_id", req.TicketID, "error", err)
	}
	s.Broadcaster.Broadcast("ticket_requeued", map[string]string{
		"ticket_id": req.TicketID, "reason": "agent error: " + req.Error,
	})
	writeJSON(w, http.StatusOK, map[string]string{"agent_id": agentID, "ticket_id": req.TicketID, "status": "requeued"})
}

func (s *Server) listAgentProfiles(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	profiles, err := s.DB.ListAgentProfilesDB(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list agent profiles: "+err.Error())
		return
	}
	if profiles == nil {
		profiles = []*models.AgentProfile{}
	}
	writeJSON(w, http.StatusOK, profiles)
}

func (s *Server) createAgentProfile(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var p models.AgentProfile
	if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if p.ID == "" || p.Name == "" {
		writeError(w, http.StatusBadRequest, "id and name are required")
		return
	}
	if err := s.DB.CreateAgentProfileDB(ctx, &p); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create agent profile: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, p)
}

func (s *Server) getAgentMemory(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	blocks, err := s.DB.ListMemoryBlocksDB(ctx, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list memory blocks: "+err.Error())
		return
	}
	if blocks == nil {
		blocks = []*repository.MemoryBlock{}
	}
	writeJSON(w, http.StatusOK, blocks)
}

func (s *Server) updateAgentMemory(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var in repository.MemoryBlockInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if in.Label == "" {
		writeError(w, http.StatusBadRequest, "label is required")
		return
	}
	if err := s.DB.SetMemoryBlockDB(ctx, id, &in); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to set memory block: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"agent_id": id, "label": in.Label, "status": "updated"})
}

func (s *Server) deleteAgentMemory(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	label := r.URL.Query().Get("label")
	if label == "" {
		writeError(w, http.StatusBadRequest, "label query parameter is required")
		return
	}
	if err := s.DB.DeleteMemoryBlockDB(ctx, id, label); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to delete memory block: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"agent_id": id, "label": label, "status": "deleted"})
}

func (s *Server) getQueue(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	queue, err := s.DB.GetQueue(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to get queue: "+err.Error())
		return
	}
	if queue == nil {
		queue = []*models.QueueItem{}
	}
	writeJSON(w, http.StatusOK, queue)
}

func (s *Server) listRepos(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	activeOnly := r.URL.Query().Get("active") == "true"
	repos, err := s.DB.ListRepos(ctx, activeOnly)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list repos: "+err.Error())
		return
	}
	if repos == nil {
		repos = []*models.Repo{}
	}
	writeJSON(w, http.StatusOK, repos)
}

func (s *Server) addRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var repo database.RepoInput
	if err := json.NewDecoder(r.Body).Decode(&repo); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if repo.Name == "" || repo.URL == "" {
		writeError(w, http.StatusBadRequest, "name and url are required")
		return
	}
	if err := s.DB.AddRepo(ctx, &repo); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to add repo: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"name": repo.Name, "status": "created"})
}

func (s *Server) bulkUpdateRepos(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var repos []database.RepoInput
	if err := json.NewDecoder(r.Body).Decode(&repos); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	for _, repo := range repos {
		if err := s.DB.UpdateRepoDB(ctx, &repo); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to update repo "+repo.Name+": "+err.Error())
			return
		}
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"status": "ok", "updated": len(repos)})
}

func (s *Server) getRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	repo, err := s.DB.GetRepo(ctx, name)
	if err != nil {
		writeError(w, http.StatusNotFound, "repo not found")
		return
	}
	writeJSON(w, http.StatusOK, repo)
}

func (s *Server) updateRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	var repo database.RepoInput
	if err := json.NewDecoder(r.Body).Decode(&repo); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	repo.Name = name
	if err := s.DB.UpdateRepoDB(ctx, &repo); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to update repo: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"name": name, "status": "updated"})
}

func (s *Server) patchRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	var patch map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&patch); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	allowed := map[string]bool{"url": true, "branch": true, "description": true, "active": true}
	filtered := make(map[string]interface{})
	for k, v := range patch {
		if allowed[k] {
			filtered[k] = v
		}
	}
	if err := s.DB.PatchRepoDB(ctx, name, filtered); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to patch repo: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"name": name, "status": "patched"})
}

func (s *Server) deleteRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	if err := s.DB.DeleteRepo(ctx, name); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to delete repo: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"name": name, "status": "deleted"})
}

func (s *Server) listRepoBranches(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	repo, err := s.DB.GetRepo(ctx, name)
	if err != nil {
		writeError(w, http.StatusNotFound, "repo not found")
		return
	}
	provider := s.buildVCSProvider()
	if provider == nil {
		writeError(w, http.StatusServiceUnavailable, "VCS provider not configured")
		return
	}
	projectPath := extractProjectPath(repo.URL, provider)
	branches, err := provider.ListBranches(ctx, projectPath)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list branches: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, branches)
}

func (s *Server) activateRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	if err := s.DB.SetRepoActiveDB(ctx, name, true); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to activate repo: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"name": name, "status": "active"})
}

func (s *Server) deactivateRepo(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	name := chi.URLParam(r, "name")
	if err := s.DB.SetRepoActiveDB(ctx, name, false); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to deactivate repo: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"name": name, "status": "inactive"})
}

func (s *Server) listGitLabProjects(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	provider := s.buildVCSProvider()
	if provider == nil {
		writeError(w, http.StatusServiceUnavailable, "VCS provider not configured")
		return
	}
	projects, err := provider.ListProjects(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list projects: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, projects)
}

func (s *Server) importSelectedRepos(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var req struct {
		Repos []struct {
			Name        string `json:"name"`
			URL         string `json:"url"`
			Branch      string `json:"branch"`
			Description string `json:"description"`
		} `json:"repos"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	imported := 0
	for _, r := range req.Repos {
		in := &database.RepoInput{
			Name: r.Name, URL: r.URL, Branch: r.Branch, Description: r.Description, Active: true,
		}
		if in.Branch == "" {
			in.Branch = "development"
		}
		if err := s.DB.AddRepo(ctx, in); err != nil {
			slog.Warn("failed to import repo", "name", r.Name, "error", err)
			continue
		}
		imported++
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"status": "ok", "imported": imported})
}

func (s *Server) initFromGitLab(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	provider := s.buildVCSProvider()
	if provider == nil {
		writeError(w, http.StatusServiceUnavailable, "VCS provider not configured")
		return
	}
	projects, err := provider.ListProjects(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list projects: "+err.Error())
		return
	}
	imported := 0
	for _, p := range projects {
		name, _ := p["name"].(string)
		url, _ := p["url"].(string)
		if name == "" || url == "" {
			continue
		}
		in := &database.RepoInput{Name: name, URL: url, Branch: "development", Active: true}
		if err := s.DB.AddRepo(ctx, in); err != nil {
			slog.Warn("failed to import repo", "name", name, "error", err)
			continue
		}
		imported++
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{"status": "ok", "imported": imported})
}

func (s *Server) buildVCSProvider() vcs.VCSProvider {
	vcsProvider := s.Config.VCSProvider
	gitlabHost := s.Config.GitLabHost
	gitlabToken := s.Config.GitLabToken
	githubToken := s.Config.GitHubToken
	githubHost := s.Config.GitHubHost

	if s.DB != nil {
		if v, err := s.DB.GetSetting(context.Background(), "vcs_provider"); err == nil && v != "" {
			vcsProvider = v
		}
		if v, err := s.DB.GetSetting(context.Background(), "gitlab_host"); err == nil && v != "" {
			gitlabHost = v
		}
		if v, err := s.DB.GetSetting(context.Background(), "gitlab_token"); err == nil && v != "" {
			gitlabToken = v
		}
		if v, err := s.DB.GetSetting(context.Background(), "git_token"); err == nil && v != "" {
			githubToken = v
		}
	}

	if vcsProvider == "github" {
		return github.New(githubHost, githubToken)
	}
	return gitlab.New(gitlabHost, gitlabToken)
}

func extractProjectPath(repoURL string, provider vcs.VCSProvider) string {
	host := provider.GetHost()
	if host == "" {
		return ""
	}
	hostIdx := indexOf(repoURL, host)
	if hostIdx < 0 {
		return ""
	}
	rest := repoURL[hostIdx+len(host):]
	rest = trimPrefix(rest, "/")
	rest = trimSuffix(rest, ".git")
	return rest
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

func trimPrefix(s, prefix string) string {
	if len(s) >= len(prefix) && s[:len(prefix)] == prefix {
		return s[len(prefix):]
	}
	return s
}

func trimSuffix(s, suffix string) string {
	if len(s) >= len(suffix) && s[len(s)-len(suffix):] == suffix {
		return s[:len(s)-len(suffix)]
	}
	return s
}

func (s *Server) getSettings(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	settings, err := s.DB.GetAllSettings(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to get settings: "+err.Error())
		return
	}
	masked := config.MaskSettings(settings)
	result := make([]map[string]string, 0, len(masked))
	for k, v := range masked {
		result = append(result, map[string]string{"key": k, "value": v})
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) updateSettings(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var settings map[string]string
	if err := json.NewDecoder(r.Body).Decode(&settings); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	for k, v := range settings {
		if !config.ValidateSettingKey(k) {
			writeError(w, http.StatusBadRequest, "setting key not allowed: "+k)
			return
		}
		if err := s.DB.SetSetting(ctx, k, v); err != nil {
			writeError(w, http.StatusInternalServerError, "failed to set setting: "+err.Error())
			return
		}
	}
	writeJSON(w, http.StatusOK, config.MaskSettings(settings))
}

func (s *Server) listMCPServers(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	servers, err := s.DB.ListMCPServers(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list MCP servers: "+err.Error())
		return
	}
	if servers == nil {
		servers = []*models.MCPServer{}
	}
	writeJSON(w, http.StatusOK, servers)
}

func (s *Server) createMCPServer(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in repository.MCPServerInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if in.Name == "" || in.Command == "" {
		writeError(w, http.StatusBadRequest, "name and command are required")
		return
	}
	id, err := s.DB.CreateMCPServerDB(ctx, &in)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create MCP server: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"id": id, "status": "created"})
}

func (s *Server) updateMCPServer(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var in repository.MCPServerInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := s.DB.UpdateMCPServerDB(ctx, id, &in); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to update MCP server: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "updated"})
}

func (s *Server) deleteMCPServer(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	if err := s.DB.DeleteMCPServerDB(ctx, id); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to delete MCP server: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "deleted"})
}

func (s *Server) listPlugins(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	plugins, err := s.DB.ListPluginsDB(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list plugins: "+err.Error())
		return
	}
	if plugins == nil {
		plugins = []*repository.Plugin{}
	}
	writeJSON(w, http.StatusOK, plugins)
}

func (s *Server) createPlugin(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in repository.PluginInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if in.Name == "" {
		writeError(w, http.StatusBadRequest, "name is required")
		return
	}
	id, err := s.DB.CreatePluginDB(ctx, &in)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to create plugin: "+err.Error())
		return
	}
	writeJSON(w, http.StatusCreated, map[string]string{"id": id, "status": "created"})
}

func (s *Server) updatePlugin(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var in repository.PluginInput
	if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if err := s.DB.UpdatePluginDB(ctx, id, &in); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to update plugin: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "updated"})
}

func (s *Server) deletePlugin(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	if err := s.DB.DeletePluginDB(ctx, id); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to delete plugin: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"id": id, "status": "deleted"})
}



func (s *Server) agentSessionProxy(w http.ResponseWriter, r *http.Request) {
	kc := s.k8sOrError(w)
	if kc == nil {
		return
	}
	ctx := r.Context()
	ticketID := chi.URLParam(r, "ticketID")
	podName := "agent-worker-" + strings.ToLower(ticketID)
	podIP, err := kc.GetPodIP(ctx, podName)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to get pod: "+err.Error())
		return
	}
	if podIP == "" {
		writeError(w, http.StatusNotFound, "agent pod not found or has no IP")
		return
	}
	target := fmt.Sprintf("http://%s:4096", podIP)
	proxy := httputil.NewSingleHostReverseProxy(mustParseURL(target))
	proxy.FlushInterval = -1
	r.URL.Path = "/" + chi.URLParam(r, "*")
	proxy.ServeHTTP(w, r)
}

func mustParseURL(raw string) *url.URL {
	u, err := url.Parse(raw)
	if err != nil {
		panic(err)
	}
	return u
}

// --- Pipeline handlers ---

func (s *Server) listPhases(w http.ResponseWriter, r *http.Request) {
	phases := []map[string]interface{}{
		{"id": "work", "name": "Work", "order": 1, "role": "developer"},
		{"id": "test", "name": "Test", "order": 2, "role": "qa"},
		{"id": "review", "name": "Review", "order": 3, "role": "reviewer"},
		{"id": "ship", "name": "Ship", "order": 4, "role": "release"},
		{"id": "listen", "name": "Listen", "order": 5, "role": "monitor"},
	}
	writeJSON(w, http.StatusOK, phases)
}

func (s *Server) listRoles(w http.ResponseWriter, r *http.Request) {
	roles := []map[string]interface{}{
		{"id": "developer", "name": "Developer", "instruction": "Implement the requested changes."},
		{"id": "qa", "name": "QA", "instruction": "Run tests and verify the implementation."},
		{"id": "reviewer", "name": "Reviewer", "instruction": "Review code quality and correctness."},
		{"id": "release", "name": "Release", "instruction": "Create MR and handle pipeline."},
		{"id": "monitor", "name": "Monitor", "instruction": "Watch MR feedback and respond."},
	}
	writeJSON(w, http.StatusOK, roles)
}

func (s *Server) getRoleInstruction(w http.ResponseWriter, r *http.Request) {
	role := chi.URLParam(r, "role")
	instructions := map[string]string{
		"developer": "Implement the requested changes. Write tests. Follow project conventions.",
		"qa":        "Run all tests. Report failures with context. Suggest fixes.",
		"reviewer":  "Review code quality, correctness, and adherence to conventions.",
		"release":   "Create a merge request with a clear description. Monitor pipeline.",
		"monitor":   "Watch MR feedback. Respond to comments. Handle requested changes.",
	}
	instr, ok := instructions[role]
	if !ok {
		writeError(w, http.StatusNotFound, "unknown role")
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"role": role, "instruction": instr})
}

func (s *Server) getTicketPipeline(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	steps, err := s.DB.ListPipelineSteps(ctx, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list pipeline steps: "+err.Error())
		return
	}
	if steps == nil {
		steps = []*repository.PipelineStep{}
	}
	writeJSON(w, http.StatusOK, steps)
}

func (s *Server) phaseComplete(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	agentID := chi.URLParam(r, "id")
	var req struct {
		TicketID    string `json:"ticket_id"`
		Phase       string `json:"phase"`
		Result      string `json:"result"`
		LLMUsage    string `json:"llm_usage"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.TicketID == "" || req.Phase == "" {
		writeError(w, http.StatusBadRequest, "ticket_id and phase are required")
		return
	}
	if s.PipelineEngine == nil {
		writeError(w, http.StatusServiceUnavailable, "pipeline engine not available")
		return
	}
	next, err := s.PipelineEngine.CompletePhase(ctx, agentID, req.TicketID, req.Phase, req.Result, req.LLMUsage)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "phase complete failed: "+err.Error())
		return
	}
	resp := map[string]interface{}{"agent_id": agentID, "ticket_id": req.TicketID, "completed_phase": req.Phase}
	if next != "" {
		resp["next_phase"] = string(next)
	} else {
		resp["status"] = "completed"
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) phaseFail(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	agentID := chi.URLParam(r, "id")
	var req struct {
		TicketID string `json:"ticket_id"`
		Phase    string `json:"phase"`
		Reason   string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.TicketID == "" || req.Phase == "" {
		writeError(w, http.StatusBadRequest, "ticket_id and phase are required")
		return
	}
	if s.PipelineEngine == nil {
		writeError(w, http.StatusServiceUnavailable, "pipeline engine not available")
		return
	}
	if err := s.PipelineEngine.FailPhase(ctx, agentID, req.TicketID, req.Phase, req.Reason); err != nil {
		writeError(w, http.StatusInternalServerError, "phase fail failed: "+err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"agent_id": agentID, "ticket_id": req.TicketID, "failed_phase": req.Phase, "reason": req.Reason})
}

// --- Group / team channel handlers ---

func (s *Server) listGroupMessages(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	msgs, err := s.DB.ListGroupMessages(ctx, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list messages: "+err.Error())
		return
	}
	if msgs == nil {
		msgs = []*repository.GroupMessage{}
	}
	writeJSON(w, http.StatusOK, msgs)
}

func (s *Server) addGroupMessage(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		AgentID     string `json:"agent_id"`
		Content     string `json:"content"`
		MessageType string `json:"message_type"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid request body")
		return
	}
	if req.Content == "" {
		writeError(w, http.StatusBadRequest, "content is required")
		return
	}
	if req.MessageType == "" {
		req.MessageType = "message"
	}
	if err := s.DB.AddGroupMessage(ctx, id, req.AgentID, req.MessageType, req.Content); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to add message: "+err.Error())
		return
	}
	s.Broadcaster.Broadcast("group_message", map[string]string{"group_id": id, "agent_id": req.AgentID})
	writeJSON(w, http.StatusCreated, map[string]string{"group_id": id, "status": "created"})
}

func (s *Server) serveSPA(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	if path == "/" {
		data, err := fs.ReadFile(s.staticFS, "index.html")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		w.Write(data)
		return
	}
	http.StripPrefix("/", http.FileServer(http.FS(s.staticFS))).ServeHTTP(w, r)
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

// WriteJSONForTest exports writeJSON for testing.
func WriteJSONForTest(w http.ResponseWriter, status int, v interface{}) { writeJSON(w, status, v) }

// WriteErrorForTest exports writeError for testing.
func WriteErrorForTest(w http.ResponseWriter, status int, msg string) { writeError(w, status, msg) }

// --- Ticket hierarchy + approval handlers ---

func (s *Server) decomposeTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	ticket, err := s.DB.GetTicket(ctx, id)
	if err != nil {
		writeError(w, http.StatusNotFound, "ticket not found")
		return
	}
	if ticket.Type != "idea" {
		writeError(w, http.StatusBadRequest, "only idea tickets can be decomposed")
		return
	}
	s.Broadcaster.Broadcast("decompose_requested", map[string]string{"ticket_id": id})
	writeJSON(w, http.StatusAccepted, map[string]string{"ticket_id": id, "status": "decompose_requested"})
}

func (s *Server) approveTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		Feedback string `json:"feedback"`
	}
	json.NewDecoder(r.Body).Decode(&req)
	if err := s.DB.ApproveTicket(ctx, id, req.Feedback); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to approve: "+err.Error())
		return
	}
	s.Broadcaster.Broadcast("ticket_approved", map[string]string{"ticket_id": id, "feedback": req.Feedback})
	writeJSON(w, http.StatusOK, map[string]string{"ticket_id": id, "status": "approved"})
}

func (s *Server) rejectTicket(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	var req struct {
		Feedback string `json:"feedback"`
	}
	json.NewDecoder(r.Body).Decode(&req)
	if err := s.DB.RejectTicket(ctx, id, req.Feedback); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to reject: "+err.Error())
		return
	}
	s.Broadcaster.Broadcast("ticket_rejected", map[string]string{"ticket_id": id, "feedback": req.Feedback})
	writeJSON(w, http.StatusOK, map[string]string{"ticket_id": id, "status": "rejected"})
}

func (s *Server) listChildren(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	id := chi.URLParam(r, "id")
	children, err := s.DB.ListChildren(ctx, id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list children: "+err.Error())
		return
	}
	if children == nil {
		children = []*models.Ticket{}
	}
	writeJSON(w, http.StatusOK, children)
}

func (s *Server) listPendingApprovals(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	tickets, err := s.DB.ListPendingApprovals(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to list pending approvals: "+err.Error())
		return
	}
	if tickets == nil {
		tickets = []*models.Ticket{}
	}
	writeJSON(w, http.StatusOK, tickets)
}