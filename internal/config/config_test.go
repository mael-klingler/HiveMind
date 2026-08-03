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

import "testing"

func TestLoadDefaults(t *testing.T) {
	cfg := Load()
	if cfg.Port != "8080" {
		t.Errorf("expected Port=8080, got %s", cfg.Port)
	}
	if cfg.AgentNamespace != "hivemind" {
		t.Errorf("expected AgentNamespace=hivemind, got %s", cfg.AgentNamespace)
	}
	if cfg.AgentMaxRetries != 3 {
		t.Errorf("expected AgentMaxRetries=3, got %d", cfg.AgentMaxRetries)
	}
	if cfg.VCSProvider != "gitlab" {
		t.Errorf("expected VCSProvider=gitlab, got %s", cfg.VCSProvider)
	}
	if cfg.DryRun != false {
		t.Errorf("expected DryRun=false, got %v", cfg.DryRun)
	}
}

func TestOrchestrationURL(t *testing.T) {
	cfg := &Config{AgentNamespace: "hivemind"}
	url := cfg.OrchestrationURL()
	expected := "http://orchestrator.hivemind.svc.cluster.local:8080"
	if url != expected {
		t.Errorf("expected %s, got %s", expected, url)
	}
}

func TestGetEnvBool(t *testing.T) {
	tests := []struct {
		key      string
		val      string
		expected bool
	}{
		{"TEST_BOOL_1", "true", true},
		{"TEST_BOOL_2", "1", true},
		{"TEST_BOOL_3", "yes", true},
		{"TEST_BOOL_4", "false", false},
		{"TEST_BOOL_5", "0", false},
	}
	for _, tt := range tests {
		t.Setenv(tt.key, tt.val)
		result := getEnvBool(tt.key, false)
		if result != tt.expected {
			t.Errorf("getEnvBool(%s) = %v, expected %v", tt.key, result, tt.expected)
		}
	}
}

func TestGetEnvInt(t *testing.T) {
	t.Setenv("TEST_INT", "42")
	result := getEnvInt("TEST_INT", 0)
	if result != 42 {
		t.Errorf("expected 42, got %d", result)
	}
	result = getEnvInt("MISSING_INT", 99)
	if result != 99 {
		t.Errorf("expected fallback 99, got %d", result)
	}
}

func TestGetEnvSlice(t *testing.T) {
	t.Setenv("TEST_SLICE", "a,b,c")
	result := getEnvSlice("TEST_SLICE", nil)
	if len(result) != 3 || result[0] != "a" || result[2] != "c" {
		t.Errorf("expected [a,b,c], got %v", result)
	}
	result = getEnvSlice("MISSING_SLICE", []string{"default"})
	if len(result) != 1 || result[0] != "default" {
		t.Errorf("expected [default], got %v", result)
	}
}