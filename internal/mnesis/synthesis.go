package mnesis

import (
	"fmt"
	"strings"
)

func BuildSynthesisPrompt(board *SharedBoard, goal string, plenary *PlenaryResult) string {
	boardFull := board.RenderFull()
	support := 0
	objections := 0
	abstentions := 0
	var votesStr string
	var dissensStr string
	if plenary != nil {
		support = plenary.Support
		objections = plenary.Objections
		abstentions = plenary.Abstentions
		var voteLines []string
		for _, v := range plenary.Votes {
			voteLines = append(voteLines, fmt.Sprintf("- %s (%s): %s (conf=%.2f) — %s", v.Agent, v.Role, v.Position, v.Confidence, v.Reasoning))
		}
		votesStr = strings.Join(voteLines, "\n")
		var dissensLines []string
		for _, id := range plenary.DissensItems {
			if item, ok := board.Items[id]; ok {
				dissensLines = append(dissensLines, fmt.Sprintf("- [%s] %s", id, summary(item.Content, 200)))
			}
		}
		dissensStr = strings.Join(dissensLines, "\n")
	}

	return fmt.Sprintf(`Du bist der Synthesizer. Die Denk-Gesellschaft hat ihre Diskussion
beendet und eine Plenar-Abstimmung durchgeführt. Deine Aufgabe: produziere die
FINALE THESE aus dem gesamten Board und den Plenar-Positionen.

GOAL: %s

=== VOLLSTÄNDIGES BOARD ===
%s

=== PLENAR-ABSTIMMUNG ===
Support: %d | Objektionen: %d | Enthaltungen: %d

Stimmen:
%s

Dissens-Items (mit Objektionen):
%s

AUFGABE:
1. Identifiziere die 5-10 wichtigsten Einsichten
2. Erkenne den roten Faden — wie haben sich Ideen entwickelt?
3. Integriere die Dissens-Positionen. Wenn Dissens unaufösbar ist, benenne ihn klar.
4. Formuliere eine prägnante, umsetzbare Finale These
5. Strukturiere als:
   - Executive Summary (3-5 Sätze)
   - Kern-Argumente (nummeriert)
   - Kontroverse Punkte
   - Offene Fragen
   - Nächste Schritte (was sollte als nächstes passieren?)

OUTPUT-FORMAT (strikt, JSON-Block):
` + "```json" + `
{
  "executive_summary": "...",
  "core_arguments": ["...", "..."],
  "controversies": ["...", "..."],
  "open_questions": ["...", "..."],
  "next_steps": ["...", "..."],
  "confidence": 0.0-1.0
}
` + "```", goal, boardFull, support, objections, abstentions, votesStr, dissensStr)
}

func ParseSynthesis(content string) (*SynthesisResult, error) {
	data := extractJSON(content)
	if data == nil {
		data = extractJSONLoose(content)
	}
	if data == nil {
		return &SynthesisResult{RawResponse: content, ExecutiveSummary: content}, fmt.Errorf("no JSON in synthesis response")
	}
	result := &SynthesisResult{RawResponse: content}
	if v, ok := data["executive_summary"].(string); ok {
		result.ExecutiveSummary = v
	}
	if arr, ok := data["core_arguments"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				result.CoreArguments = append(result.CoreArguments, s)
			}
		}
	}
	if arr, ok := data["controversies"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				result.Controversies = append(result.Controversies, s)
			}
		}
	}
	if arr, ok := data["open_questions"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				result.OpenQuestions = append(result.OpenQuestions, s)
			}
		}
	}
	if arr, ok := data["next_steps"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				result.NextSteps = append(result.NextSteps, s)
			}
		}
	}
	if v, ok := data["confidence"].(float64); ok {
		result.Confidence = v
	}
	return result, nil
}

func BuildPlenaryPrompt(agentName, agentRole, topItems, goal string) string {
	return fmt.Sprintf(`Du bist %s (%s) in der Plenar-Abstimmung der Denk-Gesellschaft.

Die Diskussion ist konvergiert. Hier sind die zentralen Thesen des Boards:

%s

GOAL: %s

AUFGABE:
Bewerte diese Thesen aus deiner Perspektive. Du kannst zustimmen, widersprechen
oder dich enthalten. Wenn du widersprichst, formuliere den EINEN wichtigsten
Einwand prägnant (1-2 Sätze).

OUTPUT-FORMAT (strikt, JSON-Block):
` + "```json" + `
{
  "position": "support" | "object" | "abstain",
  "confidence": 0.0-1.0,
  "reasoning": "<kurze Begründung, 2-4 Sätze>",
  "key_objection": "<wenn object: wichtigster Einwand. Sonst leer.>",
  "target_item_id": "<optional: item_id>"
}
` + "```", agentName, agentRole, topItems, goal)
}

func ParsePlenaryVote(content string) (*PlenaryVote, error) {
	data := extractJSON(content)
	if data == nil {
		return &PlenaryVote{Position: "abstain"}, fmt.Errorf("no JSON in plenary response")
	}
	vote := &PlenaryVote{Position: "abstain"}
	if v, ok := data["position"].(string); ok {
		v := strings.ToLower(v)
		if v == "support" || v == "object" || v == "abstain" {
			vote.Position = v
		}
	}
	if v, ok := data["confidence"].(float64); ok {
		vote.Confidence = v
	}
	if v, ok := data["reasoning"].(string); ok {
		vote.Reasoning = v
	}
	if v, ok := data["key_objection"].(string); ok {
		vote.KeyObjection = v
	}
	if v, ok := data["target_item_id"].(string); ok {
		vote.TargetItemID = v
	}
	return vote, nil
}