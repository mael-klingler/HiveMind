package api

import (
	"context"

	"github.com/maelklingler/hivemind/internal/database/repository"
)

type PipelineEngineInterface interface {
	CompletePhase(ctx context.Context, agentID, ticketID, currentPhase, result, llmUsage string) (repository.Phase, error)
	FailPhase(ctx context.Context, agentID, ticketID, currentPhase, reason string) error
}