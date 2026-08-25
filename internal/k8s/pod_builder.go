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

package k8s

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

type PodSpecParams struct {
	TicketID            string
	TicketTitle         string
	Repos              []RepoRef
	AssignmentMD       string
	Analysis           map[string]interface{}
	AgentID            string
	QueueID            string
	GitLabHost         string
	GitUser            string
	GitLabToken        string
	GitHubToken        string
	GitHubHost         string
	OllamaBaseURL      string
	OpencodeModel      string
	OllamaCloudAPIKey  string
	MCPServers         []MCPServerRef
	PluginNames        []string
	MemoryMD           string
	Branch             string
	GitSSLNoVerify     bool
	PermissionWrite    string
	PermissionBash     string
	PermissionExtDir   string
	PermissionDoomLoop string
}

type RepoRef struct {
	Name   string `json:"name"`
	URL    string `json:"url"`
	Branch string `json:"branch"`
}

type MCPServerRef struct {
	Name       string            `json:"name"`
	Command    string            `json:"command"`
	Args       []string          `json:"args,omitempty"`
	Env        map[string]string `json:"environment,omitempty"`
	ServerType string            `json:"server_type"`
	Enabled    bool              `json:"enabled"`
}

func BuildPodSpec(params PodSpecParams) *corev1.Pod {
	podName := fmt.Sprintf("agent-worker-%s", strings.ToLower(params.TicketID))
	_ = buildReposJSON(params.Repos)

	labels := map[string]string{
		"app.kubernetes.io/name":      "hivemind",
		"app.kubernetes.io/component": "agent",
		"ticket-id":                   params.TicketID,
	}

	initContainer := corev1.Container{
		Name:            "clone-repos",
		Image:           getEnv("AGENT_IMAGE", "hivemind-opencode:v0.1.0"),
		ImagePullPolicy: corev1.PullIfNotPresent,
		VolumeMounts: []corev1.VolumeMount{
			{Name: "workspace", MountPath: "/workspace"},
			{Name: "repos-config", MountPath: "/config"},
		},
		Env: []corev1.EnvVar{
			{Name: "GITLAB_HOST", Value: params.GitLabHost},
			{Name: "GITLAB_TOKEN", ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "gitlab-token"},
					Key:                  "token",
				},
			}},
			{Name: "GITHUB_TOKEN", ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "github-token"},
					Key:                  "token",
				},
			}},
			{Name: "GITHUB_HOST", Value: params.GitHubHost},
			{Name: "GIT_USER", Value: params.GitUser},
			{Name: "GIT_SSL_NO_VERIFY", Value: func() string {
				if params.GitSSLNoVerify {
					return "1"
				}
				return "0"
			}()},
		},
		Command: []string{"/bin/bash", "-c"},
		Args:    []string{buildCloneScript(params.Repos)},
		Resources: corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("200m"),
				corev1.ResourceMemory: resource.MustParse("256Mi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("1"),
				corev1.ResourceMemory: resource.MustParse("1Gi"),
			},
		},
	}

	opencodeEnv := []corev1.EnvVar{
		{Name: "GITLAB_TOKEN", ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "gitlab-token"},
				Key:                  "token",
			},
		}},
		{Name: "GITLAB_HOST", Value: params.GitLabHost},
		{Name: "GIT_USER", Value: params.GitUser},
		{Name: "GITLAB_USER", Value: params.GitUser},
		{Name: "GITHUB_TOKEN", ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "github-token"},
				Key:                  "token",
			},
		}},
		{Name: "GITHUB_HOST", Value: params.GitHubHost},
		{Name: "OLLAMA_BASE_URL", Value: params.OllamaBaseURL},
		{Name: "OPENCODE_MODEL", Value: params.OpencodeModel},
		{Name: "OPENCODE_PLUGINS", Value: func() string {
			if len(params.PluginNames) == 0 {
				return `["opencode-snip","opencode-agent-memory","opencode-handoff"]`
			}
			return toJSONString(params.PluginNames)
		}()},
		{Name: "QUEUE_ID", Value: params.QueueID},
		{Name: "TICKET_ID", Value: params.TicketID},
		{Name: "AGENT_ID", Value: params.AgentID},
		{Name: "BRANCH", Value: params.Branch},
		{Name: "OPENCODE_SERVER_PASSWORD", Value: getEnv("OPENCODE_SERVER_PASSWORD", "")},
		{Name: "COMMENT_POLL_INTERVAL", Value: getEnv("COMMENT_POLL_INTERVAL", "30")},
		{Name: "ORCHESTRATOR_URL", Value: fmt.Sprintf("http://orchestrator.%s.svc.cluster.local:8080", getEnv("AGENT_NAMESPACE", "hivemind"))},
		{Name: "HIVEMIND_API_KEY", ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "orchestrator-env"},
				Key:                  "HIVEMIND_API_KEY",
			},
		}},
		{Name: "MODEL_ROUTING_ENABLED", Value: getEnv("MODEL_ROUTING_ENABLED", "false")},
		{Name: "SIMPLE_MODEL", Value: getEnv("SIMPLE_MODEL", "")},
		{Name: "COMPLEX_MODEL", Value: getEnv("COMPLEX_MODEL", "")},
		{Name: "DRY_RUN", Value: "false"},
		{Name: "OPENCODE_PERMISSION_WRITE", Value: defaultIfEmpty(params.PermissionWrite, "allow")},
		{Name: "OPENCODE_PERMISSION_BASH", Value: defaultIfEmpty(params.PermissionBash, "allow")},
		{Name: "OPENCODE_PERMISSION_EXTERNAL_DIRECTORY", Value: defaultIfEmpty(params.PermissionExtDir, "allow")},
		{Name: "OPENCODE_PERMISSION_DOOM_LOOP", Value: defaultIfEmpty(params.PermissionDoomLoop, "deny")},
		{Name: "TEST_COMMAND", Value: getEnv("TEST_COMMAND", "")},
		{Name: "BROWSER", Value: "none"},
		{Name: "DISPLAY", Value: ""},
		{Name: "NO_OPEN", Value: "1"},
		{Name: "GIT_SSL_NO_VERIFY", Value: func() string {
			if params.GitSSLNoVerify {
				return "1"
			}
			return "0"
		}()},
	}

	if params.OllamaCloudAPIKey != "" {
		opencodeEnv = append(opencodeEnv,
			corev1.EnvVar{Name: "OLLAMA_CLOUD_API_KEY", ValueFrom: &corev1.EnvVarSource{
				SecretKeyRef: &corev1.SecretKeySelector{
					LocalObjectReference: corev1.LocalObjectReference{Name: "ollama-cloud-api-key"},
					Key:                  "api-key",
				},
			}},
		)
	}

	llmProvider := getEnv("LLM_PROVIDER", "")
	if llmProvider != "" {
		opencodeEnv = append(opencodeEnv, corev1.EnvVar{Name: "LLM_PROVIDER", Value: llmProvider})
	}
	openaiKey := getEnv("OPENAI_API_KEY", "")
	if openaiKey != "" {
		opencodeEnv = append(opencodeEnv, corev1.EnvVar{Name: "OPENAI_API_KEY", ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "openai-api-key"},
				Key:                  "api-key",
			},
		}})
	}
	openaiBase := getEnv("OPENAI_BASE_URL", "")
	if openaiBase != "" {
		opencodeEnv = append(opencodeEnv, corev1.EnvVar{Name: "OPENAI_BASE_URL", Value: openaiBase})
	}
	anthropicKey := getEnv("ANTHROPIC_API_KEY", "")
	if anthropicKey != "" {
		opencodeEnv = append(opencodeEnv, corev1.EnvVar{Name: "ANTHROPIC_API_KEY", ValueFrom: &corev1.EnvVarSource{
			SecretKeyRef: &corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "anthropic-api-key"},
				Key:                  "api-key",
			},
		}})
	}

	mainContainer := corev1.Container{
		Name:            "opencode-agent",
		Image:           getEnv("AGENT_IMAGE", "hivemind-opencode:v0.1.0"),
		ImagePullPolicy: corev1.PullIfNotPresent,
		VolumeMounts: []corev1.VolumeMount{
			{Name: "workspace", MountPath: "/workspace"},
			{Name: "task-prompt", MountPath: "/etc/task"},
			{Name: "opencode-config", MountPath: "/mnt/opencode-config"},
			{Name: "memory-blocks", MountPath: "/mnt/memory-blocks"},
			{Name: "repos-config", MountPath: "/config"},
		},
		Env:    opencodeEnv,
		Command: []string{"/scripts/entrypoint.sh"},
		Args:   []string{"/etc/task/task.md"},
		Ports: []corev1.ContainerPort{
			{Name: "opencode-web", ContainerPort: 4096},
		},
		Resources: corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("500m"),
				corev1.ResourceMemory: resource.MustParse("1Gi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("4"),
				corev1.ResourceMemory: resource.MustParse("8Gi"),
			},
		},
	}

	volumes := []corev1.Volume{
		{Name: "workspace", VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{}}},
		{Name: "repos-config", VolumeSource: corev1.VolumeSource{ConfigMap: &corev1.ConfigMapVolumeSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: fmt.Sprintf("%s-repos", podName)},
		}}},
		{Name: "task-prompt", VolumeSource: corev1.VolumeSource{ConfigMap: &corev1.ConfigMapVolumeSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: fmt.Sprintf("%s-assignment", podName)},
		}}},
		{Name: "opencode-config", VolumeSource: corev1.VolumeSource{ConfigMap: &corev1.ConfigMapVolumeSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: fmt.Sprintf("%s-opencode", podName)},
		}}},
		{Name: "memory-blocks", VolumeSource: corev1.VolumeSource{ConfigMap: &corev1.ConfigMapVolumeSource{
			LocalObjectReference: corev1.LocalObjectReference{Name: fmt.Sprintf("%s-memory", podName)},
		}}},
	}

	pod := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      podName,
			Namespace: getEnv("AGENT_NAMESPACE", "hivemind"),
			Labels:    labels,
		},
		Spec: corev1.PodSpec{
			Hostname:    podName,
			Subdomain:   "agent-session",
			RestartPolicy: corev1.RestartPolicyNever,
			ServiceAccountName: "agent-runner",
			AutomountServiceAccountToken: ptr(false),
			SecurityContext: &corev1.PodSecurityContext{
				RunAsNonRoot: ptr(true),
				RunAsUser:    ptr(int64(1000)),
				RunAsGroup:   ptr(int64(1000)),
				FSGroup:      ptr(int64(1000)),
			},
			Volumes:        volumes,
			InitContainers: []corev1.Container{initContainer},
			Containers:     []corev1.Container{mainContainer},
		},
	}

	return pod
}

