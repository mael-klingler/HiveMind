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

package repository

import (
	"context"

	"github.com/maelklingler/hivemind/internal/models"
)

// SettingsRepository defines settings persistence operations.
type SettingsRepository interface {
	GetSetting(ctx context.Context, key string) (string, error)
	SetSetting(ctx context.Context, key, value string) error
	GetAllSettings(ctx context.Context) (map[string]string, error)
}

// MetricEventInput is the input for recording a metric event.
type MetricEventInput struct {
	EventType       string
	TicketID        string
	AgentID         string
	Phase           string
	DurationSeconds float64
	Labels          string
	Value           float64
}

// MetricsSummary is the aggregate metrics result.
type MetricsSummary struct {
	TotalTickets          int     `json:"total_tickets"`
	CompletedTickets      int     `json:"completed_tickets"`
	FailedTickets         int     `json:"failed_tickets"`
	MergedTickets         int     `json:"merged_tickets"`
	TotalRetries          int     `json:"total_retries"`
	AvgReviewCycles       float64 `json:"avg_review_cycles"`
	TotalPromptTokens     int64   `json:"total_prompt_tokens"`
	TotalCompletionTokens int64   `json:"total_completion_tokens"`
	TotalLLMCostUSD       float64 `json:"total_llm_cost_usd"`
}

// MetricRepository defines metric event + summary operations.
type MetricRepository interface {
	RecordMetricEvent(ctx context.Context, in *MetricEventInput) error
	GetMetricsSummary(ctx context.Context) (*MetricsSummary, error)
}

// Step represents a pipeline step definition (static metadata).
type Step struct {
	ID    string `json:"id"`
	Name  string `json:"name"`
	Order int    `json:"order"`
}

// StepRepository defines static step operations (may be DB-backed or hardcoded).
type StepRepository interface {
	ListSteps(ctx context.Context) ([]*Step, error)
}

var _ = models.MetricEvent{}