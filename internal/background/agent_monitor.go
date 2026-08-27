package background

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/tools/cache"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/k8s"
)

type AgentMonitor struct {
	Config      *config.Config
	DB          *database.DB
	K8s         *k8s.Client
	Leader      *LeaderElector
	processedMu sync.Map
}

func NewAgentMonitor(cfg *config.Config, db *database.DB, k8sClient *k8s.Client, leader *LeaderElector) *AgentMonitor {
	return &AgentMonitor{Config: cfg, DB: db, K8s: k8sClient, Leader: leader}
}

func (am *AgentMonitor) Stop() {}

func (am *AgentMonitor) Run(ctx context.Context) error {
	if am.K8s == nil || am.K8s.ClientSet == nil {
		slog.Warn("agent monitor: k8s client not available, falling back to polling")
		return am.runPolling(ctx)
	}
	slog.Info("agent monitor started (informer mode)")
	factory := informers.NewFilteredSharedInformerFactory(
		am.K8s.ClientSet,
		30*time.Second,
		am.K8s.Namespace,
		func(lo *metav1.ListOptions) {
			lo.LabelSelector = "app.kubernetes.io/component=agent"
		},
	)
	informer := factory.Core().V1().Pods().Informer()
	informer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		UpdateFunc: func(oldObj, newObj interface{}) {
			oldPod := oldObj.(*corev1.Pod)
			newPod := newObj.(*corev1.Pod)
			if oldPod.Status.Phase != newPod.Status.Phase {
				am.handlePodEvent(ctx, newPod)
			}
		},
		DeleteFunc: func(obj interface{}) {
			pod := obj.(*corev1.Pod)
			am.handlePodDelete(ctx, pod)
		},
	})
	factory.Start(ctx.Done())
	if !cache.WaitForCacheSync(ctx.Done(), informer.HasSynced) {
		return fmt.Errorf("informer cache sync failed")
	}
	slog.Info("agent monitor informer synced")
	<-ctx.Done()
	slog.Info("agent monitor stopping")
	return nil
}

func (am *AgentMonitor) runPolling(ctx context.Context) error {
	ticker := time.NewTicker(time.Duration(am.Config.AgentPollInterval) * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if am.Leader != nil {
				acq, err := am.Leader.TryAcquire(ctx)
				if err != nil || !acq {
					continue
				}
				defer am.Leader.Renew(ctx)
			}
			if err := am.checkAgentPods(ctx); err != nil {
				slog.Error("agent monitor error", "error", err)
			}
		}
	}
}