type SpawnResult struct {
	PodName string
	Success bool
}

func SpawnAgentPod(ctx context.Context, client *Client, params PodSpecParams) (*SpawnResult, error) {
	podName := fmt.Sprintf("agent-worker-%s", strings.ToLower(params.TicketID))
	namespace := getEnv("AGENT_NAMESPACE", "hivemind")

	if err := client.EnsureNamespace(ctx, namespace); err != nil {
		return nil, fmt.Errorf("ensure namespace: %w", err)
	}

	reposJSON := buildReposJSON(params.Repos)
	opencodeConfigJSON := toJSONString(buildOpencodeConfig(params))
	memoryMD := params.MemoryMD
	if memoryMD == "" {
		memoryMD = defaultMemoryMD()
	}

	cmLabels := map[string]string{"ticket-id": params.TicketID}

	if _, err := client.CreateConfigMap(ctx, fmt.Sprintf("%s-repos", podName), map[string]string{"repos.json": reposJSON}, cmLabels); err != nil {
		return nil, fmt.Errorf("create repos configmap: %w", err)
	}
	slog.Info("configmap created", "name", fmt.Sprintf("%s-repos", podName))

	if _, err := client.CreateConfigMap(ctx, fmt.Sprintf("%s-assignment", podName), map[string]string{"task.md": params.AssignmentMD}, cmLabels); err != nil {
		return nil, fmt.Errorf("create assignment configmap: %w", err)
	}
	slog.Info("configmap created", "name", fmt.Sprintf("%s-assignment", podName))

	if _, err := client.CreateConfigMap(ctx, fmt.Sprintf("%s-opencode", podName), map[string]string{"opencode.json": opencodeConfigJSON}, cmLabels); err != nil {
		return nil, fmt.Errorf("create opencode configmap: %w", err)
	}
	slog.Info("configmap created", "name", fmt.Sprintf("%s-opencode", podName))

	if _, err := client.CreateConfigMap(ctx, fmt.Sprintf("%s-memory", podName), map[string]string{"memory.md": memoryMD}, cmLabels); err != nil {
		return nil, fmt.Errorf("create memory configmap: %w", err)
	}
	slog.Info("configmap created", "name", fmt.Sprintf("%s-memory", podName))

	// Secrets (gitlab-token, ollama-cloud-api-key, openai-api-key,
	// anthropic-api-key, orchestrator-env) are ensured once at orchestrator
	// startup via Client.EnsureSecrets to avoid concurrent-spawn races.

	pod := BuildPodSpec(params)

	existing, err := client.GetPod(ctx, podName)
	if err != nil {
		return nil, fmt.Errorf("check existing pod: %w", err)
	}
	if existing != nil {
		if err := client.DeletePod(ctx, podName); err != nil {
			return nil, fmt.Errorf("delete existing pod: %w", err)
		}
		if err := client.WaitForPodDeletion(ctx, podName, 60*time.Second); err != nil {
			return nil, fmt.Errorf("wait for pod deletion: %w", err)
		}
	}

	created, err := client.CreatePod(ctx, pod)
	if err != nil {
		return nil, fmt.Errorf("create pod: %w", err)
	}
	slog.Info("agent pod started", "pod", podName, "uid", created.UID)

	complexity := "Medium"
	if c, ok := params.Analysis["complexity"].(string); ok {
		complexity = c
	}
	primary := ""
	if p, ok := params.Analysis["primary_repo"].(string); ok {
		primary = p
	} else if len(params.Repos) > 0 {
		primary = params.Repos[0].Name
	}
	slog.Info("ticket processed", "ticket_id", params.TicketID, "title", params.TicketTitle,
		"complexity", complexity, "primary", primary, "repos", len(params.Repos))

	return &SpawnResult{PodName: podName, Success: true}, nil
}

