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
	"testing"
)

func TestParseMRURL(t *testing.T) {
	p := New("github.com", "token")
	tests := []struct {
		url      string
		wantPath string
		wantIID  string
	}{
		{"https://github.com/owner/repo/pull/123", "owner/repo", "123"},
		{"https://github.enterprise.com/org/project/pull/456", "org/project", "456"},
		{"", "", ""},
		{"https://gitlab.com/some/mr/1", "", ""},
	}
	for _, tt := range tests {
		path, iid := p.ParseMRURL(tt.url)
		if path != tt.wantPath || iid != tt.wantIID {
			t.Errorf("ParseMRURL(%q) = (%q, %q), want (%q, %q)", tt.url, path, iid, tt.wantPath, tt.wantIID)
		}
	}
}

func TestExtractTicketID(t *testing.T) {
	p := New("github.com", "token")
	tests := []struct {
		branch string
		want   string
	}{
		{"feature/GH-789-fix", "GH-789"},
		{"fix/PROJ-100-update", "PROJ-100"},
		{"main", ""},
	}
	for _, tt := range tests {
		got := p.ExtractTicketIDFromBranch(tt.branch)
		if got != tt.want {
			t.Errorf("ExtractTicketIDFromBranch(%q) = %q, want %q", tt.branch, got, tt.want)
		}
	}
}

func TestAuthHeaders(t *testing.T) {
	p := New("github.com", "mytoken")
	headers := p.AuthHeaders("")
	if headers["Authorization"] != "Bearer mytoken" {
		t.Errorf("expected Bearer mytoken, got %s", headers["Authorization"])
	}
	if headers["X-GitHub-Api-Version"] != "2022-11-28" {
		t.Errorf("expected API version header")
	}
}

func TestParseWebhookEvent_Issue(t *testing.T) {
	p := New("github.com", "token")
	payload := map[string]interface{}{
		"action": "opened",
		"issue": map[string]interface{}{
			"number":   float64(99),
			"title":    "Add dark mode",
			"body":     "Need a dark mode toggle",
			"html_url": "https://github.com/org/repo/issues/99",
			"labels":   []interface{}{},
		},
		"repository": map[string]interface{}{
			"id":        float64(1),
			"full_name": "org/repo",
		},
	}
	headers := map[string]string{"X-GitHub-Event": "issues"}
	event := p.ParseWebhookEvent(payload, headers)
	if event == nil {
		t.Fatal("expected non-nil event")
	}
	if event.Type != "issue" {
		t.Errorf("expected type=issue, got %s", event.Type)
	}
	if event.Title != "Add dark mode" {
		t.Errorf("expected title=Add dark mode, got %s", event.Title)
	}
}

func TestParseWebhookEvent_PR(t *testing.T) {
	p := New("github.com", "token")
	payload := map[string]interface{}{
		"action": "opened",
		"pull_request": map[string]interface{}{
			"number":   float64(42),
			"title":    "Fix authentication",
			"html_url": "https://github.com/org/repo/pull/42",
			"state":    "open",
			"head":     map[string]interface{}{"ref": "feature/GH-42-auth"},
			"base":     map[string]interface{}{"ref": "main"},
		},
		"repository": map[string]interface{}{
			"id":        float64(1),
			"full_name": "org/repo",
		},
	}
	headers := map[string]string{"X-GitHub-Event": "pull_request"}
	event := p.ParseWebhookEvent(payload, headers)
	if event == nil {
		t.Fatal("expected non-nil event")
	}
	if event.Type != "merge_request" {
		t.Errorf("expected type=merge_request, got %s", event.Type)
	}
	if event.SourceBranch != "feature/GH-42-auth" {
		t.Errorf("expected source_branch, got %s", event.SourceBranch)
	}
}