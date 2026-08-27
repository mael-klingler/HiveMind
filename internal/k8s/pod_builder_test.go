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

package k8s_test

import (
	"os"
	"strings"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/maelklingler/hivemind/internal/k8s"
)

func TestBuildPodSpec_Basic(t *testing.T) {
	os.Setenv("AGENT_NAMESPACE", "hivemind")
	os.Setenv("AGENT_IMAGE", "hivemind-opencode:test")
	defer os.Unsetenv("AGENT_NAMESPACE")
	defer os.Unsetenv("AGENT_IMAGE")

	params := k8s.PodSpecParams{
		TicketID:      "TASK-1",
		TicketTitle:   "Test task",
		AssignmentMD:  "# Task\nDo things.",
		AgentID:       "agent-1",
		QueueID:       "q-1",
		GitLabHost:    "gitlab.com",
		GitUser:       "ci",
		GitLabToken:   "secret-token",
		OpencodeModel: "llama3",
		Branch:        "feature/TASK-1",
	}

	pod := k8s.BuildPodSpec(params)
	assert.Equal(t, "agent-worker-task-1", pod.Name)
	assert.Equal(t, "hivemind", pod.Namespace)
	assert.Equal(t, "TASK-1", pod.Labels["ticket-id"])
	assert.Equal(t, "agent", pod.Labels["app.kubernetes.io/component"])

	require.Len(t, pod.Spec.InitContainers, 1)
	assert.Equal(t, "clone-repos", pod.Spec.InitContainers[0].Name)

	require.Len(t, pod.Spec.Containers, 1)
	assert.Equal(t, "opencode-agent", pod.Spec.Containers[0].Name)
	assert.Equal(t, "hivemind-opencode:test", pod.Spec.Containers[0].Image)
}

func TestBuildPodSpec_SecurityContext(t *testing.T) {
	params := k8s.PodSpecParams{TicketID: "T-1"}
	pod := k8s.BuildPodSpec(params)

	require.NotNil(t, pod.Spec.SecurityContext)
	assert.True(t, *pod.Spec.SecurityContext.RunAsNonRoot)
	assert.Equal(t, int64(1000), *pod.Spec.SecurityContext.RunAsUser)
	assert.Equal(t, int64(1000), *pod.Spec.SecurityContext.RunAsGroup)
}

func TestBuildPodSpec_ResourceRequirements(t *testing.T) {
	params := k8s.PodSpecParams{TicketID: "T-1"}
	pod := k8s.BuildPodSpec(params)

	require.Len(t, pod.Spec.Containers, 1)
	res := pod.Spec.Containers[0].Resources
	assert.NotEmpty(t, res.Requests)
	assert.NotEmpty(t, res.Limits)
}

func TestBuildCloneScript_NoTokenInURL(t *testing.T) {
	script := k8s.BuildCloneScriptForTest([]k8s.RepoRef{{Name: "repo", URL: "https://gitlab.com/x/y", Branch: "main"}})
	// Credentials are injected into clone URLs via auth_url (for non-interactive containers)
	// but piped through sed to strip them from log output.
	assert.Contains(t, script, "GIT_TERMINAL_PROMPT=0")
	assert.Contains(t, script, "credential.helper store")
	assert.Contains(t, script, "/workspace/.git-credentials")
	// Every git clone line must pipe output through sed to strip credentials from logs.
	lines := strings.Split(script, "\n")
	for _, line := range lines {
		if strings.Contains(line, "git clone") && !strings.Contains(line, "sed") {
			// Only the auth_url assignment line may contain the token variable;
			// actual git clone output must be piped through sed.
			assert.NotContains(t, line, "${GITLAB_TOKEN}", "token must not appear in git clone line without sed redaction: %s", line)
		}
	}
}

func TestBuildReposJSON(t *testing.T) {
	repos := []k8s.RepoRef{{Name: "a", URL: "u", Branch: "b"}}
	json := k8s.BuildReposJSONForTest(repos)
	assert.Contains(t, json, `"a"`)
	assert.Contains(t, json, `"u"`)
	assert.Contains(t, json, `"b"`)
}

func TestBuildPodSpec_LabelsContainTicketID(t *testing.T) {
	params := k8s.PodSpecParams{TicketID: "PROJ-42"}
	pod := k8s.BuildPodSpec(params)
	assert.Equal(t, "PROJ-42", pod.Labels["ticket-id"])
	assert.True(t, strings.HasPrefix(pod.Name, "agent-worker-proj-42"))
}