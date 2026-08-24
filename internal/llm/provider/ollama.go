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
	"log/slog"
	"net/http"
	"regexp"
	"strings"
	"time"
)

// OllamaProvider implements Provider for Ollama / Ollama Cloud.
type OllamaProvider struct {
	BaseURL  string
	APIKey   string
	Model    string
	IsCloud  bool
	timeout  time.Duration
	client   *http.Client
}

// NewOllamaProvider creates a new Ollama provider.
func NewOllamaProvider(baseURL, apiKey, model string, isCloud bool) *OllamaProvider {
	if baseURL == "" {
		if isCloud {
			baseURL = "https://ollama.com/v1"
		} else {
			baseURL = "http://localhost:11434/v1"
		}
	}
	timeout := 120 * time.Second
	return &OllamaProvider{
		BaseURL: strings.TrimSuffix(baseURL, "/"),
		APIKey:  apiKey,
		Model:   model,
		IsCloud: isCloud,
		timeout: timeout,
		client:  &http.Client{Timeout: timeout},
	}
}

func (p *OllamaProvider) Name() string {
	if p.IsCloud {
		return "ollama_cloud"
	}
	return "ollama"
}

func (p *OllamaProvider) IsAvailable(ctx context.Context) bool {
	url := fmt.Sprintf("%s/models", p.BaseURL)
	req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
	for k, v := range p.buildHeaders() {
		req.Header.Set(k, v)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == 200
}

func (p *OllamaProvider) AnalyzeRepos(ctx context.Context, ticketID, ticketTitle, ticketDescription string, repos []RepoContext) (*AnalysisResult, error) {
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
		re := reJSONBlock
		if m := re.FindString(content); m != "" {
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

func (p *OllamaProvider) Complete(ctx context.Context, messages []ChatMessage, model string) (*ChatResponse, error) {
	if model == "" {
		model = p.Model
	}
	body := chatRequest{Model: model, Messages: messages, Stream: false}
	if !p.IsCloud {
		body.Format = "json"
	}

	data, _ := json.Marshal(body)
	url := fmt.Sprintf("%s/chat/completions", p.BaseURL)
	if p.IsCloud {
		url = fmt.Sprintf("%s/api/chat", strings.TrimSuffix(p.BaseURL, "/v1"))
		ollamaBody := map[string]interface{}{
			"model":   model,
			"messages": messages,
			"stream":  false,
		}
		if body.Format != "" {
			ollamaBody["format"] = body.Format
		}
		data, _ = json.Marshal(ollamaBody)
	}

	var lastErr error
	maxRetries := 3
	delays := []time.Duration{5 * time.Second, 15 * time.Second, 30 * time.Second}

	for attempt := 0; attempt < maxRetries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		for k, v := range p.buildHeaders() {
			req.Header.Set(k, v)
		}

		resp, err := p.client.Do(req)
		if err != nil {
			lastErr = err
			if attempt < maxRetries-1 {
				slog.Warn("LLM not reachable, retrying", "provider", p.Name(), "attempt", attempt+1, "delay", delays[attempt])
				time.Sleep(delays[attempt])
				continue
			}
			return nil, fmt.Errorf("LLM not reachable after %d attempts: %w", maxRetries, lastErr)
		}
		respBody, err := io.ReadAll(resp.Body)
		resp.Body.Close()
		if err != nil {
			return nil, fmt.Errorf("read response: %w", err)
		}

		if resp.StatusCode >= 400 {
			if (resp.StatusCode == 503 || resp.StatusCode == 429 || resp.StatusCode == 502) && attempt < maxRetries-1 {
				slog.Warn("LLM HTTP error, retrying", "provider", p.Name(), "status", resp.StatusCode, "attempt", attempt+1)
				time.Sleep(delays[attempt])
				lastErr = fmt.Errorf("LLM HTTP %d: %s", resp.StatusCode, string(respBody))
				continue
			}
			return nil, fmt.Errorf("LLM HTTP %d: %s", resp.StatusCode, string(respBody))
		}

		if p.IsCloud {
			var ollamaResp struct {
				Message struct {
					Content string `json:"content"`
				} `json:"message"`
				PromptEvalCount int `json:"prompt_eval_count"`
				EvalCount       int `json:"eval_count"`
			}
			if err := json.Unmarshal(respBody, &ollamaResp); err != nil {
				return nil, fmt.Errorf("unmarshal ollama response: %w", err)
			}
			return &ChatResponse{
				Content:      ollamaResp.Message.Content,
				Usage:        Usage{PromptTokens: ollamaResp.PromptEvalCount, CompletionTokens: ollamaResp.EvalCount},
				ModelUsed:    model,
				ProviderName: p.Name(),
			}, nil
		}

		var chatResp struct {
			Choices []struct {
				Message struct {
					Content string `json:"content"`
				} `json:"message"`
				Usage *struct {
					PromptTokens     int `json:"prompt_tokens"`
					CompletionTokens int `json:"completion_tokens"`
				} `json:"usage,omitempty"`
			} `json:"choices"`
			Usage *struct {
				PromptTokens     int `json:"prompt_tokens"`
				CompletionTokens int `json:"completion_tokens"`
			} `json:"usage,omitempty"`
		}
		if err := json.Unmarshal(respBody, &chatResp); err != nil {
			return nil, fmt.Errorf("unmarshal response: %w", err)
		}
		var content string
		if len(chatResp.Choices) > 0 {
			content = chatResp.Choices[0].Message.Content
		}
		var u Usage
		if chatResp.Usage != nil {
			u.PromptTokens = chatResp.Usage.PromptTokens
			u.CompletionTokens = chatResp.Usage.CompletionTokens
		} else if len(chatResp.Choices) > 0 && chatResp.Choices[0].Usage != nil {
			u.PromptTokens = chatResp.Choices[0].Usage.PromptTokens
			u.CompletionTokens = chatResp.Choices[0].Usage.CompletionTokens
		}
		return &ChatResponse{
			Content:      content,
			Usage:        u,
			ModelUsed:    model,
			ProviderName: p.Name(),
		}, nil
	}

	return nil, lastErr
}

func (p *OllamaProvider) buildHeaders() map[string]string {
	headers := map[string]string{"Content-Type": "application/json"}
	if p.APIKey != "" {
		headers["Authorization"] = fmt.Sprintf("Bearer %s", p.APIKey)
	}
	return headers
}

type chatRequest struct {
	Model    string        `json:"model"`
	Messages []ChatMessage `json:"messages"`
	Stream   bool          `json:"stream"`
	Format   string        `json:"format,omitempty"`
}

func buildAnalysisPrompt(ticketID, title, description string, repos []RepoContext) string {
	type analysisPrompt struct {
		Instruction  string        `json:"instruction"`
		Ticket       interface{}   `json:"ticket"`
		Repositories []RepoContext `json:"repositories"`
	}
	ticket := map[string]string{
		"id":          ticketID,
		"title":       title,
		"description": description,
	}
	data, _ := json.MarshalIndent(analysisPrompt{
		Instruction:  "Select 1-4 repositories for this ticket. Return JSON with: selected_repos[], primary_repo, complexity (Low/Medium/High), estimated_hours, reasoning.",
		Ticket:       ticket,
		Repositories: repos,
	}, "", "  ")
	return string(data)
}

var (
	reJSONBlock = regexp.MustCompile(`(?s)\{.*\}`)
	reCodeOpen  = regexp.MustCompile("(?s)^```(?:json)?\\s*\n?")
	reCodeClose = regexp.MustCompile("(?s)\n?\\s*```\\s*$")
)

func stripCodeFences(s string) string {
	s = reCodeOpen.ReplaceAllString(s, "")
	s = reCodeClose.ReplaceAllString(s, "")
	return strings.TrimSpace(s)
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}