func buildCloneScript(repos []RepoRef) string {
	reposJSON := buildReposJSON(repos)
	script := `set -uo pipefail
FALLBACK_BRANCHES="development qa main master"

# Configure git credential helper so tokens never appear in URLs / logs.
git config --global credential.helper store
git config --global credential.interactive false
export HOME=/workspace

cat > /workspace/repos.json << 'REPOSEOF'
` + reposJSON + `
REPOSEOF

# Write git credentials for all known GitLab/GitHub hosts upfront
# so they are available before any clone attempt.
if [ -n "$GITLAB_TOKEN" ] && [ -n "$GITLAB_HOST" ]; then
  PROTO="https"
  if echo "$GITLAB_HOST" | grep -qE '^https?://'; then
    PROTO=$(echo "$GITLAB_HOST" | sed -E 's|^(https?://).*|\1|')
  fi
  HOST=$(echo "$GITLAB_HOST" | sed -E 's|^https?://([^/@]+).*|\1|')
  echo "${PROTO}${GIT_USER}:${GITLAB_TOKEN}@${HOST}" >> /workspace/.git-credentials
  chmod 600 /workspace/.git-credentials
fi
if [ -n "$GITHUB_TOKEN" ] && [ -n "$GITHUB_HOST" ]; then
  PROTO="https"
  if echo "$GITHUB_HOST" | grep -qE '^https?://'; then
    PROTO=$(echo "$GITHUB_HOST" | sed -E 's|^(https?://).*|\1|')
  fi
  HOST=$(echo "$GITHUB_HOST" | sed -E 's|^https?://([^/@]+).*|\1|')
  echo "${PROTO}${GIT_USER}:${GITHUB_TOKEN}@${HOST}" >> /workspace/.git-credentials
  chmod 600 /workspace/.git-credentials
fi

for repo in $(jq -r 'keys[]' /workspace/repos.json); do
  url=$(jq -r --arg r "$repo" '.[$r].url' /workspace/repos.json)
  branch=$(jq -r --arg r "$repo" '.[$r].branch' /workspace/repos.json)

  # Strip any existing credentials from the URL.
  # Credentials are already in ~/.git-credentials (set before the loop).
  proto=""
  host=""
  if echo "$url" | grep -qE "^https?://"; then
    proto=$(echo "$url" | sed -E "s|^(https?://).*|\1|")
    host=$(echo "$url" | sed -E "s|^https?://([^/@]+).*|\1|")
    url="${proto}${host}$(echo "$url" | sed -E "s|^https?://[^/@]+||")"
  fi

  echo "Cloning $repo (branch: $branch) ..."
  if git clone -b "$branch" --single-branch "$url" "/workspace/$repo" 2>&1; then
    echo "Cloned $repo on branch $branch"
  else
    echo "Branch $branch not found for $repo, trying fallback branches..."
    CLONED=false
    for fb in $branch $FALLBACK_BRANCHES; do
      if [ "$fb" = "$branch" ]; then continue; fi
      rm -rf "/workspace/$repo" 2>/dev/null || true
      if git clone -b "$fb" --single-branch "$url" "/workspace/$repo" 2>&1; then
        echo "Cloned $repo on fallback branch $fb"
        CLONED=true
        break
      fi
    done
    if [ "$CLONED" = "false" ]; then
      rm -rf "/workspace/$repo" 2>/dev/null || true
      echo "No fallback branch worked for $repo, cloning default branch..."
      if git clone "$url" "/workspace/$repo" 2>&1; then
        echo "Cloned $repo on default branch"
      else
        echo "Failed to clone $repo - skipping"
        continue
      fi
    fi
  fi
  echo "Init leankg $repo ..."
  cd "/workspace/$repo"
  command -v leankg >/dev/null 2>&1 && { leankg init || echo "leankg init failed for $repo"; } || echo "leankg not installed, skipping init for $repo"
  echo "Index leankg $repo ..."
  command -v leankg >/dev/null 2>&1 && { leankg index . || echo "leankg index failed for $repo"; } || echo "leankg not installed, skipping index for $repo"
done

# Clean up credentials after clone to minimize exposure window.
# Keep /workspace/.git-credentials for the main container to use.
echo "All repos processed"
`
	return script
}

