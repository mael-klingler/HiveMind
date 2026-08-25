package mnesis

import "strings"

var topicKeywords = map[string][]string{
	"tech": {"software", "system", "architektur", "code", "api", "datenbank", "skalierbar", "leistung", "infrastruktur", "engineering", "technik", "algorithmus", "ki", "machine learning", "cloud", "deployment", "protokoll", "schnittstelle", "implementierung"},
	"recht": {"recht", "gesetz", "verordnung", "compliance", "haftung", "vertrag", "urheberrecht", "patent", "dsgvo", "datenschutz", "regulierung", "gericht", "klage", "auflage", "richtlinie"},
	"ethik": {"ethik", "moral", "verantwortung", "werte", "fairness", "gerechtigkeit", "pflicht", "verantwortlich", "interessenabwägung"},
	"forschung": {"forschung", "wissenschaft", "studie", "evidenz", "methode", "hypothes", "experiment", "daten", "empirisch", "korrelation", "kausalität", "replikation", "literatur"},
	"markt": {"markt", "ökonom", "preis", "nachfrage", "angebot", "anreiz", "kosten", "nutzen", "geschäftsmodell", "wettbewerb", "monopol", "rendite", "investition"},
	"langzeit": {"langfrist", "szenari", "zukunft", "pfadabhäng", "folge", "generation", "jahrzehnt", "trend", "prognose", "strategie", "strategisch", "plan"},
	"gesellschaft": {"gesellschaft", "sozial", "macht", "kultur", "demokratie", "politik", "gemeinshaft", "identität", "ungleichheit", "norm", "institution"},
	"philosophie": {"philosoph", "begriff", "logik", "wahrheit", "erkenntnis", "metaphysik", "ontolog", "epistemolog", "sprache", "bewusstsein", "freiheit"},
	"geschichte": {"geschichte", "historisch", "vergangen", "epoche", "revolution", "krieg", "zivilisation", "tradition", "erbe"},
	"default": {},
}

var topicAgentSets = map[string][]string{
	"tech":        {"engineer", "analytical", "red_team", "quantifier", "scientist", "strategist"},
	"recht":       {"analytical", "red_team", "scientist", "strategist"},
	"ethik":       {"red_team", "analytical", "scientist", "strategist"},
	"forschung":   {"scientist", "analytical", "quantifier", "red_team", "strategist"},
	"markt":       {"quantifier", "analytical", "strategist", "red_team", "scientist"},
	"langzeit":    {"strategist", "analytical", "red_team", "quantifier", "scientist"},
	"gesellschaft": {"analytical", "red_team", "scientist", "strategist"},
	"philosophie": {"analytical", "red_team", "scientist", "strategist"},
	"geschichte":  {"analytical", "red_team", "scientist", "strategist"},
	"default":     {"engineer", "analytical", "red_team", "scientist", "quantifier"},
}

type TopicRegistry struct {
	keywords  map[string][]string
	agentSets map[string][]string
}

func NewTopicRegistry() *TopicRegistry {
	return &TopicRegistry{keywords: topicKeywords, agentSets: topicAgentSets}
}

func (tr *TopicRegistry) Match(goal string) TopicMatch {
	goalLower := strings.ToLower(goal)
	scores := make(map[string]int)
	for topic, kws := range tr.keywords {
		score := 0
		for _, kw := range kws {
			if strings.Contains(goalLower, kw) {
				score++
			}
		}
		scores[topic] = score
	}
	bestTopic := "default"
	bestScore := 0
	for topic, score := range scores {
		if score > bestScore {
			bestScore = score
			bestTopic = topic
		}
	}
	confidence := 0.0
	if bestScore > 0 {
		confidence = float64(bestScore) / float64(len(scores))
	}
	return TopicMatch{Topic: bestTopic, Confidence: confidence, Scores: scores}
}

func (tr *TopicRegistry) AgentSetFor(topic string) []string {
	if set, ok := tr.agentSets[topic]; ok {
		return set
	}
	return tr.agentSets["default"]
}