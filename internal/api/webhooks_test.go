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

package api

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"testing"
	"time"
)

func TestExtractTicketIDFromBranch(t *testing.T) {
	tests := []struct {
		branch string
		want   string
	}{
		{"feature/PROJ-123-add-login", "PROJ-123"},
		{"fix/BUG-456-crash-on-startup", "BUG-456"},
		{"feature/TASK-789-refactor", "TASK-789"},
		{"feature/GL-42-update-deps", "GL-42"},
		{"feature/GH-99-ci-fix", "GH-99"},
		{"main", ""},
		{"develop", ""},
		{"feature/some-random-branch", ""},
	}
	for _, tt := range tests {
		got := extractTicketIDFromBranch(tt.branch)
		if got != tt.want {
			t.Errorf("extractTicketIDFromBranch(%q) = %q, want %q", tt.branch, got, tt.want)
		}
	}
}

func TestIsDuplicateWebhook(t *testing.T) {
	webhookDedup = make(map[string]time.Time)

	if isDuplicateWebhook("event-1") {
		t.Error("first call should not be duplicate")
	}
	if !isDuplicateWebhook("event-1") {
		t.Error("second call with same ID should be duplicate")
	}
	if isDuplicateWebhook("event-2") {
		t.Error("different ID should not be duplicate")
	}
}

func TestVerifyGitlabWebhook(t *testing.T) {
	secret := "mysecret"
	if !verifyGitlabWebhook([]byte("body"), "mysecret", secret) {
		t.Error("valid signature should pass")
	}
	if verifyGitlabWebhook([]byte("body"), "wrongsecret", secret) {
		t.Error("invalid signature should fail")
	}
}

func TestVerifyGitHubWebhook(t *testing.T) {
	body := []byte(`{"action":"opened"}`)
	secret := "mysecret"
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write(body)
	sig := "sha256=" + hex.EncodeToString(mac.Sum(nil))

	if !verifyGitHubWebhook(body, sig, secret) {
		t.Error("valid signature should pass")
	}
	if verifyGitHubWebhook(body, "sha256=wrong", secret) {
		t.Error("invalid signature should fail")
	}
}