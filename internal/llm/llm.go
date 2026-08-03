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

package llm

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

	"github.com/maelklingler/hivemind/internal/config"
)

const (
	ProviderOllama      = "ollama"
	ProviderOllamaCloud = "ollama_cloud"
	ProviderOpenAI      = "openai"
	ProviderAnthropic   = "anthropic"
)

type LLMClient struct {
	Provider string
	BaseURL  string
	Model    string
	APIKey   string
	Timeout  time.Duration
	client   *http.Client
}

type ChatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type ChatRequest struct {
	Model    string        `json:"model"`
	Messages []ChatMessage `json:"messages"`
	Stream   bool          `json:"stream"`
	Format   string        `json:"format,omitempty"`
}

type ChatResponse struct {
	Message   ChatMessage  `json:"message,omitempty"`
	Choices   []Choice     `json:"choices,omitempty"`
	Usage     Usage        `json:"usage,omitempty"`
}

type Choice struct {
	Message ChatMessage `json:"message"`
	Usage   *Usage      `json:"usage,omitempty"`
}

type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
}

type AnalysisResult struct {
	SelectedRepos []string               `json:"selected_repos"`
	PrimaryRepo   string                 `json:"primary_repo"`
	Complexity    string                 `json:"complexity"`
	EstimatedHours float64               `json:"estimated_hours"`
	Reasoning     string                 `json:"reasoning"`
	Branch        string                 `json:"branch,omitempty"`
	LLMUsage      map[string]interface{} `json:"_llm_usage,omitempty"`
}

func NewLLMClient(cfg *config.Config) *LLMClient {
	provider := detectProvider(cfg)
	baseURL := cfg.OllamaBaseURL
	if baseURL == "" {
		baseURL = "http://localhost:11434/v1"
	}

	apiKey := ""
	switch provider {
	case ProviderOllamaCloud:
		apiKey = cfg.OllamaCloudAPIKey
	case ProviderOpenAI:
		apiKey = cfg.OpenAIAPIKey
	case ProviderAnthropic:
		apiKey = cfg.AnthropicAPIKey
	}

	model := cfg.OllamaModel
	if model == "" {
		model = cfg.OpencodeModel
	}
	if model == "" {
		model = "llama3.1:8b"
	}

	timeout := 120 * time.Second

	return &LLMClient{
		Provider: provider,
		BaseURL:  strings.TrimSuffix(baseURL, "/"),
		Model:    model,
		APIKey:   apiKey,
		Timeout:  timeout,
		client:   &http.Client{Timeout: timeout},
	}
}

func detectProvider(cfg *config.Config) string {
	if cfg.LLMProvider != "" {
		return cfg.LLMProvider
	}
	if cfg.OpenAIAPIKey != "" {
		return ProviderOpenAI
	}
	if cfg.AnthropicAPIKey != "" {
		return ProviderAnthropic
	}
	if cfg.OllamaCloudAPIKey != "" {
		return ProviderOllamaCloud
	}
	return ProviderOllama
}

