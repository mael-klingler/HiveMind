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
	"testing"
)

func TestParseMRURL(t *testing.T) {
	p := New("gitlab.com", "token")
	tests := []struct {
		url      string
		wantPath string
		wantIID  string
	}{
		{"https://gitlab.com/mygroup/myproject/-/merge_requests/42", "mygroup/myproject", "42"},
		{"https://gitlab.example.com/org/repo/merge_requests/7", "org/repo", "7"},
		{"", "", ""},
		{"not-a-url", "", ""},
	}
	for _, tt := range tests {
		path, iid := p.ParseMRURL(tt.url)
		if path != tt.wantPath || iid != tt.wantIID {
			t.Errorf("ParseMRURL(%q) = (%q, %q), want (%q, %q)", tt.url, path, iid, tt.wantPath, tt.wantIID)
		}
	}
}

func TestExtractTicketID(t *testing.T) {
	p := New("gitlab.com", "token")
	tests := []struct {
		branch string
		want   string
	}{
		{"feature/PROJ-123-add-login", "PROJ-123"},
		{"fix/BUG-456-crash", "BUG-456"},
		{"feature/something-else", ""},
	}
	for _, tt := range tests {
		got := p.ExtractTicketIDFromBranch(tt.branch)
		if got != tt.want {
			t.Errorf("ExtractTicketIDFromBranch(%q) = %q, want %q", tt.branch, got, tt.want)
		}
	}
}

func TestAuthHeaders(t *testing.T) {
	p := New("gitlab.com", "mytoken")
	headers := p.AuthHeaders("")
	if headers["PRIVATE-TOKEN"] != "mytoken" {
		t.Errorf("expected PRIVATE-TOKEN=mytoken, got %s", headers["PRIVATE-TOKEN"])
	}
	headers = p.AuthHeaders("othertoken")
	if headers["PRIVATE-TOKEN"] != "othertoken" {
		t.Errorf("expected PRIVATE-TOKEN=othertoken, got %s", headers["PRIVATE-TOKEN"])
	}
}

func TestParseWebhookEvent_Issue(t *testing.T) {
	p := New("gitlab.com", "token")
	payload := map[string]interface{}{
		"object_kind": "issue",
		"object_attributes": map[string]interface{}{
			"action":      "open",
			"iid":         float64(42),
			"title":       "Fix login bug",
			"description": "Login button doesn't work",
			"url":         "https://gitlab.com/group/project/issues/42",
		},
		"project": map[string]interface{}{
			"id":                  float64(1),
			"path_with_namespace": "group/project",
		},
		"labels": []interface{}{},
	}
	headers := map[string]string{"X-Gitlab-Event": "Issue Hook"}
	event := p.ParseWebhookEvent(payload, headers)
	if event == nil {
		t.Fatal("expected non-nil event")
	}
	if event.Type != "issue" {
		t.Errorf("expected type=issue, got %s", event.Type)
	}
	if event.Title != "Fix login bug" {
		t.Errorf("expected title=Fix login bug, got %s", event.Title)
	}
}

func TestParseWebhookEvent_MR(t *testing.T) {
	p := New("gitlab.com", "token")
	payload := map[string]interface{}{
		"object_attributes": map[string]interface{}{
			"action":        "merge",
			"iid":           float64(5),
			"title":         "Merge feature branch",
			"url":           "https://gitlab.com/group/project/-/merge_requests/5",
			"state":         "merged",
			"source_branch": "feature/PROJ-123-fix",
			"target_branch": "main",
		},
		"project": map[string]interface{}{
			"id":                  float64(1),
			"path_with_namespace": "group/project",
		},
	}
	headers := map[string]string{"X-Gitlab-Event": "Merge Request Hook"}
	event := p.ParseWebhookEvent(payload, headers)
	if event == nil {
		t.Fatal("expected non-nil event")
	}
	if event.Type != "merge_request" {
		t.Errorf("expected type=merge_request, got %s", event.Type)
	}
	if event.SourceBranch != "feature/PROJ-123-fix" {
		t.Errorf("expected source_branch, got %s", event.SourceBranch)
	}
}

func TestGetBranchListURL(t *testing.T) {
	p := New("gitlab.example.com", "token")
	url := p.GetBranchListURL("group/project")
	expected := "https://gitlab.example.com/group/project/-/branches"
	if url != expected {
		t.Errorf("expected %s, got %s", expected, url)
	}
}