func (am *AgentMonitor) handlePodEvent(ctx context.Context, pod *corev1.Pod) {
	if am.Leader != nil {
		acq, err := am.Leader.TryAcquire(ctx)
		if err != nil || !acq {
			return
		}
		defer am.Leader.Renew(ctx)
	}
	ticketID := pod.Labels["ticket-id"]
	if ticketID == "" {
		return
	}
	phase := string(pod.Status.Phase)
	if am.isAlreadyProcessed(string(pod.UID), phase) {
		return
	}
	switch phase {
	case string(corev1.PodSucceeded):
		slog.Info("agent pod completed", "pod", pod.Name, "ticket_id", ticketID)
		am.extractAndSetBranchAndMR(ctx, pod, ticketID)
		if err := am.DB.UpdateTicketStatus(ctx, ticketID, "completed"); err != nil {
			slog.Error("failed to update ticket status to completed", "ticket_id", ticketID, "error", err)
		}
		if err := am.K8s.DeletePod(ctx, pod.Name); err != nil {
			slog.Error("failed to delete completed pod", "pod", pod.Name, "error", err)
		}
		am.K8s.CleanupAgentResources(ctx, ticketID)
		am.checkParentCompletion(ctx, ticketID)
	case string(corev1.PodFailed):
		slog.Warn("agent pod failed", "pod", pod.Name, "ticket_id", ticketID)
		am.extractAndSetBranchAndMR(ctx, pod, ticketID)
		ticket, err := am.DB.GetTicket(ctx, ticketID)
		if err == nil && ticket.RetryCount < am.Config.AgentMaxRetries {
			if err := am.DB.RequeueTicket(ctx, ticketID, am.Config.AgentMaxRetries); err != nil {
				slog.Error("failed to requeue ticket", "ticket_id", ticketID, "error", err)
			}
		} else {
			if err := am.DB.UpdateTicketStatus(ctx, ticketID, "failed"); err != nil {
				slog.Error("failed to update ticket status to failed", "ticket_id", ticketID, "error", err)
			}
		}
	case string(corev1.PodRunning):
		if am.isStale(*pod, am.Config.AgentStaleTimeout) {
			slog.Warn("stale agent pod detected", "pod", pod.Name, "ticket_id", ticketID)
			am.extractAndSetBranchAndMR(ctx, pod, ticketID)
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

func (am *AgentMonitor) extractAndSetBranchAndMR(ctx context.Context, pod *corev1.Pod, ticketID string) {
	logs, err := am.K8s.GetPodLogs(ctx, pod.Name, 500)
	if err != nil {
		slog.Warn("failed to get pod logs for MR extraction", "pod", pod.Name, "error", err)
		return
	}

	var branch, mrURL, mrProjectPath string

	for _, line := range strings.Split(logs, "\n") {
		if strings.HasPrefix(line, "🌿 Branch:") || strings.Contains(line, "Branch:") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 {
				branch = strings.TrimSpace(parts[1])
			}
		}
		if strings.Contains(line, "https://") && strings.Contains(line, "/merge_requests/") {
			start := strings.Index(line, "https://")
			if start == -1 {
				continue
			}
			url := line[start:]
			url = strings.TrimSpace(strings.Split(url, "'")[0])
			url = strings.TrimSpace(strings.Split(url, "\"")[0])
			url = strings.TrimSpace(strings.Split(url, " ")[0])
			mrURL = url
			if idx := strings.Index(mrURL, "/-/merge_requests/"); idx > 0 {
				mrProjectPath = mrURL[:idx]
				if strings.HasPrefix(mrProjectPath, "https://") {
					mrProjectPath = strings.TrimPrefix(mrProjectPath, "https://")
				} else if strings.HasPrefix(mrProjectPath, "http://") {
					mrProjectPath = strings.TrimPrefix(mrProjectPath, "http://")
				}
			}
		}
	}

	if branch == "" && mrURL == "" {
		return
	}

	slog.Info("extracted branch/MR from pod logs", "ticket_id", ticketID, "branch", branch, "mr_url", mrURL, "mr_project_path", mrProjectPath)
	if err := am.DB.SetTicketBranchAndMR(ctx, ticketID, branch, mrURL, mrProjectPath); err != nil {
		slog.Error("failed to set ticket branch and MR", "ticket_id", ticketID, "error", err)
	}
}

func (am *AgentMonitor) handlePodDelete(ctx context.Context, pod *corev1.Pod) {
	ticketID := pod.Labels["ticket-id"]
	if ticketID == "" {
		return
	}
	agents, err := am.DB.ListAgents(ctx)
	if err != nil {
		return
	}
	for _, agent := range agents {
		if agent.Status == "running" && agent.CurrentTask == ticketID {
			slog.Warn("orphan agent detected after pod delete, resetting to idle", "agent_id", agent.ID, "ticket_id", ticketID)
			if err := am.DB.SetAgentIdle(ctx, agent.ID); err != nil {
				slog.Error("failed to reset orphan agent", "agent_id", agent.ID, "error", err)
			}
		}
	}
}

func (am *AgentMonitor) isAlreadyProcessed(uid string, phase string) bool {
	key := fmt.Sprintf("%s/%s", uid, phase)
	if _, ok := am.processedMu.Load(key); ok {
		return true
	}
	am.processedMu.Store(key, true)
	return false
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
		am.handlePodEvent(ctx, &pod)
	}
	agents, err := am.DB.ListAgents(ctx)
	if err != nil {
		return err
	}
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
	return nil
}

func (am *AgentMonitor) isStale(pod corev1.Pod, staleTimeoutSeconds int) bool {
	if pod.Status.StartTime == nil {
		return false
	}
	elapsed := time.Since(pod.Status.StartTime.Time)
	return elapsed > time.Duration(staleTimeoutSeconds)*time.Second
}

func (am *AgentMonitor) checkParentCompletion(ctx context.Context, childID string) {
	child, err := am.DB.GetTicket(ctx, childID)
	if err != nil || child == nil || child.ParentID == "" {
		return
	}
	allDone, err := am.DB.AreAllChildrenCompleted(ctx, child.ParentID)
	if err != nil {
		slog.Error("failed to check parent children", "parent", child.ParentID, "error", err)
		return
	}
	if !allDone {
		return
	}
	parent, err := am.DB.GetTicket(ctx, child.ParentID)
	if err != nil || parent == nil {
		return
	}
	if parent.Status == "planned" {
		slog.Info("all children completed, promoting parent to approval", "parent", child.ParentID)
		if err := am.DB.SetApprovalRequired(ctx, child.ParentID, true); err != nil {
			slog.Error("failed to set approval required", "ticket_id", child.ParentID, "error", err)
		}
		if err := am.DB.UpdateTicketStatus(ctx, child.ParentID, "approval"); err != nil {
			slog.Error("failed to set parent to approval", "ticket_id", child.ParentID, "error", err)
		}
	}
}