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
	"time"

	corev1 "k8s.io/api/core/v1"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/k8s"
)

type AgentMonitor struct {
	Config *config.Config
	DB     *database.DB
	K8s    *k8s.Client
	stopCh chan struct{}
}

func NewAgentMonitor(cfg *config.Config, db *database.DB, k8sClient *k8s.Client) *AgentMonitor {
	return &AgentMonitor{
		Config: cfg,
		DB:     db,
		K8s:    k8sClient,
		stopCh: make(chan struct{}),
	}
}

func (am *AgentMonitor) Run(ctx context.Context) error {
	slog.Info("agent monitor started")
	ticker := time.NewTicker(time.Duration(am.Config.AgentPollInterval) * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			slog.Info("agent monitor stopping")
			return nil
		case <-am.stopCh:
			slog.Info("agent monitor stopped")
			return nil
		case <-ticker.C:
			if err := am.checkAgentPods(ctx); err != nil {
				slog.Error("agent monitor error", "error", err)
			}
		}
	}
}

func (am *AgentMonitor) Stop() {
	close(am.stopCh)
}

func (am *AgentMonitor) checkAgentPods(ctx context.Context) error {
	pods, err := am.K8s.ListPods(ctx, "app.kubernetes.io/component=agent")
	if err != nil {
		return err
	}

	podTickets := make(map[string]bool)
	for _, pod := range pods {
		ticketID := pod.Labels["ticket-id"]
		if ticketID != "" {
			podTickets[ticketID] = true
		}
	}

	for _, pod := range pods {
		ticketID := pod.Labels["ticket-id"]
		if ticketID == "" {
			continue
		}
		phase := string(pod.Status.Phase)

		switch phase {
		case string(corev1.PodSucceeded):
			slog.Info("agent pod completed", "pod", pod.Name, "ticket_id", ticketID)
			if err := am.DB.UpdateTicketStatus(ctx, ticketID, "completed"); err != nil {
				slog.Error("failed to update ticket status to completed", "ticket_id", ticketID, "error", err)
			}
			if err := am.K8s.DeletePod(ctx, pod.Name); err != nil {
				slog.Error("failed to delete completed pod", "pod", pod.Name, "error", err)
			}
			am.K8s.CleanupAgentResources(ctx, ticketID)

		case string(corev1.PodFailed):
			slog.Warn("agent pod failed", "pod", pod.Name, "ticket_id", ticketID)
			ticket, err := am.DB.GetTicket(ctx, ticketID)
			if err == nil && ticket.RetryCount < am.Config.AgentMaxRetries {
				if err := am.DB.RequeueTicket(ctx, ticketID, am.Config.AgentMaxRetries); err != nil {
					slog.Error("failed to requeue ticket", "ticket_id", ticketID, "error", err)
				}
				slog.Info("re-queuing ticket after pod failure", "ticket_id", ticketID, "retry", ticket.RetryCount+1)
			} else {
				if err := am.DB.UpdateTicketStatus(ctx, ticketID, "failed"); err != nil {
					slog.Error("failed to update ticket status to failed", "ticket_id", ticketID, "error", err)
				}
			}

		case string(corev1.PodRunning):
			if am.isStale(pod, am.Config.AgentStaleTimeout) {
				slog.Warn("stale agent pod detected", "pod", pod.Name, "ticket_id", ticketID)
				if err := am.DB.UpdateTicketStatus(ctx, ticketID, "completed"); err != nil {
					slog.Error("failed to update stale ticket status", "ticket_id", ticketID, "error", err)
				}
				if err := am.K8s.DeletePod(ctx, pod.Name); err != nil {
					slog.Error("failed to delete stale pod", "pod", pod.Name, "error", err)
				}
				am.K8s.CleanupAgentResources(ctx, ticketID)
			}
		}
	}

	agents, err := am.DB.ListAgents(ctx)
	if err != nil {
		slog.Error("failed to list agents for orphan check", "error", err)
	} else {
		for _, agent := range agents {
			if agent.Status == "running" && agent.CurrentTask != "" {
				if !podTickets[agent.CurrentTask] {
					slog.Warn("orphan agent detected, resetting to idle", "agent_id", agent.ID, "ticket_id", agent.CurrentTask)
					if err := am.DB.SetAgentIdle(ctx, agent.ID); err != nil {
						slog.Error("failed to reset orphan agent", "agent_id", agent.ID, "error", err)
					}
				}
			}
		}
	}

	return nil
}

func (am *AgentMonitor) isStale(pod corev1.Pod, staleTimeoutSeconds int) bool {
	if pod.Status.StartTime == nil {
		return false
	}
	elapsed := time.Since(pod.Status.StartTime.Time)
	return elapsed > time.Duration(staleTimeoutSeconds)*time.Second
}