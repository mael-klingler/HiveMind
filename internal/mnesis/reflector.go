package mnesis

import (
	"context"
	"strings"
)

type Reflector struct{}

func NewReflector() *Reflector { return &Reflector{} }

func (r *Reflector) Reflect(ctx context.Context, session *SocietyResult) *ReflectionResult {
	result := &ReflectionResult{Confidence: 0.7}
	if session == nil {
		return result
	}
	if session.Turns < 5 {
		result.ThinkingErrors = append(result.ThinkingErrors, "premature_closure")
		result.Recommendations = append(result.Recommendations, "Mehr Runden erlauben bevor konvergiert wird")
	}
	if len(session.AgentsActivated) == 1 {
		result.ThinkingErrors = append(result.ThinkingErrors, "groupthink_risk")
		result.Recommendations = append(result.Recommendations, "Mehr Agenten aktivieren für Diversität")
	}
	if session.Plenary != nil && session.Plenary.Support == len(session.Plenary.Votes) && len(session.Plenary.Votes) > 0 {
		result.ThinkingErrors = append(result.ThinkingErrors, "confirmation_bias")
		result.Recommendations = append(result.Recommendations, "Red Team stärker einbinden")
	}
	if session.Plenary != nil && len(session.Plenary.DissensItems) > 0 {
		result.Recommendations = append(result.Recommendations, "Dissens-Items als Follow-up-Tickets behandeln")
	}
	if len(session.Synthesis.NextSteps) == 0 {
		result.ThinkingErrors = append(result.ThinkingErrors, "no_actionable_output")
		result.Recommendations = append(result.Recommendations, "Synthesis muss konkrete nächste Schritte liefern")
	}
	agentSet := strings.Join(session.AgentsActivated, ",")
	if !strings.Contains(agentSet, "red_team") {
		result.MissingPerspectives = append(result.MissingPerspectives, "red_team")
	}
	if !strings.Contains(agentSet, "scientist") {
		result.MissingPerspectives = append(result.MissingPerspectives, "scientist")
	}
	return result
}