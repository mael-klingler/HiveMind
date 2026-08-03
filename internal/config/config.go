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

package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Port                string
	AgentNamespace      string
	AgentImage          string
	AgentMaxRetries     int
	AgentRetryDelay     int
	AgentStaleTimeout   int
	DatabaseURL         string
	RedisURL            string
	RedisEnabled        bool
	VCSProvider         string
	GitLabHost          string
	GitLabToken         string
	GitHubHost          string
	GitHubToken         string
	GitUser             string
	OllamaHost          string
	OllamaBaseURL       string
	OllamaModel         string
	OpencodeModel       string
	OllamaCloudAPIKey   string
	LLMProvider         string
	OpenAIAPIKey        string
	OpenAIBaseURL       string
	AnthropicAPIKey     string
	ModelRoutingEnabled bool
	SimpleModel         string
	ComplexModel        string
	OrchestratorConfig  string
	OrchestratorWebhookURL string
	GitLabWebhookSecret string
	GitHubWebhookSecret string
	HivemindAPIKey      string
	RateLimitPerMinute  int
	TestCommand         string
	OpencodePort        string
	OpencodeServerPassword string
	CommentPollInterval int
	QueuePollInterval   int
	AgentPollInterval   int
	ReviewPollInterval  int
	DryRun              bool
	GitSSLNoVerify      bool
	CORSOrigins         []string
	PVCMountPath        string
	WorkDir             string
	TrackBranch         string
	BranchFallbackOrder []string
	LeankgEnabled       bool
}

func Load() *Config {
	return &Config{
		Port:                getEnv("ORCHESTRATOR_PORT", "8080"),
		AgentNamespace:      getEnv("AGENT_NAMESPACE", "hivemind"),
		AgentImage:          getEnv("AGENT_IMAGE", "hivemind-opencode:latest"),
		AgentMaxRetries:     getEnvInt("AGENT_MAX_RETRIES", 3),
		AgentRetryDelay:     getEnvInt("AGENT_RETRY_DELAY", 120),
		AgentStaleTimeout:   getEnvInt("AGENT_STALE_TIMEOUT", 3600),
		DatabaseURL:         getEnv("DATABASE_URL", ""),
		RedisURL:            getEnv("REDIS_URL", "redis://localhost:6379"),
		RedisEnabled:        getEnvBool("REDIS_ENABLED", false),
		VCSProvider:         getEnv("VCS_PROVIDER", "gitlab"),
		GitLabHost:          getEnv("GITLAB_HOST", "gitlab.com"),
		GitLabToken:         getEnv("GITLAB_TOKEN", ""),
		GitHubHost:          getEnv("GITHUB_HOST", "github.com"),
		GitHubToken:         getEnv("GITHUB_TOKEN", ""),
		GitUser:             getEnv("GIT_USER", "gitlab-ci-token"),
		OllamaHost:          getEnv("OLLAMA_HOST", "http://localhost:11434"),
		OllamaBaseURL:       getEnv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
		OllamaModel:         getEnv("OLLAMA_MODEL", "llama3.1:8b"),
		OpencodeModel:       getEnv("OPENCODE_MODEL", "llama3.1:8b"),
		OllamaCloudAPIKey:   getEnv("OLLAMA_CLOUD_API_KEY", ""),
		LLMProvider:         getEnv("LLM_PROVIDER", ""),
		OpenAIAPIKey:        getEnv("OPENAI_API_KEY", ""),
		OpenAIBaseURL:       getEnv("OPENAI_BASE_URL", ""),
		AnthropicAPIKey:     getEnv("ANTHROPIC_API_KEY", ""),
		ModelRoutingEnabled: getEnvBool("MODEL_ROUTING_ENABLED", false),
		SimpleModel:         getEnv("SIMPLE_MODEL", ""),
		ComplexModel:        getEnv("COMPLEX_MODEL", ""),
		OrchestratorConfig:  getEnv("ORCHESTRATOR_CONFIG", "/app/config/orchestrator_config.json"),
		OrchestratorWebhookURL: getEnv("ORCHESTRATOR_WEBHOOK_URL", ""),
		GitLabWebhookSecret: getEnv("GITLAB_WEBHOOK_SECRET", ""),
		GitHubWebhookSecret: getEnv("GITHUB_WEBHOOK_SECRET", ""),
		HivemindAPIKey:      getEnv("HIVEMIND_API_KEY", ""),
		RateLimitPerMinute:  getEnvInt("RATE_LIMIT_PER_MINUTE", 30),
		TestCommand:         getEnv("TEST_COMMAND", ""),
		OpencodePort:        getEnv("OPENCODE_PORT", "4096"),
		OpencodeServerPassword: getEnv("OPENCODE_SERVER_PASSWORD", ""),
		CommentPollInterval: getEnvInt("COMMENT_POLL_INTERVAL", 30),
		QueuePollInterval:   getEnvInt("QUEUE_POLL_INTERVAL", 5),
		AgentPollInterval:   getEnvInt("AGENT_POLL_INTERVAL", 10),
		ReviewPollInterval:  getEnvInt("REVIEW_POLL_INTERVAL", 60),
		DryRun:              getEnvBool("DRY_RUN", false),
		GitSSLNoVerify:      getEnvBool("GIT_SSL_NO_VERIFY", false),
		CORSOrigins:         getEnvSlice("CORS_ORIGINS", []string{"*"}),
		PVCMountPath:        getEnv("PVC_MOUNT_PATH", "/mnt/repos"),
		WorkDir:             getEnv("WORK_DIR", "/app/work"),
		TrackBranch:         getEnv("TRACK_BRANCH", "development"),
		BranchFallbackOrder: getEnvSlice("BRANCH_FALLBACK_ORDER", []string{"development", "qa", "main", "master"}),
		LeankgEnabled:       getEnvBool("LEANKG_ENABLED", true),
	}
}

