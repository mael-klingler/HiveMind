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

package vcs

import (
	"context"
)

// VCSProvider defines the interface for version control system operations.
type VCSProvider interface {
	Name() string
	TokenEnvKey() string
	HostEnvKey() string
	GetToken() string
	GetHost() string
	AuthHeaders(token string) map[string]string
	ParseMRURL(mrURL string) (projectPath string, mrIID string)
	FetchMR(ctx context.Context, mrURL string) (map[string]interface{}, error)
	FetchMRComments(ctx context.Context, projectPath, mrIID string) ([]map[string]interface{}, error)
	SearchOpenMRs(ctx context.Context, projectPath, sourceBranch string) ([]map[string]interface{}, error)
	CreateMR(ctx context.Context, projectPath, sourceBranch, targetBranch, title, description string) (map[string]interface{}, error)
	FetchMRApprovals(ctx context.Context, projectPath, mrIID string) (map[string]interface{}, error)
	ParseWebhookEvent(payload map[string]interface{}, headers map[string]string) *WebhookEvent
	GetBranchListURL(projectPath string) string
	GetDefaultGitUser() string
	ExtractTicketIDFromBranch(branch string) string
	ListBranches(ctx context.Context, projectPath string) ([]map[string]interface{}, error)
	ListProjects(ctx context.Context, opts ...ListProjectsOption) ([]map[string]interface{}, error)
	CreateProjectHook(ctx context.Context, projectPath string, hookConfig map[string]interface{}) (map[string]interface{}, error)
}

// WebhookEvent represents a parsed webhook event from a VCS provider.
type WebhookEvent struct {
	Type         string                 `json:"type"`
	Action       string                 `json:"action"`
	ProjectID    interface{}            `json:"project_id"`
	ProjectPath  string                 `json:"project_path"`
	IID          interface{}            `json:"iid"`
	Title        string                 `json:"title"`
	Description  string                 `json:"description"`
	URL          string                 `json:"url"`
	Labels       []string               `json:"labels"`
	State        string                 `json:"state,omitempty"`
	SourceBranch string                 `json:"source_branch,omitempty"`
	TargetBranch string                 `json:"target_branch,omitempty"`
	Raw          map[string]interface{} `json:"raw,omitempty"`
}

// ListProjectsOption is a functional option for listing projects.
type ListProjectsOption func(*ListProjectsOptions)

type ListProjectsOptions struct {
	MinAccessLevel int
	Membership     bool
	OrderBy        string
}