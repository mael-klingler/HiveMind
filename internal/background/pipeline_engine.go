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
	"time"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/sse"
)

// PipelineEngine monitors pipeline_steps and drives phase transitions.
type PipelineEngine struct {
	Config      *config.Config
	DB          *database.DB
	Broadcaster *sse.Broadcaster
	stopCh      chan struct{}
}

func NewPipelineEngine(cfg *config.Config, db *database.DB, b *sse.Broadcaster) *PipelineEngine {
	return &PipelineEngine{
		Config:      cfg,
		DB:          db,
		Broadcaster: b,
		stopCh:      make(chan struct{}),
	}
}

func (pe *PipelineEngine) Run(ctx context.Context) error {
	slog.Info("pipeline engine started")
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			slog.Info("pipeline engine stopping")
			return nil
		case <-pe.stopCh:
			slog.Info("pipeline engine stopped")
			return nil
		case <-ticker.C:
			// The pipeline engine is currently a passive observer; phase
			// transitions are driven by agent calls to /phase_complete and
			// /phase_fail. Future work: auto-advance stale phases.
		}
	}
}

func (pe *PipelineEngine) Stop() {
	close(pe.stopCh)
}

// CompletePhase marks the current phase step as completed and advances to the
// next phase, updating the ticket's phase timestamp.
func (pe *PipelineEngine) CompletePhase(ctx context.Context, agentID string, ticketID, currentPhase, result, llmUsage string) (repository.Phase, error) {
	step, err := pe.findStep(ctx, ticketID, currentPhase)
	if err != nil {
		return "", fmt.Errorf("find step: %w", err)
	}
	if step == nil {
		return "", fmt.Errorf("no step for ticket %s phase %s", ticketID, currentPhase)
	}

	if err := pe.DB.UpdateTicketPhaseTimestamp(ctx, ticketID, currentPhase); err != nil {
		slog.Warn("failed to update phase timestamp", "ticket_id", ticketID, "phase", currentPhase, "error", err)
	}

	next := nextPhase(repository.Phase(currentPhase))
	if next == "" {
		if err := pe.DB.UpdateTicketStatus(ctx, ticketID, "completed"); err != nil {
			return "", fmt.Errorf("complete ticket: %w", err)
		}
		pe.Broadcaster.Broadcast("ticket_completed", map[string]string{"ticket_id": ticketID})
		return "", nil
	}

	nextStepID := fmt.Sprintf("step-%s-%s-%d", ticketID, next, time.Now().UnixNano())
	nextStep := &repository.PipelineStep{
		ID:       nextStepID,
		TicketID: ticketID,
		Phase:    next,
		Status:   "pending",
	}
	if err := pe.DB.CreatePipelineStep(ctx, nextStep); err != nil {
		return "", fmt.Errorf("create next step: %w", err)
	}
	pe.Broadcaster.Broadcast("phase_advanced", map[string]string{
		"ticket_id": ticketID, "from": currentPhase, "to": string(next),
	})
	return next, nil
}

// FailPhase marks the current phase as failed and either retries or fails the ticket.
func (pe *PipelineEngine) FailPhase(ctx context.Context, agentID, ticketID, currentPhase, reason string) error {
	ticket, err := pe.DB.GetTicket(ctx, ticketID)
	if err != nil {
		return fmt.Errorf("get ticket: %w", err)
	}
	if ticket.RetryCount >= pe.Config.AgentMaxRetries {
		if err := pe.DB.UpdateTicketStatus(ctx, ticketID, "failed"); err != nil {
			return fmt.Errorf("fail ticket: %w", err)
		}
		pe.Broadcaster.Broadcast("ticket_failed", map[string]string{"ticket_id": ticketID, "reason": reason})
		return nil
	}
	if err := pe.DB.RequeueTicket(ctx, ticketID, pe.Config.AgentMaxRetries); err != nil {
		return fmt.Errorf("requeue ticket: %w", err)
	}
	pe.Broadcaster.Broadcast("ticket_requeued", map[string]string{
		"ticket_id": ticketID, "reason": "phase " + currentPhase + " failed: " + reason,
	})
	return nil
}

func (pe *PipelineEngine) findStep(ctx context.Context, ticketID, phase string) (*repository.PipelineStep, error) {
	steps, err := pe.DB.ListPipelineSteps(ctx, ticketID)
	if err != nil {
		return nil, err
	}
	for _, s := range steps {
		if string(s.Phase) == phase && s.Status != "completed" && s.Status != "failed" {
			return s, nil
		}
	}
	return nil, nil
}

func nextPhase(p repository.Phase) repository.Phase {
	switch p {
	case repository.PhaseWork:
		return repository.PhaseTest
	case repository.PhaseTest:
		return repository.PhaseReview
	case repository.PhaseReview:
		return repository.PhaseShip
	case repository.PhaseShip:
		return repository.PhaseListen
	default:
		return ""
	}
}