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

// Package provider defines the LLM provider interface and implementations.
package provider

import (
	"context"
)

// Provider is the interface every LLM provider (ollama, openai, anthropic) implements.
type Provider interface {
	Name() string
	IsAvailable(ctx context.Context) bool
	AnalyzeRepos(ctx context.Context, ticketID, ticketTitle, ticketDescription string, repos []RepoContext) (*AnalysisResult, error)
	Complete(ctx context.Context, messages []ChatMessage, model string) (*ChatResponse, error)
}

// ChatMessage is a single message in a chat conversation.
type ChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

// ChatResponse is the result of a chat completion.
type ChatResponse struct {
	Content      string
	Usage        Usage
	ModelUsed    string
	ProviderName string
}

// Usage reports token usage for a completion.
type Usage struct {
	PromptTokens     int
	CompletionTokens int
}

// RepoContext describes a repo for analysis.
type RepoContext struct {
	Name        string
	Description string
	Tags        []string
	Available   bool
}

// AnalysisResult is the structured output of AnalyzeRepos.
type AnalysisResult struct {
	SelectedRepos  []string `json:"selected_repos"`
	PrimaryRepo    string   `json:"primary_repo"`
	Complexity     string   `json:"complexity"`
	EstimatedHours float64  `json:"estimated_hours"`
	Reasoning      string   `json:"reasoning"`
	Branch         string   `json:"branch,omitempty"`
	Usage          Usage    `json:"-"`
}