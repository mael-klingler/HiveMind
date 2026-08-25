package mnesis

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/maelklingler/hivemind/internal/llm/provider"
)

type AgentSociety struct {
	Agents   map[string]Agent
	Provider provider.Provider
	Topic    string
	Router   *Router
}

func NewAgentSociety(llm provider.Provider, topic string, agentSet []string) *AgentSociety {
	agents := CreateAgentSet(agentSet)
	metas := make(map[string]AgentMeta)
	for name, a := range agents {
		p := a.Persona()
		metas[name] = AgentMeta{
			Name: name, Role: a.Role(),
			Topics: p.Topics, Strengths: p.Strengths,
		}
	}
	return &AgentSociety{
		Agents:   agents,
		Provider: llm,
		Topic:    topic,
		Router:   NewRouter(llm, metas, topic),
	}
}

type RunOptions struct {
	MaxTurns          int
	ConvergenceTurns  int
	MaxDurationSeconds int
}

func DefaultRunOptions() RunOptions {
	return RunOptions{MaxTurns: 30, ConvergenceTurns: 3, MaxDurationSeconds: 300}
}

func (s *AgentSociety) Run(ctx context.Context, goal string, opts RunOptions, onEvent func(SocietyEvent)) (*SocietyResult, error) {
	if opts.MaxTurns == 0 {
		opts = DefaultRunOptions()
	}
	sessionID := uuid.NewString()
	startTime := time.Now()
	board := NewBoard(goal)
	board.Add(fmt.Sprintf("Goal: %s", goal), "system", WithPin())
	activated := []string{}
	turnsWithoutNew := 0
	prevCount := board.ItemCount()

	if onEvent != nil {
		onEvent(SocietyEvent{Type: "society_start", Data: map[string]interface{}{
			"session_id": sessionID, "goal": goal, "agents": agentNames(s.Agents),
		}, Timestamp: time.Now()})
	}

	var lastDecision *RouterDecision
	for turn := 1; turn <= opts.MaxTurns; turn++ {
		board.AdvanceTurn()
		if opts.MaxDurationSeconds > 0 && time.Since(startTime).Seconds() > float64(opts.MaxDurationSeconds) {
			if onEvent != nil {
				onEvent(SocietyEvent{Type: "time_limit", Data: map[string]interface{}{"elapsed": time.Since(startTime).Seconds()}, Turn: turn})
			}
			break
		}
		decision, err := s.Router.Decide(ctx, board, goal)
		if err != nil {
			lastDecision = &RouterDecision{ShouldContinue: false, ConvergenceNote: err.Error()}
			break
		}
		lastDecision = decision
		if onEvent != nil {
			onEvent(SocietyEvent{Type: "router_decision", Data: map[string]interface{}{
				"next_agents": decision.NextAgents, "reasoning": decision.Reasoning,
				"should_continue": decision.ShouldContinue,
			}, Turn: turn})
		}
		if !decision.ShouldContinue {
			if onEvent != nil {
				onEvent(SocietyEvent{Type: "convergence", Data: map[string]interface{}{"reason": decision.ConvergenceNote}, Turn: turn})
			}
			break
		}
		for _, name := range decision.NextAgents {
			agent, ok := s.Agents[name]
			if !ok {
				continue
			}
			if onEvent != nil {
				onEvent(SocietyEvent{Type: "agent_start", Data: map[string]interface{}{"agent": name, "role": agent.Role()}, Turn: turn})
			}
			output, err := agent.Act(ctx, board, goal, s.Provider)
			if err != nil {
				continue
			}
			activated = append(activated, name)
			s.Router.RecordActivation(name)
			if onEvent != nil {
				onEvent(SocietyEvent{Type: "agent_output", Data: map[string]interface{}{
					"agent": name, "content": output.Content, "confidence": output.Confidence,
					"items_added": output.BoardItemsAdded, "has_more_to_say": output.HasMoreToSay,
				}, Turn: turn})
			}
		}
		newItems := board.ItemCount() - prevCount
		if newItems == 0 {
			turnsWithoutNew++
		} else {
			turnsWithoutNew = 0
		}
		prevCount = board.ItemCount()
		if onEvent != nil {
			onEvent(SocietyEvent{Type: "turn_complete", Data: map[string]interface{}{
				"turn": turn, "new_items": newItems, "total_items": board.ItemCount(),
				"turns_without_new": turnsWithoutNew,
			}, Turn: turn})
		}
		if turnsWithoutNew >= opts.ConvergenceTurns {
			if onEvent != nil {
				onEvent(SocietyEvent{Type: "convergence", Data: map[string]interface{}{"reason": fmt.Sprintf("No new items for %d turns", opts.ConvergenceTurns)}, Turn: turn})
			}
			break
		}
	}

	plenary := s.runPlenary(ctx, board, goal, onEvent)

	if onEvent != nil {
		onEvent(SocietyEvent{Type: "synthesis_start", Turn: board.Turn})
	}
	synthesisPrompt := BuildSynthesisPrompt(board, goal, plenary)
	resp, err := s.Provider.Complete(ctx, []provider.ChatMessage{{Role: "user", Content: synthesisPrompt}}, "")
	var synthesis SynthesisResult
	if err != nil {
		synthesis = SynthesisResult{RawResponse: err.Error()}
	} else {
		syn, _ := ParseSynthesis(resp.Content)
		if syn != nil {
			synthesis = *syn
		}
	}
	if onEvent != nil {
		onEvent(SocietyEvent{Type: "synthesis_done", Data: map[string]interface{}{"synthesis": synthesis}, Turn: board.Turn})
		onEvent(SocietyEvent{Type: "society_done", Data: map[string]interface{}{"session_id": sessionID, "turns": board.Turn}, Turn: board.Turn})
	}

	converged := turnsWithoutNew >= opts.ConvergenceTurns
	if lastDecision != nil && !lastDecision.ShouldContinue {
		converged = true
	}
	convergenceReason := fmt.Sprintf("No new items for %d turns", opts.ConvergenceTurns)
	if lastDecision != nil && !lastDecision.ShouldContinue {
		convergenceReason = "Router: " + lastDecision.ConvergenceNote
	}
	completedAt := time.Now()
	return &SocietyResult{
		SessionID:        sessionID,
		Goal:             goal,
		Turns:            board.Turn,
		AgentsActivated:  activated,
		FinalThesis:      synthesis.ExecutiveSummary,
		Synthesis:        synthesis,
		Converged:        converged,
		ConvergenceReason: convergenceReason,
		StartedAt:        startTime,
		CompletedAt:      &completedAt,
		Plenary:          plenary,
	}, nil
}

