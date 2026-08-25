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
	"github.com/maelklingler/hivemind/internal/llm/provider"
	"github.com/maelklingler/hivemind/internal/mnesis"
	"github.com/maelklingler/hivemind/internal/sse"
)

type Planner struct {
	Config      *config.Config
	DB          *database.DB
	LLM         provider.Provider
	Broadcaster *sse.Broadcaster
	stopCh      chan struct{}
}

func NewPlanner(cfg *config.Config, db *database.DB, llm provider.Provider, b *sse.Broadcaster) *Planner {
	return &Planner{Config: cfg, DB: db, LLM: llm, Broadcaster: b, stopCh: make(chan struct{})}
}

func (p *Planner) Run(ctx context.Context) error {
	slog.Info("planner started")
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-p.stopCh:
			return nil
		case <-ticker.C:
			if err := p.processIdeas(ctx); err != nil {
				slog.Error("planner error", "error", err)
			}
		}
	}
}

func (p *Planner) Stop() { close(p.stopCh) }

func (p *Planner) processIdeas(ctx context.Context) error {
	tickets, err := p.DB.ListTicketsPaged(ctx, "queued", 100, 0)
	if err != nil {
		return err
	}
	for _, t := range tickets {
		if t.Type == "" || t.Type == "task" {
			continue
		}
		if t.Type != "idea" {
			continue
		}
		if err := p.decompose(ctx, t); err != nil {
			slog.Error("failed to decompose idea", "ticket_id", t.ID, "error", err)
		}
	}
	return nil
}

func (p *Planner) decompose(ctx context.Context, ticket *database.TicketFull) error {
	if p.LLM == nil {
		slog.Warn("planner: LLM not available, skipping decomposition", "ticket_id", ticket.ID)
		return nil
	}
	dec := mnesis.CRODecide(ticket.Title + " " + ticket.Description)
	society := mnesis.NewAgentSociety(p.LLM, dec.Topic, dec.AgentSet)
	result, err := society.Run(ctx, ticket.Title+"\n\n"+ticket.Description, mnesis.DefaultRunOptions(), nil)
	if err != nil {
		return fmt.Errorf("agent society run: %w", err)
	}
	if len(result.Synthesis.NextSteps) == 0 {
		slog.Warn("planner: no next_steps in synthesis", "ticket_id", ticket.ID)
		return nil
	}
	planning, _ := json.Marshal(map[string]interface{}{
		"executive_summary":  result.Synthesis.ExecutiveSummary,
		"core_arguments":    result.Synthesis.CoreArguments,
		"next_steps":        result.Synthesis.NextSteps,
		"classification":    dec.Classification,
		"topic":             dec.Topic,
		"agent_set":         dec.AgentSet,
		"plenary_support":   result.Plenary.Support,
		"plenary_objections": result.Plenary.Objections,
	})
	if err := p.DB.SetTicketAIPlanning(ctx, ticket.ID, string(planning)); err != nil {
		slog.Warn("failed to set AI planning", "ticket_id", ticket.ID, "error", err)
	}
	for i, step := range result.Synthesis.NextSteps {
		childID := fmt.Sprintf("%s-%d", ticket.ID, i+1)
		title := step
		if len(title) > 200 {
			title = title[:200]
		}
		input := &database.TicketInput{
			ID:          childID,
			Title:       title,
			Description: step,
			IssueType:   "Task",
			Priority:    ticket.Priority,
		}
		if err := p.DB.CreateTicketAndEnqueueWithParent(ctx, input, ticket.ID); err != nil {
			slog.Error("failed to create child ticket", "parent", ticket.ID, "child", childID, "error", err)
			continue
		}
		slog.Info("planner created child ticket", "parent", ticket.ID, "child", childID, "title", title)
	}
	if err := p.DB.UpdateTicketStatus(ctx, ticket.ID, "planned"); err != nil {
		slog.Error("failed to mark idea as planned", "ticket_id", ticket.ID, "error", err)
	}
	p.Broadcaster.Broadcast("ticket_decomposed", map[string]interface{}{
		"ticket_id": ticket.ID, "children": len(result.Synthesis.NextSteps),
	})
	return nil
}

var _ = strings.Join