func buildReposJSON(repos []RepoRef) string {
	type repoEntry struct {
		URL    string `json:"url"`
		Branch string `json:"branch"`
	}
	m := make(map[string]repoEntry)
	for _, r := range repos {
		m[r.Name] = repoEntry{URL: r.URL, Branch: r.Branch}
	}
	data, _ := json.MarshalIndent(m, "", "  ")
	return string(data)
}

type opencodeConfig struct {
	Schema    string                       `json:"$schema"`
	Model     string                       `json:"model"`
	SmallModel string                     `json:"small_model"`
	Autoupdate bool                        `json:"autoupdate"`
	Share     string                       `json:"share"`
	Plugin    []string                     `json:"plugin"`
	Provider  map[string]providerEntry     `json:"provider"`
	MCP       map[string]interface{}       `json:"mcp"`
}

type providerEntry struct {
	NPM     string      `json:"npm"`
	Name    string      `json:"name"`
	Options interface{} `json:"options"`
	Models  map[string]modelEntry `json:"models"`
}

type modelEntry struct {
	Name    string                 `json:"name"`
	Options map[string]interface{} `json:"options,omitempty"`
}

func buildOpencodeConfig(params PodSpecParams) opencodeConfig {
	llmProvider := getEnv("LLM_PROVIDER", "")
	providerKey := "ollama"
	providerName := "Ollama"
	providerURL := params.OllamaBaseURL
	if providerURL == "" {
		providerURL = "http://localhost:11434/v1"
	}

	if llmProvider == "ollama_cloud" && params.OllamaCloudAPIKey != "" {
		providerKey = "ollama_cloud"
		providerName = "Ollama Cloud"
		providerURL = strings.TrimSuffix(providerURL, "/")
		if params.OllamaBaseURL == "" {
			providerURL = "https://ollama.com/v1"
		}
	}

	modelRef := fmt.Sprintf("%s/%s", providerKey, params.OpencodeModel)
	mcpEntries := make(map[string]interface{})
	for _, srv := range params.MCPServers {
		entry := map[string]interface{}{
			"type":    srv.ServerType,
			"command": srv.Command,
			"enabled": srv.Enabled,
		}
		if len(srv.Args) > 0 {
			entry["args"] = srv.Args
		}
		if len(srv.Env) > 0 {
			entry["environment"] = srv.Env
		}
		mcpEntries[srv.Name] = entry
	}

	providerOpts := map[string]interface{}{"baseURL": providerURL}
	if providerKey == "ollama_cloud" && params.OllamaCloudAPIKey != "" {
		providerOpts["headers"] = map[string]string{"Authorization": "Bearer ${OLLAMA_CLOUD_API_KEY}"}
	}

	return opencodeConfig{
		Schema:     "https://opencode.ai/config.json",
		Model:      modelRef,
		SmallModel: modelRef,
		Autoupdate: false,
		Share:      "disabled",
		Plugin:     append([]string{}, params.PluginNames...),
		Provider: map[string]providerEntry{
			providerKey: {
				NPM:     "@ai-sdk/openai-compatible",
				Name:    providerName,
				Options: providerOpts,
				Models: map[string]modelEntry{
					params.OpencodeModel: {
						Name: params.OpencodeModel,
						Options: map[string]interface{}{"num_ctx": float64(32768)},
					},
				},
			},
		},
		MCP: mcpEntries,
	}
}

