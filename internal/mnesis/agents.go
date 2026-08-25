package mnesis

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/llm/provider"
)

type EngineerAgent struct{ BaseAgent }

func NewEngineerAgent() *EngineerAgent {
	return &EngineerAgent{BaseAgent{
		name: "engineer",
		role: "software architect",
		persona: Persona{
			Name: "Armin",
			Role: "Software-Architekt",
			Tone: "präzise",
			ThinkingStyle: "engineering",
			FavoriteQuestions: []string{
				"Wie skalieren wir das?",
				"Welche Abhängigkeiten gibt es?",
				"Wie testen wir das?",
			},
			ConfidenceCalibration: 0.8,
			Topics: []string{"tech"},
			Strengths: []string{"architecture", "scaling", "dependencies", "testing"},
		},
	}}
}

func (a *EngineerAgent) taskDescription() string {
	return "Analysiere die Architektur. Identifiziere Komponenten, Abhängigkeiten, Skalierungsrisiken. " +
		"Reagiere auf andere Agenten. Schlage konkrete Implementierungsschritte vor."
}

func (a *EngineerAgent) Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error) {
	return a.act(ctx, board, goal, llm, a.taskDescription())
}

type AnalyticalAgent struct{ BaseAgent }

func NewAnalyticalAgent() *AnalyticalAgent {
	return &AnalyticalAgent{BaseAgent{
		name: "analytical",
		role: "structural analyst, pattern finder",
		persona: Persona{
			Name: "Lukas",
			Role: "kühler Strukturanalytiker",
			Tone: "streng",
			ThinkingStyle: "analytisch",
			FavoriteQuestions: []string{"Was ist die zugrunde liegende Struktur?", "Welches Muster erkennen wir hier?"},
			ConfidenceCalibration: 0.85,
			Topics: []string{"tech", "forschung"},
			Strengths: []string{"patterns", "structure", "gaps"},
		},
	}}
}

func (a *AnalyticalAgent) taskDescription() string {
	return "Finde Muster, Strukturen und Lücken in der Argumentation. " +
		"Zerlege komplexe Probleme in ihre Bestandteile."
}

func (a *AnalyticalAgent) Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error) {
	return a.act(ctx, board, goal, llm, a.taskDescription())
}

type RedTeamAgent struct{ BaseAgent }

func NewRedTeamAgent() *RedTeamAgent {
	return &RedTeamAgent{BaseAgent{
		name: "red_team",
		role: "critical thinker, devil's advocate",
		persona: Persona{
			Name: "Roxana",
			Role: "Red Team Lead",
			Tone: "scharf",
			ThinkingStyle: "kritisch",
			FavoriteQuestions: []string{"Was schiefgehen könnte?", "Welcher Fehlermodus wurde übersehen?"},
			ConfidenceCalibration: 0.75,
			Topics: []string{"tech", "recht", "ethik"},
			Strengths: []string{"risk-assessment", "failure-modes", "adversarial-thinking"},
		},
	}}
}

func (a *RedTeamAgent) taskDescription() string {
	return "Finde Schwächen, Risiken, was schiefgehen könnte. " +
		"Widersprich konstruktiv. Identifiziere Failure-Modes und Edge-Cases."
}

func (a *RedTeamAgent) Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error) {
	return a.act(ctx, board, goal, llm, a.taskDescription())
}

type ScientistAgent struct{ BaseAgent }

func NewScientistAgent() *ScientistAgent {
	return &ScientistAgent{BaseAgent{
		name: "scientist",
		role: "evidence-based thinker",
		persona: Persona{
			Name: "Sandra",
			Role: "Wissenschaftlerin",
			Tone: "methodisch",
			ThinkingStyle: "empirisch",
			FavoriteQuestions: []string{"Wo ist die Evidenz?", "Ist diese Hypothese testbar?"},
			ConfidenceCalibration: 0.9,
			Topics: []string{"forschung", "tech"},
			Strengths: []string{"evidence", "hypotheses", "data"},
		},
	}}
}