func (c *LLMClient) IsAvailable() bool {
	url := fmt.Sprintf("%s/models", c.BaseURL)
	req, _ := http.NewRequest("GET", url, nil)
	for k, v := range c.buildHeaders() {
		req.Header.Set(k, v)
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	return resp.StatusCode == 200
}

func (c *LLMClient) AnalyzeReposForTicket(ctx context.Context, ticketID, ticketTitle, ticketDescription string, repos []RepoContext) (*AnalysisResult, error) {
	prompt := buildAnalysisPrompt(ticketID, ticketTitle, ticketDescription, repos)
	messages := []ChatMessage{
		{Role: "system", Content: "You are a Senior Architect. Analyze the ticket and select the repositories most likely affected. Respond ONLY as JSON."},
		{Role: "user", Content: prompt},
	}

	body := ChatRequest{
		Model:    c.Model,
		Messages: messages,
		Stream:   false,
	}
	if c.Provider == ProviderOllama {
		body.Format = "json"
	}

	resp, err := c.post(ctx, body)
	if err != nil {
		return nil, fmt.Errorf("LLM request failed: %w", err)
	}

	content := extractContent(resp)
	content = stripCodeFences(content)

	var result AnalysisResult
	if err := json.Unmarshal([]byte(content), &result); err != nil {
		re := reJSONBlock
		if m := re.FindString(content); m != "" {
			if err2 := json.Unmarshal([]byte(m), &result); err2 != nil {
				return nil, fmt.Errorf("no JSON in LLM response: %s", content[:300])
			}
		} else {
			return nil, fmt.Errorf("no JSON in LLM response: %s", content[:300])
		}
	}

	if resp.Usage.PromptTokens > 0 || resp.Usage.CompletionTokens > 0 {
		result.LLMUsage = map[string]interface{}{
			"prompt_tokens":     resp.Usage.PromptTokens,
			"completion_tokens":  resp.Usage.CompletionTokens,
			"model":             c.Model,
			"provider":          c.Provider,
		}
	}

	return &result, nil
}

func (c *LLMClient) post(ctx context.Context, body ChatRequest) (*ChatResponse, error) {
	data, _ := json.Marshal(body)

	url := fmt.Sprintf("%s/chat/completions", c.BaseURL)
	if c.Provider == ProviderOllamaCloud {
		url = fmt.Sprintf("%s/api/chat", strings.TrimSuffix(c.BaseURL, "/v1"))
		ollamaBody := map[string]interface{}{
			"model":   body.Model,
			"messages": body.Messages,
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
		for k, v := range c.buildHeaders() {
			req.Header.Set(k, v)
		}

		resp, err := c.client.Do(req)
		if err != nil {
			lastErr = err
			if attempt < maxRetries-1 {
				slog.Warn("LLM not reachable, retrying", "attempt", attempt+1, "delay", delays[attempt])
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
				slog.Warn("LLM HTTP error, retrying", "status", resp.StatusCode, "attempt", attempt+1)
				time.Sleep(delays[attempt])
				lastErr = fmt.Errorf("LLM HTTP %d: %s", resp.StatusCode, string(respBody))
				continue
			}
			return nil, fmt.Errorf("LLM HTTP %d: %s", resp.StatusCode, string(respBody))
		}

		var chatResp ChatResponse
		if c.Provider == ProviderOllamaCloud {
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
			chatResp = ChatResponse{
				Choices: []Choice{
					{Message: ChatMessage{Role: "assistant", Content: ollamaResp.Message.Content}},
				},
				Usage: Usage{
					PromptTokens:     ollamaResp.PromptEvalCount,
					CompletionTokens: ollamaResp.EvalCount,
				},
			}
		} else {
			if err := json.Unmarshal(respBody, &chatResp); err != nil {
				return nil, fmt.Errorf("unmarshal response: %w", err)
			}
		}

		return &chatResp, nil
	}

	return nil, lastErr
}

func (c *LLMClient) buildHeaders() map[string]string {
	headers := map[string]string{"Content-Type": "application/json"}
	if c.APIKey != "" {
		if c.Provider == ProviderAnthropic {
			headers["x-api-key"] = c.APIKey
			headers["anthropic-version"] = "2023-06-01"
		} else {
			headers["Authorization"] = fmt.Sprintf("Bearer %s", c.APIKey)
		}
	}
	return headers
}

type RepoContext struct {
	Name        string
	Description string
	Tags        []string
	Available   bool
}

func buildAnalysisPrompt(ticketID, title, description string, repos []RepoContext) string {
	type analysisPrompt struct {
		Instruction string      `json:"instruction"`
		Ticket      interface{} `json:"ticket"`
		Repositories []RepoContext `json:"repositories"`
	}
	ticket := map[string]string{
		"id":          ticketID,
		"title":       title,
		"description": description,
	}
	return toJSON(analysisPrompt{
		Instruction:  "Select 1-4 repositories for this ticket. Return JSON with: selected_repos[], primary_repo, complexity (Low/Medium/High), estimated_hours, reasoning.",
		Ticket:       ticket,
		Repositories: repos,
	})
}

func extractContent(resp *ChatResponse) string {
	if resp.Message.Content != "" {
		return resp.Message.Content
	}
	if len(resp.Choices) > 0 {
		return resp.Choices[0].Message.Content
	}
	return ""
}

var (
	reJSONBlock   = regexp.MustCompile(`(?s)\{.*\}`)
	reCodeOpen    = regexp.MustCompile("(?s)^```(?:json)?\\s*\n?")
	reCodeClose   = regexp.MustCompile("(?s)\n?\\s*```\\s*$")
)

func stripCodeFences(s string) string {
	s = reCodeOpen.ReplaceAllString(s, "")
	s = reCodeClose.ReplaceAllString(s, "")
	return strings.TrimSpace(s)
}

func toJSON(v interface{}) string {
	data, _ := json.MarshalIndent(v, "", "  ")
	return string(data)
}