func (c *Config) OrchestrationURL() string {
	return fmt.Sprintf("http://orchestrator.%s.svc.cluster.local:8080", c.AgentNamespace)
}

var secretSettingKeys = map[string]bool{
	"git_token":            true,
	"gitlab_token":         true,
	"ollama_cloud_api_key": true,
	"openai_api_key":       true,
	"anthropic_api_key":    true,
	"hivemind_api_key":     true,
	"password":             true,
	"secret":               true,
}

func MaskSettings(settings map[string]string) map[string]string {
	masked := make(map[string]string, len(settings))
	for k, v := range settings {
		if secretSettingKeys[k] || strings.Contains(strings.ToLower(k), "token") ||
			strings.Contains(strings.ToLower(k), "key") || strings.Contains(strings.ToLower(k), "password") ||
			strings.Contains(strings.ToLower(k), "secret") {
			if len(v) > 4 {
				masked[k] = v[:2] + strings.Repeat("*", len(v)-4) + v[len(v)-2:]
			} else {
				masked[k] = "****"
			}
		} else {
			masked[k] = v
		}
	}
	return masked
}

var allowedSettings = map[string]bool{
	"git_token": true, "gitlab_token": true, "gitlab_host": true,
	"ollama_host": true, "ollama_base_url": true, "ollama_model": true,
	"opencode_model": true, "agent_namespace": true, "agent_image": true,
	"agent_max_retries": true, "dry_run": true, "vcs_provider": true,
	"comment_poll_interval": true, "agent_stale_timeout": true,
	"track_branch": true, "test_command": true, "pvc_mount_path": true,
	"work_dir": true, "leankg_enabled": true,
}

func ValidateSettingKey(key string) bool {
	return allowedSettings[key]
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getEnvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

func getEnvBool(key string, fallback bool) bool {
	if v := os.Getenv(key); v != "" {
		lower := strings.ToLower(v)
		return lower == "true" || lower == "1" || lower == "yes"
	}
	return fallback
}

func getEnvSlice(key string, fallback []string) []string {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	parts := strings.Split(v, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p != "" {
			result = append(result, p)
		}
	}
	if len(result) == 0 {
		return fallback
	}
	return result
}