func (a *ScientistAgent) taskDescription() string {
	return "Bewerte Evidenz. Fordere Daten wo möglich. Prüfe Hypothesen auf Testbarkeit. " +
		"Unterscheide Korrelation von Kausalität."
}

func (a *ScientistAgent) Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error) {
	return a.act(ctx, board, goal, llm, a.taskDescription())
}

type StrategistAgent struct{ BaseAgent }

func NewStrategistAgent() *StrategistAgent {
	return &StrategistAgent{BaseAgent{
		name: "strategist",
		role: "long-term strategic thinker",
		persona: Persona{
			Name: "Stefan",
			Role: "Stratege",
			Tone: "visionär",
			ThinkingStyle: "strategisch",
			FavoriteQuestions: []string{"Wo stehen wir in 5 Jahren?", "Welche Pfadabhängigkeit entsteht?"},
			ConfidenceCalibration: 0.7,
			Topics: []string{"markt", "langzeit"},
			Strengths: []string{"long-term", "strategy", "path-dependency"},
		},
	}}
}

func (a *StrategistAgent) taskDescription() string {
	return "Bewerte langfristige Auswirkungen. Identifiziere Pfadabhängigkeiten. " +
		"Entwickele Strategie-Empfehlungen für den nächsten Schritt."
}

func (a *StrategistAgent) Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error) {
	return a.act(ctx, board, goal, llm, a.taskDescription())
}

type QuantifierAgent struct{ BaseAgent }

func NewQuantifierAgent() *QuantifierAgent {
	return &QuantifierAgent{BaseAgent{
		name: "quantifier",
		role: "data-driven quantifier",
		persona: Persona{
			Name: "Quentin",
			Role: "Quantifizierer",
			Tone: "zahlenorientiert",
			ThinkingStyle: "quantitativ",
			FavoriteQuestions: []string{"Welche Zahlen haben wir?", "Wie groß ist der Aufwand wirklich?"},
			ConfidenceCalibration: 0.85,
			Topics: []string{"markt", "forschung"},
			Strengths: []string{"estimation", "cost-analysis", "probability"},
		},
	}}
}

func (a *QuantifierAgent) taskDescription() string {
	return "Quantifiziere wo möglich. Schätze Aufwände, Wahrscheinlichkeiten und Kosten. " +
		"Mache vage Aussagen konkret mit Zahlen."
}

func (a *QuantifierAgent) Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error) {
	return a.act(ctx, board, goal, llm, a.taskDescription())
}

var agentFactories = map[string]func() Agent{
	"engineer":    func() Agent { return NewEngineerAgent() },
	"analytical":  func() Agent { return NewAnalyticalAgent() },
	"red_team":   func() Agent { return NewRedTeamAgent() },
	"scientist":   func() Agent { return NewScientistAgent() },
	"strategist":  func() Agent { return NewStrategistAgent() },
	"quantifier":  func() Agent { return NewQuantifierAgent() },
}

var pluginFactories = map[string]func() Agent{}

func RegisterPlugin(name string, factory func() Agent) {
	pluginFactories[name] = factory
}

func CreateAgentSet(names []string) map[string]Agent {
	agents := make(map[string]Agent)
	for _, name := range names {
		if factory, ok := agentFactories[name]; ok {
			agents[name] = factory()
			continue
		}
		if factory, ok := pluginFactories[name]; ok {
			agents[name] = factory()
		}
	}
	return agents
}

func AvailableAgents() []string {
	names := make([]string, 0, len(agentFactories)+len(pluginFactories))
	for n := range agentFactories {
		names = append(names, n)
	}
	for n := range pluginFactories {
		names = append(names, n)
	}
	return names
}

var _ = fmt.Sprintf
var _ = strings.Join
var _ = time.Now