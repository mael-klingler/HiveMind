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

package github

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/vcs"
)

var gitHubTicketPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)PROJ-\d+`),
	regexp.MustCompile(`(?i)BUG-\d+`),
	regexp.MustCompile(`(?i)TASK-\d+`),
	regexp.MustCompile(`(?i)GH-\d+`),
}

type GitHubProvider struct {
	Host       string
	Token      string
	httpClient *http.Client
}

func New(host, token string) *GitHubProvider {
	if host == "" {
		host = "github.com"
	}
	return &GitHubProvider{
		Host:  host,
		Token: token,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

func (g *GitHubProvider) Name() string         { return "github" }
func (g *GitHubProvider) TokenEnvKey() string   { return "GITHUB_TOKEN" }
func (g *GitHubProvider) HostEnvKey() string    { return "GITHUB_HOST" }
func (g *GitHubProvider) GetToken() string       { return g.Token }
func (g *GitHubProvider) GetHost() string        { return g.Host }
func (g *GitHubProvider) GetDefaultGitUser() string { return "x-access-token" }

func (g *GitHubProvider) baseAPIURL() string {
	if g.Host == "github.com" {
		return "https://api.github.com"
	}
	return fmt.Sprintf("https://%s/api/v3", g.Host)
}

func (g *GitHubProvider) AuthHeaders(token string) map[string]string {
	t := token
	if t == "" {
		t = g.Token
	}
	headers := map[string]string{
		"Content-Type": "application/json",
		"Accept":       "application/vnd.github+json",
	}
	if t != "" {
		headers["Authorization"] = fmt.Sprintf("Bearer %s", t)
		headers["X-GitHub-Api-Version"] = "2022-11-28"
	}
	return headers
}

func (g *GitHubProvider) ParseMRURL(mrURL string) (string, string) {
	if mrURL == "" || !strings.Contains(mrURL, "/pull/") {
		return "", ""
	}
	parts := strings.SplitN(mrURL, "/pull/", 2)
	if len(parts) < 2 {
		return "", ""
	}
	repoPath := strings.TrimPrefix(parts[0], "https://")
	repoPath = strings.TrimPrefix(repoPath, "http://")
	if idx := strings.Index(repoPath, "/"); idx >= 0 {
		repoPath = repoPath[idx+1:]
	}
	prNumber := strings.Split(parts[1], "/")[0]
	prNumber = strings.Split(prNumber, "?")[0]
	return repoPath, prNumber
}

func (g *GitHubProvider) GetBranchListURL(projectPath string) string {
	return fmt.Sprintf("https://%s/%s/branches", g.Host, projectPath)
}

func (g *GitHubProvider) ExtractTicketIDFromBranch(branch string) string {
	cleaned := strings.TrimPrefix(branch, "feature/")
	cleaned = strings.TrimPrefix(cleaned, "fix/")
	cleaned = strings.TrimPrefix(cleaned, "bugfix/")
	for _, re := range gitHubTicketPatterns {
		if m := re.FindString(cleaned); m != "" {
			return strings.ToUpper(m)
		}
	}
	return ""
}

func (g *GitHubProvider) ParseWebhookEvent(payload map[string]interface{}, headers map[string]string) *vcs.WebhookEvent {
	eventType := headers["X-GitHub-Event"]
	if eventType == "issues" {
		action, _ := payload["action"].(string)
		if action == "opened" || action == "edited" || action == "reopened" {
			issue, _ := payload["issue"].(map[string]interface{})
			repo, _ := payload["repository"].(map[string]interface{})
			actionMap := map[string]string{"opened": "open", "edited": "update", "reopened": "reopen"}
			labels := extractGitHubLabels(issue)
			return &vcs.WebhookEvent{
				Type:         "issue",
				Action:       actionMap[action],
				ProjectID:    repo["id"],
				ProjectPath:  stringVal(repo["full_name"]),
				IID:          issue["number"],
				Title:        stringVal(issue["title"]),
				Description:  stringVal(issue["body"]),
				URL:          stringVal(issue["html_url"]),
				Labels:       labels,
				Raw:          payload,
			}
		}
	} else if eventType == "pull_request" {
		action, _ := payload["action"].(string)
		validActions := map[string]bool{"opened": true, "synchronize": true, "reopened": true, "closed": true, "review_requested": true}
		if validActions[action] {
			pr, _ := payload["pull_request"].(map[string]interface{})
			repo, _ := payload["repository"].(map[string]interface{})
			actionMap := map[string]string{
				"opened": "open", "synchronize": "update", "reopened": "reopen",
				"closed": "close", "review_requested": "update",
			}
			head, _ := pr["head"].(map[string]interface{})
			base, _ := pr["base"].(map[string]interface{})
			return &vcs.WebhookEvent{
				Type:         "merge_request",
				Action:       actionMap[action],
				ProjectID:    repo["id"],
				ProjectPath:  stringVal(repo["full_name"]),
				IID:          pr["number"],
				Title:        stringVal(pr["title"]),
				URL:          stringVal(pr["html_url"]),
				State:        stringVal(pr["state"]),
				SourceBranch: stringVal(head["ref"]),
				TargetBranch: stringVal(base["ref"]),
				Raw:          payload,
			}
		}
	}
	return nil
}

func extractGitHubLabels(issue map[string]interface{}) []string {
	labelsRaw, ok := issue["labels"]
	if !ok {
		return nil
	}
	switch v := labelsRaw.(type) {
	case []interface{}:
		var result []string
		for _, item := range v {
			if m, ok := item.(map[string]interface{}); ok {
				if name, ok := m["name"].(string); ok {
					result = append(result, name)
				}
			}
		}
		return result
	case []string:
		return v
	}
	return nil
}

func (g *GitHubProvider) FetchMR(ctx context.Context, mrURL string) (map[string]interface{}, error) {
	repoPath, prNumber := g.ParseMRURL(mrURL)
	if repoPath == "" || prNumber == "" {
		return nil, fmt.Errorf("invalid PR URL: %s", mrURL)
	}
	url := fmt.Sprintf("%s/repos/%s/pulls/%s", g.baseAPIURL(), repoPath, prNumber)
	return g.doGet(ctx, url)
}

func (g *GitHubProvider) FetchMRComments(ctx context.Context, projectPath, mrIID string) ([]map[string]interface{}, error) {
	comments1, _ := g.doGetList(ctx, fmt.Sprintf("%s/repos/%s/issues/%s/comments", g.baseAPIURL(), projectPath, mrIID))
	comments2, _ := g.doGetList(ctx, fmt.Sprintf("%s/repos/%s/pulls/%s/comments", g.baseAPIURL(), projectPath, mrIID))
	var combined []map[string]interface{}
	if comments1 != nil {
		combined = append(combined, comments1...)
	}
	if comments2 != nil {
		combined = append(combined, comments2...)
	}
	if combined == nil {
		return nil, nil
	}
	return combined, nil
}

func (g *GitHubProvider) SearchOpenMRs(ctx context.Context, projectPath, sourceBranch string) ([]map[string]interface{}, error) {
	url := fmt.Sprintf("%s/repos/%s/pulls?state=open&head=%s", g.baseAPIURL(), projectPath, sourceBranch)
	return g.doGetList(ctx, url)
}

func (g *GitHubProvider) CreateMR(ctx context.Context, projectPath, sourceBranch, targetBranch, title, description string) (map[string]interface{}, error) {
	url := fmt.Sprintf("%s/repos/%s/pulls", g.baseAPIURL(), projectPath)
	body := map[string]interface{}{
		"title": title,
		"head":  sourceBranch,
		"base":  targetBranch,
		"body":  description,
	}
	return g.doPost(ctx, url, body)
}

func (g *GitHubProvider) FetchMRApprovals(ctx context.Context, projectPath, mrIID string) (map[string]interface{}, error) {
	url := fmt.Sprintf("%s/repos/%s/pulls/%s/reviews", g.baseAPIURL(), projectPath, mrIID)
	reviews, err := g.doGetList(ctx, url)
	if err != nil {
		return nil, err
	}
	approved := false
	if reviews != nil {
		for _, r := range reviews {
			if state, ok := r["state"].(string); ok && state == "APPROVED" {
				approved = true
				break
			}
		}
	}
	return map[string]interface{}{
		"approved":     approved,
		"total_reviews": len(reviews),
	}, nil
}

func (g *GitHubProvider) ListBranches(ctx context.Context, projectPath string) ([]map[string]interface{}, error) {
	url := fmt.Sprintf("%s/repos/%s/branches", g.baseAPIURL(), projectPath)
	return g.doGetList(ctx, url)
}

func (g *GitHubProvider) ListProjects(ctx context.Context, opts ...vcs.ListProjectsOption) ([]map[string]interface{}, error) {
	url := fmt.Sprintf("%s/user/repos?affiliation=owner,collaborator,organization_member", g.baseAPIURL())
	return g.doGetList(ctx, url)
}

func (g *GitHubProvider) CreateProjectHook(ctx context.Context, projectPath string, hookConfig map[string]interface{}) (map[string]interface{}, error) {
	url := fmt.Sprintf("%s/repos/%s/hooks", g.baseAPIURL(), projectPath)
	return g.doPost(ctx, url, hookConfig)
}

func (g *GitHubProvider) doGet(ctx context.Context, url string) (map[string]interface{}, error) {
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
		return nil, fmt.Errorf("github API error: %d", resp.StatusCode)
	}
	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func (g *GitHubProvider) doGetList(ctx context.Context, url string) ([]map[string]interface{}, error) {
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
		return nil, fmt.Errorf("github API error: %d", resp.StatusCode)
	}
	var result []map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func (g *GitHubProvider) doPost(ctx context.Context, url string, body map[string]interface{}) (map[string]interface{}, error) {
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
		return nil, fmt.Errorf("github API error: %d", resp.StatusCode)
	}
	var result map[string]interface{}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	return result, nil
}

func stringVal(v interface{}) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}