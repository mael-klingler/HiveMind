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

package provider_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/maelklingler/hivemind/internal/llm/provider"
)

func TestOllamaProvider_Complete(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "/v1/chat/completions", r.URL.Path)
		var body map[string]interface{}
		json.NewDecoder(r.Body).Decode(&body)
		assert.Equal(t, "llama3", body["model"])
		resp := map[string]interface{}{
			"choices": []map[string]interface{}{
				{"message": map[string]string{"role": "assistant", "content": "hello"}},
			},
			"usage": map[string]int{"prompt_tokens": 10, "completion_tokens": 5},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	p := provider.NewOllamaProvider(srv.URL+"/v1", "", "llama3", false)
	resp, err := p.Complete(context.Background(), []provider.ChatMessage{{Role: "user", Content: "hi"}}, "")
	require.NoError(t, err)
	assert.Equal(t, "hello", resp.Content)
	assert.Equal(t, 10, resp.Usage.PromptTokens)
	assert.Equal(t, 5, resp.Usage.CompletionTokens)
}

func TestOllamaProvider_AnalyzeRepos(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		resp := map[string]interface{}{
			"choices": []map[string]interface{}{
				{"message": map[string]string{"role": "assistant", "content": `{"selected_repos":["repo-a"],"primary_repo":"repo-a","complexity":"Low","estimated_hours":1,"reasoning":"simple"}`}},
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	p := provider.NewOllamaProvider(srv.URL+"/v1", "", "llama3", false)
	result, err := p.AnalyzeRepos(context.Background(), "T-1", "title", "desc", []provider.RepoContext{{Name: "repo-a"}})
	require.NoError(t, err)
	assert.Equal(t, []string{"repo-a"}, result.SelectedRepos)
	assert.Equal(t, "repo-a", result.PrimaryRepo)
	assert.Equal(t, "Low", result.Complexity)
}

func TestOpenAIProvider_Complete(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "Bearer sk-test", r.Header.Get("Authorization"))
		resp := map[string]interface{}{
			"choices": []map[string]interface{}{
				{"message": map[string]string{"role": "assistant", "content": "hi from openai"}},
			},
			"usage": map[string]int{"prompt_tokens": 8, "completion_tokens": 3},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	p := provider.NewOpenAIProvider(srv.URL, "sk-test", "gpt-4")
	resp, err := p.Complete(context.Background(), []provider.ChatMessage{{Role: "user", Content: "hi"}}, "")
	require.NoError(t, err)
	assert.Equal(t, "hi from openai", resp.Content)
	assert.Equal(t, "openai", resp.ProviderName)
}

func TestAnthropicProvider_Complete(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		assert.Equal(t, "sk-ant", r.Header.Get("x-api-key"))
		assert.Equal(t, "2023-06-01", r.Header.Get("anthropic-version"))
		resp := map[string]interface{}{
			"content": []map[string]interface{}{
				{"type": "text", "text": "hi from claude"},
			},
			"usage": map[string]int{"input_tokens": 5, "output_tokens": 2},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	p := provider.NewAnthropicProvider(srv.URL, "sk-ant", "claude-3")
	resp, err := p.Complete(context.Background(), []provider.ChatMessage{{Role: "user", Content: "hi"}}, "")
	require.NoError(t, err)
	assert.Equal(t, "hi from claude", resp.Content)
	assert.Equal(t, 5, resp.Usage.PromptTokens)
	assert.Equal(t, 2, resp.Usage.CompletionTokens)
}

func TestOllamaProvider_IsAvailable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
	}))
	defer srv.Close()
	p := provider.NewOllamaProvider(srv.URL+"/v1", "", "llama3", false)
	assert.True(t, p.IsAvailable(context.Background()))
}

func TestOllamaProvider_IsAvailable_Unreachable(t *testing.T) {
	p := provider.NewOllamaProvider("http://127.0.0.1:1/v1", "", "llama3", false)
	assert.False(t, p.IsAvailable(context.Background()))
}