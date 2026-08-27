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

// AnthropicProvider implements Provider for Anthropic Claude APIs.
type AnthropicProvider struct {
	BaseURL string
	APIKey  string
	Model   string
	timeout time.Duration
	client  *http.Client
}

// NewAnthropicProvider creates a new Anthropic provider.
func NewAnthropicProvider(baseURL, apiKey, model string) *AnthropicProvider {
	if baseURL == "" {
		baseURL = "https://api.anthropic.com"
	}
	timeout := 120 * time.Second
	return &AnthropicProvider{
		BaseURL: strings.TrimSuffix(baseURL, "/"),
		APIKey:  apiKey,
		Model:   model,
		timeout: timeout,
		client:  &http.Client{Timeout: timeout},
	}
}

func (p *AnthropicProvider) Name() string { return "anthropic" }

func (p *AnthropicProvider) IsAvailable(ctx context.Context) bool {
	url := fmt.Sprintf("%s/v1/models", p.BaseURL)
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	req.Header.Set("x-api-key", p.APIKey)
	req.Header.Set("anthropic-version", "2023-06-01")
	resp, err := p.client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == 200
}

func (p *AnthropicProvider) AnalyzeRepos(ctx context.Context, ticketID, ticketTitle, ticketDescription string, repos []RepoContext) (*AnalysisResult, error) {
	prompt := buildAnalysisPrompt(ticketID, ticketTitle, ticketDescription, repos)
	messages := []ChatMessage{
		{Role: "user", Content: "You are a Senior Architect. Analyze the ticket and select the repositories most likely affected. Respond ONLY as JSON.\n\n" + prompt},
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

func (p *AnthropicProvider) Complete(ctx context.Context, messages []ChatMessage, model string) (*ChatResponse, error) {
	if model == "" {
		model = p.Model
	}

	var systemContent string
	var userMessages []map[string]string
	for _, m := range messages {
		if m.Role == "system" {
			systemContent = m.Content
			continue
		}
		userMessages = append(userMessages, map[string]string{"role": m.Role, "content": m.Content})
	}

	body := map[string]interface{}{
		"model":      model,
		"max_tokens": 4096,
		"messages":   userMessages,
	}
	if systemContent != "" {
		body["system"] = systemContent
	}
	data, _ := json.Marshal(body)
	url := fmt.Sprintf("%s/v1/messages", p.BaseURL)

	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("x-api-key", p.APIKey)
	req.Header.Set("anthropic-version", "2023-06-01")

	resp, err := p.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("anthropic request: %w", err)
	}
	defer resp.Body.Close()
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read response: %w", err)
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("anthropic HTTP %d: %s", resp.StatusCode, string(respBody))
	}

	var anthropicResp struct {
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
		Usage struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(respBody, &anthropicResp); err != nil {
		return nil, fmt.Errorf("unmarshal anthropic response: %w", err)
	}
	var content string
	for _, block := range anthropicResp.Content {
		if block.Type == "text" {
			content += block.Text
		}
	}
	return &ChatResponse{
		Content:      content,
		Usage:        Usage{PromptTokens: anthropicResp.Usage.InputTokens, CompletionTokens: anthropicResp.Usage.OutputTokens},
		ModelUsed:    model,
		ProviderName: p.Name(),
	}, nil
}