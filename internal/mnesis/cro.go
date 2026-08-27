package mnesis

import "strings"

type TaskClassification string

const (
	ClassExplorative TaskClassification = "explorative"
	ClassAnalytical  TaskClassification = "analytical"
	ClassCritical    TaskClassification = "critical"
	ClassSynthetic   TaskClassification = "synthetic"
	ClassEthical     TaskClassification = "ethical"
	ClassUnknown     TaskClassification = "unknown"
)

var classificationKeywords = map[TaskClassification][]string{
	ClassExplorative: {"idee", "erfinde", "brainstorm", "kreativ", "was wäre wenn", "alternativen", "optionen", "möglichkeiten", "neue ansätze", "idea", "invent", "create", "design", "build", "baue", "erstelle", "entwickle"},
	ClassAnalytical:  {"analysiere", "verstehe", "warum", "wie funktioniert", "struktur", "vergleich", "unterschied", "zusammenhang", "analyze", "understand", "how does", "structure", "compare"},
	ClassCritical:    {"kritisch", "schlecht", "fehler", "risiken", "probleme", "schwächen", "hinterfragen", "prüfen", "zweifel", "critical", "bad", "error", "risk", "problem", "weakness", "question"},
	ClassSynthetic:   {"kombiniere", "synthese", "beste", "top", "ranking", "vergleiche ideen", "wähle", "combine", "synthesis", "best", "ranking", "choose", "decide"},
	ClassEthical:     {"ethik", "moral", "verantwortung", "werte", "schaden", "auswirkung", "langzeit", "fairness", "ethics", "moral", "responsibility", "values", "harm", "impact", "fairness"},
}

var classificationAgentSets = map[TaskClassification][]string{
	ClassExplorative: {"engineer", "analytical", "red_team", "scientist", "strategist"},
	ClassAnalytical:  {"analytical", "scientist", "quantifier", "red_team", "engineer"},
	ClassCritical:    {"red_team", "analytical", "scientist", "quantifier", "engineer"},
	ClassSynthetic:   {"engineer", "analytical", "scientist", "quantifier", "strategist"},
	ClassEthical:     {"red_team", "analytical", "scientist", "strategist", "engineer"},
	ClassUnknown:     {"engineer", "analytical", "red_team", "scientist", "quantifier"},
}

func Classify(goal string) TaskClassification {
	goalLower := strings.ToLower(goal)
	bestClass := ClassUnknown
	bestScore := 0
	for class, keywords := range classificationKeywords {
		score := 0
		for _, kw := range keywords {
			if strings.Contains(goalLower, kw) {
				score++
			}
		}
		if score > bestScore {
			bestScore = score
			bestClass = class
		}
	}
	return bestClass
}

func (c TaskClassification) AgentSet() []string {
	if set, ok := classificationAgentSets[c]; ok {
		return set
	}
	return classificationAgentSets[ClassUnknown]
}

func CRODecide(goal string) CRODecision {
	class := Classify(goal)
	topic := TopicMatch{Topic: "default"}.Topic
	if tr := NewTopicRegistry(); tr != nil {
		topic = tr.Match(goal).Topic
	}
	return CRODecision{
		Classification: string(class),
		Protocol:       "agent_society",
		AgentSet:       class.AgentSet(),
		Topic:         topic,
	}
}