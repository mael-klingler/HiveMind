package background

import (
	"context"
	"log/slog"
	"time"

	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/mnesis"
)

type LearningWorker struct {
	Config *config.Config
	DB     *database.DB
	stopCh chan struct{}
}

func NewLearningWorker(cfg *config.Config, db *database.DB) *LearningWorker {
	return &LearningWorker{Config: cfg, DB: db, stopCh: make(chan struct{})}
}

func (lw *LearningWorker) Run(ctx context.Context) error {
	slog.Info("learning worker started")
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-lw.stopCh:
			return nil
		case <-ticker.C:
			if err := lw.recordOutcomes(ctx); err != nil {
				slog.Error("learning worker error", "error", err)
			}
		}
	}
}

func (lw *LearningWorker) Stop() { close(lw.stopCh) }

func (lw *LearningWorker) recordOutcomes(ctx context.Context) error {
	completed, err := lw.DB.ListTicketsPaged(ctx, "completed", 50, 0)
	if err != nil {
		return err
	}
	for _, t := range completed {
		if t.Type == "task" && t.ParentID != "" {
			parent, err := lw.DB.GetTicket(ctx, t.ParentID)
			if err != nil || parent == nil || parent.AIPlanning == "" {
				continue
			}
			metadata := map[string]interface{}{
				"topic":     "tech",
				"ticket_id":  t.ID,
				"parent_id":  t.ParentID,
				"agent_set":  []string{"engineer", "analytical", "red_team", "scientist", "strategist"},
			}
			if err := lw.DB.RecordProceduralPattern(ctx, string(mnesis.PatternSuccessful), "ticket completed: "+t.Title, parent.AIPlanning, metadata); err != nil {
				slog.Warn("failed to record successful pattern", "ticket_id", t.ID, "error", err)
			}
		}
	}
	failed, err := lw.DB.ListTicketsPaged(ctx, "failed", 50, 0)
	if err != nil {
		return err
	}
	for _, t := range failed {
		metadata := map[string]interface{}{
			"topic":       "tech",
			"ticket_id":    t.ID,
			"retry_count":  t.RetryCount,
		}
		if err := lw.DB.RecordProceduralPattern(ctx, string(mnesis.PatternFailed), "ticket failed: "+t.Title, t.Description, metadata); err != nil {
			slog.Warn("failed to record failed pattern", "ticket_id", t.ID, "error", err)
		}
	}
	return nil
}