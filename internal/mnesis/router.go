package mnesis

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"github.com/maelklingler/hivemind/internal/llm/provider"
)

type Router struct {
	AgentRegistry     map[string]string
	Provider          provider.Provider
	AgentMetas        map[string]AgentMeta
	Topic             string
	recentActivations []string
}

func NewRouter(provider provider.Provider, agentMetas map[string]AgentMeta, topic string) *Router {
	registry := make(map[string]string)
	for name, meta := range agentMetas {
		registry[name] = meta.Role
	}
	return &Router{
		AgentRegistry: registry,
		Provider:      provider,
		AgentMetas:    agentMetas,
		Topic:         topic,
	}
}

func (r *Router) RecordActivation(name string) {
	r.recentActivations = append(r.recentActivations, name)
	if len(r.recentActivations) > 10 {
		r.recentActivations = r.recentActivations[len(r.recentActivations)-10:]
	}
}

func (r *Router) Decide(ctx context.Context, board *SharedBoard, goal string) (*RouterDecision, error) {
	agentList := r.renderAgentRegistry()
	boardSummary := r.renderBoardSummary(board)
	recent := strings.Join(r.recentActivations, ", ")
	if recent == "" {
		recent = "(none yet)"
	}

	prompt := fmt.Sprintf(`Du bist der Router einer Denk-Gesellschaft. Mehrere spezialisierte Agenten
arbeiten zusammen an einem Shared Board. Deine Aufgabe: entscheide, WER als nächstes sprechen soll.

VERFÜGBARE AGENTEN (mit Themengebieten und Stärken):
%s

ZIEL-DOMÄNE: %s

BOARD-STAND (komprimiert):
%s

LETZTE AKTIVIERUNGEN (in Reihenfolge):
%s

ENTSCHEIDUNGSKRITERIEN:
1. Diversität: nicht denselben Agenten zweimal hintereinander
2. Relevanz: wer kann auf die neuesten Board-Items am besten reagieren?
3. Abdeckung: welche Perspektive fehlt noch?
4. Konflikt: gibt es ungelöste Widersprüche? → Red Team oder Analytisch
5. Tiefe: gibt es oberflächliche Ideen? → Historiker oder Quantor
6. Empathie: fehlt menschliche Perspektive? → Empath
7. Thematische Passung: welcher Agent hat thematisch relevantes Wissen?
8. Komplementarität: wähle Agenten, deren Stärken sich ergänzen.

OUTPUT-FORMAT (strikt, JSON-Block):
` + "```json" + `
{
  "next_agents": ["agent_name_1", "agent_name_2", "agent_name_3"],
  "reasoning": "<kurze Begründung, 1-2 Sätze>",
  "should_continue": true,
  "convergence_note": "<wenn should_continue=false: warum?>"
}
` + "```" + `

WICHTIG:
- Gib 1-3 Agenten zurück (nicht alle auf einmal)
- setze should_continue=false NUR wenn wirklich keine neuen Perspektiven mehr nötig sind
- priorisiere Agenten, die auf die NEUESTEN Board-Items reagieren können
- bei Themengebiet "%s" bevorzuge Agenten mit passenden topics`, agentList, r.Topic, boardSummary, recent, r.Topic)

	messages := []provider.ChatMessage{
		{Role: "user", Content: prompt},
	}
	resp, err := r.Provider.Complete(ctx, messages, "")
	if err != nil {
		return &RouterDecision{NextAgents: []string{}, ShouldContinue: false, ConvergenceNote: err.Error()}, err
	}
	return parseRouterDecision(resp.Content), nil
}

func (r *Router) renderAgentRegistry() string {
	var lines []string
	for name, meta := range r.AgentMetas {
		parts := []string{fmt.Sprintf("- %s: %s", name, meta.Role)}
		if len(meta.Topics) > 0 {
			parts = append(parts, fmt.Sprintf("topics: %s", strings.Join(meta.Topics, ", ")))
		}
		if len(meta.Strengths) > 0 {
			parts = append(parts, fmt.Sprintf("strengths: %s", strings.Join(meta.Strengths, ", ")))
		}
		lines = append(lines, strings.Join(parts, " | "))
	}
	return strings.Join(lines, "\n")
}

func (r *Router) renderBoardSummary(board *SharedBoard) string {
	items := board.Recent(10)
	if len(items) == 0 {
		return "(empty board)"
	}
	var lines []string
	for _, item := range items {
		lines = append(lines, fmt.Sprintf("[%s] (%s) %s", item.ID, item.Author, summary(item.Content, 150)))
	}
	return strings.Join(lines, "\n")
}

func parseRouterDecision(content string) *RouterDecision {
	data := extractJSON(content)
	dec := &RouterDecision{ShouldContinue: true}
	if data == nil {
		return dec
	}
	if arr, ok := data["next_agents"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				dec.NextAgents = append(dec.NextAgents, s)
			}
		}
	}
	if v, ok := data["reasoning"].(string); ok {
		dec.Reasoning = v
	}
	if v, ok := data["should_continue"].(bool); ok {
		dec.ShouldContinue = v
	}
	if v, ok := data["convergence_note"].(string); ok {
		dec.ConvergenceNote = v
	}
	return dec
}

var _ = json.Marshal