func defaultMemoryMD() string {
	return `---
label: persona
description: Agent identity and behavior
limit: 5000
read_only: false
---
You are an autonomous software developer. Work carefully and methodically.

---
label: human
description: Operator preferences
limit: 5000
read_only: false
---
Prefer English UI language. Use Conventional Commits. Tests are mandatory.

---
label: project
description: Project conventions and architecture
limit: 5000
read_only: false
---
Tech-Stack: Vue 3 + TypeScript Frontend, Go Backend.
Tests: pnpm test && vue-tsc --noEmit (Frontend), go test ./... (Backend).
`
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// envOr returns os.Getenv(key) or fallback if empty. Same as getEnv but
// without duplicating the config.getEnv helper — kept here only for
// backward compatibility with pod_builder tests.
var _ = getEnv

func defaultIfEmpty(val, fallback string) string {
	if val == "" {
		return fallback
	}
	return val
}

func toJSONString(v interface{}) string {
	data, _ := json.Marshal(v)
	return string(data)
}

// BuildCloneScriptForTest exports buildCloneScript for testing.
func BuildCloneScriptForTest(repos []RepoRef) string { return buildCloneScript(repos) }

// BuildReposJSONForTest exports buildReposJSON for testing.
func BuildReposJSONForTest(repos []RepoRef) string { return buildReposJSON(repos) }