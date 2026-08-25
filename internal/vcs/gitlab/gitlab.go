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

package gitlab

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/vcs"
)

var gitLabTicketPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)PROJ-\d+`),
	regexp.MustCompile(`(?i)BUG-\d+`),
	regexp.MustCompile(`(?i)TASK-\d+`),
	regexp.MustCompile(`(?i)GL-\d+`),
}

type GitLabProvider struct {
	Host       string
	Token      string
	httpClient *http.Client
}

func New(host, token string) *GitLabProvider {
	if host == "" {
		host = "gitlab.com"
	}
	return &GitLabProvider{
		Host:  host,
		Token: token,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

func (g *GitLabProvider) Name() string         { return "gitlab" }
func (g *GitLabProvider) TokenEnvKey() string   { return "GITLAB_TOKEN" }
func (g *GitLabProvider) HostEnvKey() string    { return "GITLAB_HOST" }
func (g *GitLabProvider) GetToken() string       { return g.Token }
func (g *GitLabProvider) GetHost() string        { return g.Host }
func (g *GitLabProvider) GetDefaultGitUser() string { return "gitlab-ci-token" }

func (g *GitLabProvider) AuthHeaders(token string) map[string]string {
	t := token
	if t == "" {
		t = g.Token
	}
	headers := map[string]string{"Content-Type": "application/json"}
	if t != "" {
		headers["PRIVATE-TOKEN"] = t
	}
	return headers
}

func (g *GitLabProvider) ParseMRURL(mrURL string) (string, string) {
	if mrURL == "" {
		return "", ""
	}
	sep := "/-/merge_requests/"
	if !strings.Contains(mrURL, sep) {
		sep = "/merge_requests/"
	}
	parts := strings.SplitN(mrURL, sep, 2)
	if len(parts) < 2 {
		return "", ""
	}
	projectPath := strings.TrimPrefix(parts[0], "https://")
	projectPath = strings.TrimPrefix(projectPath, "http://")
	if idx := strings.Index(projectPath, "/"); idx >= 0 {
		projectPath = projectPath[idx+1:]
	}
	projectPath = strings.TrimSuffix(projectPath, ".git")
	mrIID := strings.Split(parts[1], "/")[0]
	return projectPath, mrIID
}

func (g *GitLabProvider) GetBranchListURL(projectPath string) string {
	return fmt.Sprintf("https://%s/%s/-/branches", g.Host, projectPath)
}

func (g *GitLabProvider) ExtractTicketIDFromBranch(branch string) string {
	cleaned := strings.TrimPrefix(branch, "feature/")
	cleaned = strings.TrimPrefix(cleaned, "fix/")
	cleaned = strings.TrimPrefix(cleaned, "bugfix/")
	for _, re := range gitLabTicketPatterns {
		if m := re.FindString(cleaned); m != "" {
			return strings.ToUpper(m)
		}
	}
	return ""
}

func (g *GitLabProvider) ParseWebhookEvent(payload map[string]interface{}, headers map[string]string) *vcs.WebhookEvent {
	eventType := headers["X-Gitlab-Event"]
	if eventType == "Issue Hook" {
		objKind, _ := payload["object_kind"].(string)
		attrs, _ := payload["object_attributes"].(map[string]interface{})
		action, _ := attrs["action"].(string)
		if objKind == "issue" && (action == "open" || action == "update" || action == "reopen") {
			labels := extractGitLabLabels(payload)
			return &vcs.WebhookEvent{
				Type:        "issue",
				Action:     action,
				ProjectID:  nestedMap(payload, "project", "id"),
				ProjectPath: nestedString(payload, "project", "path_with_namespace"),
				IID:         attrs["iid"],
				Title:       stringVal(attrs["title"]),
				Description: stringVal(attrs["description"]),
				URL:         stringVal(attrs["url"]),
				Labels:      labels,
				Raw:         payload,
			}
		}
	} else if eventType == "Merge Request Hook" {
		attrs, _ := payload["object_attributes"].(map[string]interface{})
		action, _ := attrs["action"].(string)
		validActions := map[string]bool{"open": true, "update": true, "reopen": true, "merge": true, "close": true, "approval": true}
		if validActions[action] {
			return &vcs.WebhookEvent{
				Type:         "merge_request",
				Action:       action,
				ProjectID:    nestedMap(payload, "project", "id"),
				ProjectPath:  nestedString(payload, "project", "path_with_namespace"),
				IID:          attrs["iid"],
				Title:        stringVal(attrs["title"]),
				URL:          stringVal(attrs["url"]),
				State:        stringVal(attrs["state"]),
				SourceBranch: stringVal(attrs["source_branch"]),
				TargetBranch: stringVal(attrs["target_branch"]),
				Raw:          payload,
			}
		}
	}
	return nil
}

func (g *GitLabProvider) apiURL(path string) string {
	return fmt.Sprintf("https://%s/api/v4%s", g.Host, path)
}

func encodePath(projectPath string) string {
	return url.PathEscape(projectPath)
}

func extractGitLabLabels(payload map[string]interface{}) []string {
	labelsRaw, ok := payload["labels"]
	if !ok {
		return nil
	}
	switch v := labelsRaw.(type) {
	case []interface{}:
		var result []string
		for _, item := range v {
			if m, ok := item.(map[string]interface{}); ok {
				if title, ok := m["title"].(string); ok {
					result = append(result, title)
				}
			}
		}
		return result
	case []string:
		return v
	}
	return nil
}

func (g *GitLabProvider) FetchMR(ctx context.Context, mrURL string) (map[string]interface{}, error) {
	projectPath, mrIID := g.ParseMRURL(mrURL)
	if projectPath == "" || mrIID == "" {
		return nil, fmt.Errorf("invalid MR URL: %s", mrURL)
	}
	encodedPath := strings.ReplaceAll(projectPath, "/", "%2F")
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/merge_requests/%s", g.Host, encodedPath, mrIID)
	return g.doGet(ctx, url)
}

func (g *GitLabProvider) FetchMRComments(ctx context.Context, projectPath, mrIID string) ([]map[string]interface{}, error) {
	encodedPath := strings.ReplaceAll(projectPath, "/", "%2F")
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/merge_requests/%s/notes?sort=asc&per_page=50",
		g.Host, encodedPath, mrIID)
	return g.doGetList(ctx, url)
}

func (g *GitLabProvider) SearchOpenMRs(ctx context.Context, projectPath, sourceBranch string) ([]map[string]interface{}, error) {
	encodedPath := strings.ReplaceAll(projectPath, "/", "%2F")
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/merge_requests?state=opened&source_branch=%s",
		g.Host, encodedPath, sourceBranch)
	return g.doGetList(ctx, url)
}

func (g *GitLabProvider) CreateMR(ctx context.Context, projectPath, sourceBranch, targetBranch, title, description string) (map[string]interface{}, error) {
	encodedPath := strings.ReplaceAll(projectPath, "/", "%2F")
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/merge_requests", g.Host, encodedPath)
	body := map[string]interface{}{
		"source_branch":       sourceBranch,
		"target_branch":       targetBranch,
		"title":               title,
		"description":         description,
		"remove_source_branch": true,
	}
	return g.doPost(ctx, url, body)
}

func (g *GitLabProvider) FetchMRApprovals(ctx context.Context, projectPath, mrIID string) (map[string]interface{}, error) {
	encodedPath := strings.ReplaceAll(projectPath, "/", "%2F")
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/merge_requests/%s/approvals", g.Host, encodedPath, mrIID)
	return g.doGet(ctx, url)
}

func (g *GitLabProvider) ListBranches(ctx context.Context, projectPath string) ([]map[string]interface{}, error) {
	encodedPath := strings.ReplaceAll(projectPath, "/", "%2F")
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/repository/branches", g.Host, encodedPath)
	return g.doGetList(ctx, url)
}

func (g *GitLabProvider) ListProjects(ctx context.Context, opts ...vcs.ListProjectsOption) ([]map[string]interface{}, error) {
	url := fmt.Sprintf("https://%s/api/v4/projects?membership=true&order_by=name&per_page=100", g.Host)
	return g.doGetList(ctx, url)
}

func (g *GitLabProvider) CreateProjectHook(ctx context.Context, projectPath string, hookConfig map[string]interface{}) (map[string]interface{}, error) {
	encodedPath := projectPath
	if strings.Contains(projectPath, "/") {
		encodedPath = strings.ReplaceAll(projectPath, "/", "%2F")
	}
	url := fmt.Sprintf("https://%s/api/v4/projects/%s/hooks", g.Host, encodedPath)
	return g.doPost(ctx, url, hookConfig)
}

func (g *GitLabProvider) doGet(ctx context.Context, url string) (map[string]interface{}, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}
	for k, v := range g.AuthHeaders("") {
		req.Header.Set(k, v)
	}
	resp, err := g.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == 404 {
		return nil, nil
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("gitlab API error: %d", resp.StatusCode)
	}
	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func (g *GitLabProvider) doGetList(ctx context.Context, initialURL string) ([]map[string]interface{}, error) {
	var all []map[string]interface{}
	pageURL := initialURL
	for pageURL != "" {
		req, err := http.NewRequestWithContext(ctx, "GET", pageURL, nil)
		if err != nil {
			return nil, err
		}
		for k, v := range g.AuthHeaders("") {
			req.Header.Set(k, v)
		}
		resp, err := g.httpClient.Do(req)
		if err != nil {
			return nil, err
		}
		if resp.StatusCode == 404 {
			resp.Body.Close()
			return nil, nil
		}
		if resp.StatusCode >= 400 {
			resp.Body.Close()
			return nil, fmt.Errorf("gitlab API error: %d", resp.StatusCode)
		}
		var page []map[string]interface{}
		if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
			resp.Body.Close()
			return nil, err
		}
		resp.Body.Close()
		all = append(all, page...)
		nextPage := resp.Header.Get("X-Next-Page")
		if nextPage != "" {
			u, err := url.Parse(pageURL)
			if err != nil {
				break
			}
			q := u.Query()
			q.Set("page", nextPage)
			u.RawQuery = q.Encode()
			pageURL = u.String()
		} else {
			pageURL = ""
		}
	}
	return all, nil
}

func (g *GitLabProvider) doPost(ctx context.Context, url string, body map[string]interface{}) (map[string]interface{}, error) {
	data, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	for k, v := range g.AuthHeaders("") {
		req.Header.Set(k, v)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := g.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == 404 {
		return nil, nil
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("gitlab API error: %d", resp.StatusCode)
	}
	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func nestedString(m map[string]interface{}, keys ...string) string {
	current := m
	for i, key := range keys {
		if i == len(keys)-1 {
			if v, ok := current[key].(string); ok {
				return v
			}
			return ""
		}
		if next, ok := current[key].(map[string]interface{}); ok {
			current = next
		} else {
			return ""
		}
	}
	return ""
}

func nestedMap(m map[string]interface{}, keys ...string) interface{} {
	current := m
	for i, key := range keys {
		if i == len(keys)-1 {
			return current[key]
		}
		if next, ok := current[key].(map[string]interface{}); ok {
			current = next
		} else {
			return nil
		}
	}
	return nil
}

func stringVal(v interface{}) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}