package mnesis

import (
	"testing"
)

func TestSharedBoard_AddQueryPinStrike(t *testing.T) {
	b := NewBoard("test goal")
	item := b.Add("first insight", "engineer", WithConfidence(0.8))
	if item.ID == "" {
		t.Fatal("item should have ID")
	}
	if item.Status != StatusOpen {
		t.Fatalf("expected open, got %s", item.Status)
	}
	b.Pin(item.ID)
	if b.Items[item.ID].Status != StatusPinned {
		t.Fatal("pin failed")
	}
	b.Strike(item.ID)
	if b.Items[item.ID].Status != StatusStruck {
		t.Fatal("strike failed")
	}
}

func TestSharedBoard_QueryRelevant(t *testing.T) {
	b := NewBoard("test")
	b.Add("software architecture scaling", "engineer")
	b.Add("legal compliance check", "jurist")
	results := b.QueryRelevant("software architecture", 5, "")
	if len(results) == 0 {
		t.Fatal("expected relevant results")
	}
	if results[0].Author != "engineer" {
		t.Fatalf("expected engineer, got %s", results[0].Author)
	}
}

func TestSharedBoard_RenderForAgent(t *testing.T) {
	b := NewBoard("test goal")
	b.Add("Goal: test goal", "system", WithPin())
	b.Add("my insight", "engineer")
	b.Add("other insight", "red_team")
	rendered := b.RenderForAgent("engineer", 15)
	if rendered == "" {
		t.Fatal("rendered board should not be empty")
	}
}

func TestCRO_Classify(t *testing.T) {
	tests := []struct {
		goal string
		want TaskClassification
	}{
		{"Erfinde eine neue Idee für ein Usersystem", ClassExplorative},
		{"Analysiere wie die Datenbank funktioniert", ClassAnalytical},
		{"Welche Risiken gibt es bei diesem System?", ClassCritical},
	}
	for _, tt := range tests {
		got := Classify(tt.goal)
		if got != tt.want {
			t.Errorf("Classify(%q) = %s, want %s", tt.goal, got, tt.want)
		}
	}
}

func TestCRO_Decide(t *testing.T) {
	dec := CRODecide("Baue ein Software-System mit API und Datenbank")
	if dec.Classification != string(ClassExplorative) && dec.Classification != string(ClassAnalytical) {
		t.Fatalf("unexpected classification: %s", dec.Classification)
	}
	if dec.Topic != "tech" {
		t.Fatalf("expected tech topic, got %s", dec.Topic)
	}
	if len(dec.AgentSet) == 0 {
		t.Fatal("agent set should not be empty")
	}
}

func TestTopicRegistry_Match(t *testing.T) {
	tr := NewTopicRegistry()
	match := tr.Match("Baue ein Software-System mit API und Datenbank")
	if match.Topic != "tech" {
		t.Fatalf("expected tech, got %s", match.Topic)
	}
}

func TestSynthesis_Parse(t *testing.T) {
	content := `{"executive_summary":"test summary","core_arguments":["arg1","arg2"],"next_steps":["step1"],"confidence":0.8}`
	result, err := ParseSynthesis(content)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}
	if result.ExecutiveSummary != "test summary" {
		t.Fatalf("expected 'test summary', got %s", result.ExecutiveSummary)
	}
	if len(result.CoreArguments) != 2 {
		t.Fatalf("expected 2 args, got %d", len(result.CoreArguments))
	}
	if len(result.NextSteps) != 1 {
		t.Fatalf("expected 1 step, got %d", len(result.NextSteps))
	}
}

func TestParsePlenaryVote(t *testing.T) {
	content := `{"position":"support","confidence":0.9,"reasoning":"good plan","key_objection":""}`
	vote, err := ParsePlenaryVote(content)
	if err != nil {
		t.Fatalf("parse failed: %v", err)
	}
	if vote.Position != "support" {
		t.Fatalf("expected support, got %s", vote.Position)
	}
	if vote.Confidence != 0.9 {
		t.Fatalf("expected 0.9, got %f", vote.Confidence)
	}
}

func TestAgentRegistry_CreateAgentSet(t *testing.T) {
	agents := CreateAgentSet([]string{"engineer", "red_team", "analytical"})
	if len(agents) != 3 {
		t.Fatalf("expected 3 agents, got %d", len(agents))
	}
	if _, ok := agents["engineer"]; !ok {
		t.Fatal("engineer missing")
	}
	if _, ok := agents["red_team"]; !ok {
		t.Fatal("red_team missing")
	}
}

func TestRegisterPlugin(t *testing.T) {
	RegisterPlugin("custom", func() Agent {
		return NewEngineerAgent()
	})
	agents := CreateAgentSet([]string{"custom"})
	if len(agents) != 1 {
		t.Fatal("plugin agent not created")
	}
}

func TestReflector_Reflect(t *testing.T) {
	r := NewReflector()
	session := &SocietyResult{
		Turns: 2,
		AgentsActivated: []string{"engineer"},
		Plenary: &PlenaryResult{Support: 1, Votes: []PlenaryVote{{Position: "support"}}},
	}
	result := r.Reflect(nil, session)
	if len(result.ThinkingErrors) == 0 {
		t.Fatal("expected thinking errors for low turns + single agent")
	}
}