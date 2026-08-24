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

package provider

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// OpenAIProvider implements Provider for OpenAI-compatible APIs.
type OpenAIProvider struct {
	BaseURL string
	APIKey  string
	Model   string
	timeout time.Duration
	client  *http.Client
}

// NewOpenAIProvider creates a new OpenAI provider.
func NewOpenAIProvider(baseURL, apiKey, model string) *OpenAIProvider {
	if baseURL == "" {
		baseURL = "https://api.openai.com/v1"
	}
	timeout := 120 * time.Second
	return &OpenAIProvider{
		BaseURL: strings.TrimSuffix(baseURL, "/"),
		APIKey:  apiKey,
		Model:   model,
		timeout: timeout,
		client:  &http.Client{Timeout: timeout},
	}
}

func (p *OpenAIProvider) Name() string { return "openai" }

func (p *OpenAIProvider) IsAvailable(ctx context.Context) bool {
	url := fmt.Sprintf("%s/models", p.BaseURL)
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	req.Header.Set("Authorization", "Bearer "+p.APIKey)
	resp, err := p.client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == 200
}

func (p *OpenAIProvider) AnalyzeRepos(ctx context.Context, ticketID, ticketTitle, ticketDescription string, repos []RepoContext) (*AnalysisResult, error) {
	prompt := buildAnalysisPrompt(ticketID, ticketTitle, ticketDescription, repos)
	messages := []ChatMessage{
		{Role: "system", Content: "You are a Senior Architect. Analyze the ticket and select the repositories most likely affected. Respond ONLY as JSON."},
		{Role: "user", Content: prompt},
	}
	resp, err := p.Complete(ctx, messages, p.Model)
	if err != nil {
		return nil, err
	}
	content := stripCodeFences(resp.Content)
	var result AnalysisResult
	if err := json.Unmarshal([]byte(content), &result); err != nil {
		if m := reJSONBlock.FindString(content); m != "" {
			if err2 := json.Unmarshal([]byte(m), &result); err2 != nil {
				return nil, fmt.Errorf("no JSON in LLM response: %s", truncate(content, 300))
			}
		} else {
			return nil, fmt.Errorf("no JSON in LLM response: %s", truncate(content, 300))
		}
	}
	result.Usage = resp.Usage
	return &result, nil
}

func (p *OpenAIProvider) Complete(ctx context.Context, messages []ChatMessage, model string) (*ChatResponse, error) {
	if model == "" {
		model = p.Model
	}
	body := chatRequest{Model: model, Messages: messages, Stream: false}
	data, _ := json.Marshal(body)
	url := fmt.Sprintf("%s/chat/completions", p.BaseURL)

	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+p.APIKey)

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("openai request: %w", err)
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("openai HTTP %d: %s", resp.StatusCode, string(respBody))
	}

	var chatResp struct {
		Choices []struct {
			Message struct {
				Content string `json:"content"`
			} `json:"message"`
		} `json:"choices"`
		Usage *struct {
			PromptTokens     int `json:"prompt_tokens"`
			CompletionTokens int `json:"completion_tokens"`
		} `json:"usage,omitempty"`
	}
	if err := json.Unmarshal(respBody, &chatResp); err != nil {
		return nil, fmt.Errorf("unmarshal openai response: %w", err)
	}
	var content string
	if len(chatResp.Choices) > 0 {
		content = chatResp.Choices[0].Message.Content
	}
	var u Usage
	if chatResp.Usage != nil {
		u.PromptTokens = chatResp.Usage.PromptTokens
		u.CompletionTokens = chatResp.Usage.CompletionTokens
	}
	return &ChatResponse{
		Content:      content,
		Usage:        u,
		ModelUsed:    model,
		ProviderName: p.Name(),
	}, nil
}