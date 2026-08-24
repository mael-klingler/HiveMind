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

// TicketInput is the input for ticket creation (API layer).
type TicketInput struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	Description string   `json:"description"`
	Labels      []string `json:"labels"`
	IssueType   string   `json:"issue_type"`
	Priority    string   `json:"priority"`
}

// TicketRepository defines the ticket persistence operations.
type TicketRepository interface {
	CreateTicket(ctx context.Context, t *TicketInput) error
	CreateTicketAndEnqueue(ctx context.Context, t *TicketInput) error
	GetTicket(ctx context.Context, id string) (*models.Ticket, error)
	ListTickets(ctx context.Context, status string, limit, offset int) ([]*models.Ticket, error)
	UpdateTicketStatus(ctx context.Context, id, status string) error
	UpdateTicket(ctx context.Context, t *models.Ticket) error
	DeleteTicket(ctx context.Context, id string) error
	SetTicketAIPlanning(ctx context.Context, id, planning string) error
	SetTicketMRURL(ctx context.Context, id, mrURL string) error
	SetTicketReviewStatus(ctx context.Context, id, status, notes string) error
	SetTicketCompletedAt(ctx context.Context, id string) error
	SetTicketMRLastNoteID(ctx context.Context, id string, noteID int) error
	SetTicketLLMUsage(ctx context.Context, id string, promptTokens, completionTokens int, totalCostUSD float64) error
	SetTicketLineStats(ctx context.Context, id string, added, removed, filesChanged int) error
	UpdateTicketPhaseTimestamp(ctx context.Context, id, phase string) error
	IncrementReviewCycleCount(ctx context.Context, id string) error
	RequeueTicket(ctx context.Context, id string, maxRetries int) error
	ListOpenMRTickets(ctx context.Context) ([]*models.Ticket, error)
}

// CommentRepository defines ticket comment operations.
type CommentRepository interface {
	AddTicketComment(ctx context.Context, ticketID, author, commentType, content string) error
	ListTicketComments(ctx context.Context, ticketID string) ([]*models.TicketComment, error)
}