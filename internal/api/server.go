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
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	chiMiddleware "github.com/go-chi/chi/v5/middleware"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/background"
	"github.com/maelklingler/hivemind/internal/k8s"
	"github.com/maelklingler/hivemind/internal/middleware"
	"github.com/maelklingler/hivemind/internal/models"
	"github.com/maelklingler/hivemind/internal/sse"
)

type Server struct {
	Config         *config.Config
	DB             *database.DB
	K8s            *k8s.Client
	Router         *chi.Mux
	Broadcaster    *sse.Broadcaster
	Dedup          repository.DedupRepository
	RateLimiter    repository.RateLimitRepository
	PipelineEngine *background.PipelineEngine
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

func NewServer(cfg *config.Config, db *database.DB, k8sClient *k8s.Client, broadcaster *sse.Broadcaster, dedup repository.DedupRepository, rateLimiter repository.RateLimitRepository, pipelineEngine *background.PipelineEngine, staticFS fs.FS) *Server {
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
		r.Get("/repos/{name}", s.getRepo)
		r.Put("/repos/{name}", s.updateRepo)
		r.Patch("/repos/{name}", s.patchRepo)
		r.Delete("/repos/{name}", s.deleteRepo)
		r.Get("/repos/{name}/branches", s.listRepoBranches)

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

		// Stream
		r.Get("/stream", s.streamEvents)

		// Pipeline
		r.Get("/phases", s.listPhases)
		r.Get("/roles", s.listRoles)
		r.Get("/roles/{role}/instruction", s.getRoleInstruction)
		r.Get("/tickets/{id}/pipeline", s.getTicketPipeline)
		r.Post("/agents/{id}/phase_complete", s.phaseComplete)
		r.Post("/agents/{id}/phase_fail", s.phaseFail)

		// Groups (team channel)
		r.Get("/groups/{id}/messages", s.listGroupMessages)
		r.Post("/groups/{id}/messages", s.addGroupMessage)
	})

	// VCS Webhooks
	r.Post("/webhooks/gitlab", s.gitlabWebhook)
	r.Post("/webhooks/github", s.githubWebhook)

	// Agent session proxy
	r.Handle("/agent-session/{ticketID}/*", http.HandlerFunc(s.agentSessionProxy))

	// Static assets (CSS, JS)
	r.Handle("/static/*", http.StripPrefix("/static/", http.FileServer(http.FS(s.staticFS))))

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
		TicketID string `json:"ticket_id"`
		Status   string `json:"status"`
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
		if err := s.DB.UpdateTicketStatus(ctx, req.TicketID, "completed"); err != nil {
			slog.Warn("failed to complete ticket", "ticket_id", req.TicketID, "error", err)
		} else {
			s.Broadcaster.Broadcast("ticket_completed", map[string]string{"ticket_id": req.TicketID})
		}
	}
	if err := s.DB.SetAgentStatus(ctx, id, newStatus); err != nil {
		writeError(w, http.StatusInternalServerError, "failed to set agent status: "+err.Error())
		return
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
	writeJSON(w, http.StatusOK, []interface{}{})
}

func (s *Server) createAgentInstruction(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusCreated, map[string]string{"status": "created"})
}

func (s *Server) updateAgentInstruction(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
}

func (s *Server) deleteAgentInstruction(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
}

func (s *Server) listAgentProfiles(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, []interface{}{})
}

func (s *Server) createAgentProfile(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusCreated, map[string]string{"status": "created"})
}

func (s *Server) getAgentMemory(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, []interface{}{})
}

func (s *Server) updateAgentMemory(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
}

func (s *Server) deleteAgentMemory(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
}

func (s *Server) getQueue(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	queue, err := s.DB.GetQueue(ctx)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "failed to get queue: "+err.Error())
		return
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
	// TODO: Implement bulk update
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
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
	// TODO: Implement
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) patchRepo(w http.ResponseWriter, r *http.Request) {
	// TODO: Implement
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
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
	// TODO: Implement via VCS provider
	writeJSON(w, http.StatusOK, []interface{}{})
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
	writeJSON(w, http.StatusOK, servers)
}

func (s *Server) createMCPServer(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusCreated, map[string]string{"status": "created"})
}

func (s *Server) updateMCPServer(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
}

func (s *Server) deleteMCPServer(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
}

func (s *Server) listPlugins(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, []interface{}{})
}

func (s *Server) createPlugin(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusCreated, map[string]string{"status": "created"})
}

func (s *Server) updatePlugin(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "updated"})
}

func (s *Server) deletePlugin(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
}

func (s *Server) streamEvents(w http.ResponseWriter, r *http.Request) {
	ch := s.Broadcaster.AddClient()
	defer s.Broadcaster.RemoveClient(ch)

	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming not supported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(http.StatusOK)

	for {
		select {
		case event, ok := <-ch:
			if !ok {
				return
			}
			data, _ := json.Marshal(event.Data)
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event.Type, string(data))
			flusher.Flush()
		case <-r.Context().Done():
			return
		}
	}
}

func (s *Server) agentSessionProxy(w http.ResponseWriter, r *http.Request) {
	// TODO: Implement reverse proxy to agent pods
	writeError(w, http.StatusNotImplemented, "agent session proxy not yet implemented")
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