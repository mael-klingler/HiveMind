package mnesis

import "time"

type Persona struct {
	Name                string
	Role                string
	Tone                string
	ThinkingStyle       string
	FavoriteQuestions   []string
	Catchphrases        []string
	ConfidenceCalibration float64
	Topics              []string
	Strengths           []string
}

type BoardItemStatus string

const (
	StatusOpen     BoardItemStatus = "open"
	StatusPinned   BoardItemStatus = "pinned"
	StatusStruck   BoardItemStatus = "struck"
	StatusResolved BoardItemStatus = "resolved"
)

type BoardItem struct {
	ID        string
	Content   string
	Status    BoardItemStatus
	Author    string
	ReplyTo   string
	Tags      []string
	Confidence float64
	TurnNumber int
	CreatedAt time.Time
}

type BoardComment struct {
	ReplyTo string
	Content string
}

type AgentOutput struct {
	Agent            string
	Role             string
	Content          string
	Confidence       float64
	BoardItemsAdded  []string
	BoardItemsStruck []string
	BoardItemsPinned []string
	BoardComments    []BoardComment
	HasMoreToSay     bool
	OpenQuestions    []string
	ThinkingTrace    string
	RawResponse      string
}

type PlenaryVote struct {
	Agent       string
	Role        string
	Position    string
	Confidence  float64
	Reasoning   string
	KeyObjection string
	TargetItemID string
}

type PlenaryResult struct {
	Votes       []PlenaryVote
	Support     int
	Objections  int
	Abstentions int
	DissensItems []string
	Rounds      int
}

type SynthesisResult struct {
	ExecutiveSummary string
	CoreArguments    []string
	Controversies    []string
	OpenQuestions    []string
	NextSteps        []string
	Confidence       float64
	RawResponse      string
}

type SocietyResult struct {
	SessionID        string
	Goal             string
	Turns            int
	AgentsActivated  []string
	FinalThesis      string
	Synthesis        SynthesisResult
	Converged        bool
	ConvergenceReason string
	StartedAt        time.Time
	CompletedAt      *time.Time
	Plenary          *PlenaryResult
}

type SocietyEvent struct {
	Type      string
	Data      map[string]interface{}
	Turn      int
	Timestamp time.Time
}

type RouterDecision struct {
	NextAgents     []string
	Reasoning      string
	ShouldContinue bool
	ConvergenceNote string
}

type AgentMeta struct {
	Name      string
	Role      string
	Topics    []string
	Strengths []string
}

type CRODecision struct {
	Classification string
	Protocol      string
	AgentSet      []string
	Topic         string
}

type TopicMatch struct {
	Topic      string
	Confidence float64
	Scores     map[string]int
}

type TopicStats struct {
	Topic        string
	Sessions     int
	SuccessRate  float64
	AvgTurns     *float64
	TopAgentSets []AgentSetStat
	BestAgentSet []string
}

type AgentSetStat struct {
	AgentSet   []string
	Count      int
	SuccessRate float64
}

type ReflectionResult struct {
	ThinkingErrors       []string
	MissingPerspectives   []string
	Recommendations      []string
	Confidence           float64
}

type PatternType string

const (
	PatternSuccessful  PatternType = "successful"
	PatternFailed      PatternType = "failed"
	PatternPreference   PatternType = "preference"
	PatternStyleNote    PatternType = "style_note"
)

type ProceduralPattern struct {
	PatternType PatternType
	Description string
	Context     string
	Metadata    map[string]interface{}
	CreatedAt   time.Time
}