func (s *AgentSociety) runPlenary(ctx context.Context, board *SharedBoard, goal string, onEvent func(SocietyEvent)) *PlenaryResult {
	if len(s.Agents) == 0 {
		return &PlenaryResult{}
	}
	var candidates []*BoardItem
	for _, item := range board.Items {
		if item.Status == StatusOpen || item.Status == StatusPinned {
			candidates = append(candidates, item)
		}
	}
	if len(candidates) == 0 {
		return &PlenaryResult{}
	}
	var topItems []*BoardItem
	for i, item := range candidates {
		if i >= 3 {
			break
		}
		topItems = append(topItems, item)
	}
	var topItemsStr []string
	for _, item := range topItems {
		topItemsStr = append(topItemsStr, fmt.Sprintf("[%s] (%s, conf=%.2f) %s", item.ID, item.Author, item.Confidence, summary(item.Content, 400)))
	}
	topItemsText := strings.Join(topItemsStr, "\n\n")

	if onEvent != nil {
		onEvent(SocietyEvent{Type: "plenary_start", Data: map[string]interface{}{
			"top_item_ids": topItems, "agents": agentNames(s.Agents),
		}, Turn: board.Turn})
	}

	var votes []PlenaryVote
	for name, agent := range s.Agents {
		prompt := BuildPlenaryPrompt(agent.Persona().Name, agent.Role(), topItemsText, goal)
		resp, err := s.Provider.Complete(ctx, []provider.ChatMessage{{Role: "user", Content: prompt}}, "")
		if err != nil {
			votes = append(votes, PlenaryVote{Agent: name, Role: agent.Role(), Position: "abstain"})
			continue
		}
		vote, _ := ParsePlenaryVote(resp.Content)
		vote.Agent = name
		vote.Role = agent.Role()
		votes = append(votes, *vote)
		if onEvent != nil {
			onEvent(SocietyEvent{Type: "plenary_vote", Data: map[string]interface{}{
				"agent": name, "position": vote.Position, "reasoning": vote.Reasoning,
			}, Turn: board.Turn})
		}
	}

	dissensItems := []string{}
	for _, v := range votes {
		if v.Position == "object" && v.TargetItemID != "" {
			dissensItems = append(dissensItems, v.TargetItemID)
		}
	}
	support := 0
	objections := 0
	abstentions := 0
	for _, v := range votes {
		switch v.Position {
		case "support":
			support++
		case "object":
			objections++
		case "abstain":
			abstentions++
		}
	}
	result := &PlenaryResult{
		Votes: votes, Support: support, Objections: objections,
		Abstentions: abstentions, DissensItems: dissensItems, Rounds: 1,
	}
	if onEvent != nil {
		onEvent(SocietyEvent{Type: "plenary_done", Data: map[string]interface{}{
			"support": support, "objections": objections, "abstentions": abstentions,
		}, Turn: board.Turn})
	}
	return result
}

func agentNames(agents map[string]Agent) []string {
	names := make([]string, 0, len(agents))
	for n := range agents {
		names = append(names, n)
	}
	return names
}