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
	"fmt"

	"github.com/maelklingler/hivemind/internal/config"
)

// NewFromConfig creates the appropriate LLM provider based on config.
func NewFromConfig(cfg *config.Config) (Provider, error) {
	provider := detectProvider(cfg)
	switch provider {
	case "ollama":
		return NewOllamaProvider(cfg.OllamaBaseURL, "", cfg.OllamaModel, false), nil
	case "ollama_cloud":
		return NewOllamaProvider(cfg.OllamaBaseURL, cfg.OllamaCloudAPIKey, cfg.OllamaModel, true), nil
	case "openai":
		return NewOpenAIProvider(cfg.OpenAIBaseURL, cfg.OpenAIAPIKey, cfg.OllamaModel), nil
	case "anthropic":
		return NewAnthropicProvider("", cfg.AnthropicAPIKey, cfg.OllamaModel), nil
	default:
		return nil, fmt.Errorf("unknown LLM provider: %s", provider)
	}
}

func detectProvider(cfg *config.Config) string {
	if cfg.LLMProvider != "" {
		return cfg.LLMProvider
	}
	if cfg.OpenAIAPIKey != "" {
		return "openai"
	}
	if cfg.AnthropicAPIKey != "" {
		return "anthropic"
	}
	if cfg.OllamaCloudAPIKey != "" {
		return "ollama_cloud"
	}
	return "ollama"
}