package mnesis

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/maelklingler/hivemind/internal/llm/provider"
)

type Agent interface {
	Name() string
	Role() string
	Persona() *Persona
	Act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider) (*AgentOutput, error)
	TurnCount() int
}

type BaseAgent struct {
	name     string
	role     string
	persona  Persona
	turnCount int
}

func (a *BaseAgent) Name() string    { return a.name }
func (a *BaseAgent) Role() string    { return a.role }
func (a *BaseAgent) Persona() *Persona { return &a.persona }
func (a *BaseAgent) TurnCount() int { return a.turnCount }

func (a *BaseAgent) systemPrompt() string {
	p := a.persona
	lines := []string{
		fmt.Sprintf("Du bist %s (%s).", p.Name, p.Role),
		fmt.Sprintf("Ton: %s.", p.Tone),
		fmt.Sprintf("Denkstil: %s.", p.ThinkingStyle),
		"",
		"Du bist Teil einer Denk-Gesellschaft. Mehrere Agenten arbeiten zusammen",
		"an einem Shared Board. Du siehst einen FOKUSSIERTEN Ausschnitt des Boards.",
		"",
		"Deine Aufgabe in diesem Turn:",
		a.taskDescription(),
		"",
		"WICHTIGE REGELN:",
		"- Lies das Board sorgfältig. Reagiere auf das, was andere geschrieben haben.",
		"- Füge NEUE Einsichten hinzu — wiederhole nicht, was schon da steht.",
		"- Wenn du nichts Neues beizutragen hast, setze has_more_to_say=false.",
		"- Du kannst Items streichen (strike), anheften (pin) oder kommentieren.",
		"- Denke kritisch: widersprich, wenn nötig. Baue auf anderen auf, wenn möglich.",
		"",
	}
	if len(p.FavoriteQuestions) > 0 {
		lines = append(lines, "Stelle dir immer wieder diese Fragen:")
		for _, q := range p.FavoriteQuestions {
			lines = append(lines, fmt.Sprintf("- %s", q))
		}
		lines = append(lines, "")
	}
	if len(p.Catchphrases) > 0 {
		lines = append(lines, "Dein Stil:")
		for _, c := range p.Catchphrases {
			lines = append(lines, fmt.Sprintf("- %s", c))
		}
		lines = append(lines, "")
	}
	return strings.Join(lines, "\n")
}

func (a *BaseAgent) outputSchemaPrompt() string {
	return `OUTPUT-FORMAT (strikt, JSON-Block):
` + "```json" + `
{
  "content": "<dein Beitrag — PRÄGNANT, 80-300 Wörter. Nur was neu & relevant ist.>",
  "confidence": 0.0-1.0,
  "board_items_added": ["<neue Einsicht 1>", "<neue Einsicht 2>"],
  "board_items_struck": ["<item_id zum Streichen>"],
  "board_items_pinned": ["<item_id zum Anheften>"],
  "board_comments": [
    {"reply_to": "<item_id>", "content": "<Kommentar>"}
  ],
  "has_more_to_say": true,
  "open_questions": ["<offene Frage 1>"]
}
` + "```"
}

func (a *BaseAgent) taskDescription() string { return "Trage deine Perspektive bei." }

func (a *BaseAgent) act(ctx context.Context, board *SharedBoard, goal string, llm provider.Provider, taskDesc string) (*AgentOutput, error) {
	a.turnCount++
	boardView := board.RenderForAgent(a.name, 15)
	sys := a.systemPrompt() + "\n\n" + a.outputSchemaPrompt()
	user := fmt.Sprintf("GOAL: %s\n\nBOARD:\n%s\n\nWas ist dein Beitrag in diesem Turn?", goal, boardView)
	messages := []provider.ChatMessage{
		{Role: "system", Content: sys},
		{Role: "user", Content: user},
	}
	resp, err := llm.Complete(ctx, messages, "")
	if err != nil {
		return &AgentOutput{Agent: a.name, Role: a.role, Content: "", HasMoreToSay: false, RawResponse: err.Error()}, err
	}
	output := parseAgentOutput(resp.Content, a.name, a.role)
	for _, id := range output.BoardItemsStruck {
		board.Strike(id)
	}
	for _, id := range output.BoardItemsPinned {
		board.Pin(id)
	}
	for _, c := range output.BoardComments {
		if c.ReplyTo != "" && c.Content != "" {
			board.Comment(c.ReplyTo, c.Content, a.name)
		}
	}
	for _, content := range output.BoardItemsAdded {
		board.Add(content, a.name, WithConfidence(output.Confidence))
	}
	return output, nil
}

func parseAgentOutput(content, agentName, role string) *AgentOutput {
	data := extractJSON(content)
	out := &AgentOutput{
		Agent:       agentName,
		Role:        role,
		HasMoreToSay: true,
		RawResponse:  content,
	}
	if data == nil {
		out.Content = content
		out.Confidence = 0.5
		return out
	}
	if v, ok := data["content"].(string); ok {
		out.Content = v
	}
	if v, ok := data["confidence"].(float64); ok {
		out.Confidence = v
	}
	if arr, ok := data["board_items_added"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				out.BoardItemsAdded = append(out.BoardItemsAdded, s)
			}
		}
	}
	if arr, ok := data["board_items_struck"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				out.BoardItemsStruck = append(out.BoardItemsStruck, s)
			}
		}
	}
	if arr, ok := data["board_items_pinned"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				out.BoardItemsPinned = append(out.BoardItemsPinned, s)
			}
		}
	}
	if arr, ok := data["board_comments"].([]interface{}); ok {
		for _, v := range arr {
			if m, ok := v.(map[string]interface{}); ok {
				reply, _ := m["reply_to"].(string)
				cont, _ := m["content"].(string)
				if reply != "" && cont != "" {
					out.BoardComments = append(out.BoardComments, BoardComment{ReplyTo: reply, Content: cont})
				}
			}
		}
	}
	if v, ok := data["has_more_to_say"].(bool); ok {
		out.HasMoreToSay = v
	}
	if arr, ok := data["open_questions"].([]interface{}); ok {
		for _, v := range arr {
			if s, ok := v.(string); ok {
				out.OpenQuestions = append(out.OpenQuestions, s)
			}
		}
	}
	return out
}

func extractJSON(content string) map[string]interface{} {
	start := strings.Index(content, "{")
	if start < 0 {
		return nil
	}
	depth := 0
	for i := start; i < len(content); i++ {
		if content[i] == '{' {
			depth++
		}
		if content[i] == '}' {
			depth--
			if depth == 0 {
				var data map[string]interface{}
				if err := json.Unmarshal([]byte(content[start:i+1]), &data); err == nil {
					return data
				}
				return nil
			}
		}
	}
	return nil
}

var _ = time.Now