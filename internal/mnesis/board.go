package mnesis

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
)

type SharedBoard struct {
	Goal       string
	Items      map[string]*BoardItem
	Turn       int
	CreatedAt  time.Time
	LastUpdated time.Time
	mu         sync.RWMutex
}

func NewBoard(goal string) *SharedBoard {
	now := time.Now()
	return &SharedBoard{
		Goal:       goal,
		Items:      make(map[string]*BoardItem),
		CreatedAt:  now,
		LastUpdated: now,
	}
}

func (b *SharedBoard) Add(content, author string, opts ...AddOption) *BoardItem {
	o := &addOpts{confidence: 0.5}
	for _, fn := range opts {
		fn(o)
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	status := StatusOpen
	if o.pin {
		status = StatusPinned
	}
	item := &BoardItem{
		ID:        fmt.Sprintf("item_%s", uuid.NewString()[:8]),
		Content:   content,
		Status:    status,
		Author:    author,
		ReplyTo:   o.replyTo,
		Tags:      o.tags,
		Confidence: o.confidence,
		TurnNumber: b.Turn,
		CreatedAt:  time.Now(),
	}
	b.Items[item.ID] = item
	b.LastUpdated = time.Now()
	return item
}

type addOpts struct {
	pin        bool
	replyTo    string
	tags       []string
	confidence float64
}

type AddOption func(*addOpts)

func WithPin() AddOption            { return func(o *addOpts) { o.pin = true } }
func WithReplyTo(id string) AddOption { return func(o *addOpts) { o.replyTo = id } }
func WithTags(tags []string) AddOption { return func(o *addOpts) { o.tags = tags } }
func WithConfidence(c float64) AddOption { return func(o *addOpts) { o.confidence = c } }

func (b *SharedBoard) Pin(id string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if item, ok := b.Items[id]; ok {
		item.Status = StatusPinned
		b.LastUpdated = time.Now()
	}
}

func (b *SharedBoard) Strike(id string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if item, ok := b.Items[id]; ok {
		item.Status = StatusStruck
		b.LastUpdated = time.Now()
	}
}

func (b *SharedBoard) Resolve(id string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if item, ok := b.Items[id]; ok {
		item.Status = StatusResolved
		b.LastUpdated = time.Now()
	}
}

func (b *SharedBoard) Comment(id, text, author string) *BoardItem {
	return b.Add(text, author, WithReplyTo(id))
}

func (b *SharedBoard) Query(status BoardItemStatus, author string, sinceTurn int, limit int) []*BoardItem {
	b.mu.RLock()
	defer b.mu.RUnlock()
	var results []*BoardItem
	for _, item := range b.Items {
		if status != "" && item.Status != status {
			continue
		}
		if author != "" && item.Author != author {
			continue
		}
		if sinceTurn > 0 && item.TurnNumber < sinceTurn {
			continue
		}
		results = append(results, item)
	}
	if limit > 0 && len(results) > limit {
		results = results[:limit]
	}
	return results
}

func (b *SharedBoard) QueryRelevant(queryText string, limit int, excludeAuthor string) []*BoardItem {
	b.mu.RLock()
	defer b.mu.RUnlock()
	queryLower := strings.ToLower(queryText)
	queryWords := strings.Fields(queryLower)
	wordSet := make(map[string]bool)
	for _, w := range queryWords {
		wordSet[w] = true
	}
	type scored struct {
		score float64
		item  *BoardItem
	}
	var scoredItems []scored
	for _, item := range b.Items {
		if item.Status == StatusStruck {
			continue
		}
		if excludeAuthor != "" && item.Author == excludeAuthor {
			continue
		}
		contentLower := strings.ToLower(item.Content)
		score := 0.0
		for w := range wordSet {
			if len(w) > 2 && strings.Contains(contentLower, w) {
				score++
			}
		}
		if score > 0 {
			scoredItems = append(scoredItems, scored{score, item})
		}
	}
	if limit > 0 && len(scoredItems) > limit {
		scoredItems = scoredItems[:limit]
	}
	results := make([]*BoardItem, len(scoredItems))
	for i, s := range scoredItems {
		results[i] = s.item
	}
	return results
}

func (b *SharedBoard) Recent(n int) []*BoardItem {
	b.mu.RLock()
	defer b.mu.RUnlock()
	var items []*BoardItem
	for _, item := range b.Items {
		items = append(items, item)
	}
	if len(items) > n {
		items = items[:n]
	}
	return items
}

func (b *SharedBoard) AdvanceTurn() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.Turn++
	b.LastUpdated = time.Now()
	return b.Turn
}

func (b *SharedBoard) ItemCount() int {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return len(b.Items)
}

func (b *SharedBoard) RenderForAgent(agentName string, maxItems int) string {
	b.mu.RLock()
	defer b.mu.RUnlock()
	var lines []string
	lines = append(lines, fmt.Sprintf("# Shared Board — Goal: %s", b.Goal))
	lines = append(lines, fmt.Sprintf("Turn: %d", b.Turn))
	lines = append(lines, "")

	var pinned []*BoardItem
	for _, item := range b.Items {
		if item.Status == StatusPinned {
			pinned = append(pinned, item)
		}
	}
	if len(pinned) > 0 {
		lines = append(lines, "## PINNED (critical)")
		for i, item := range pinned {
			if i >= 5 {
				break
			}
			lines = append(lines, fmt.Sprintf("[%s] (%s, t%d) %s", item.ID, item.Author, item.TurnNumber, summary(item.Content, 200)))
		}
		lines = append(lines, "")
	}

	var myItems []*BoardItem
	for _, item := range b.Items {
		if item.Author == agentName && item.Status != StatusStruck {
			myItems = append(myItems, item)
		}
	}
	if len(myItems) > 0 {
		lines = append(lines, "## Your Previous Contributions")
		start := len(myItems) - 5
		if start < 0 {
			start = 0
		}
		for _, item := range myItems[start:] {
			lines = append(lines, fmt.Sprintf("[%s] (t%d) %s", item.ID, item.TurnNumber, summary(item.Content, 200)))
		}
		lines = append(lines, "")
	}

	relevant := b.queryRelevantLocked(agentName, maxItems, agentName)
	if len(relevant) > 0 {
		lines = append(lines, "## Relevant Contributions by Others")
		for i, item := range relevant {
			if i >= 10 {
				break
			}
			lines = append(lines, fmt.Sprintf("[%s] (%s, t%d) %s", item.ID, item.Author, item.TurnNumber, summary(item.Content, 200)))
		}
		lines = append(lines, "")
	}

	return strings.Join(lines, "\n")
}

func (b *SharedBoard) queryRelevantLocked(queryText string, limit int, excludeAuthor string) []*BoardItem {
	queryLower := strings.ToLower(queryText)
	queryWords := strings.Fields(queryLower)
	type scored struct {
		score float64
		item  *BoardItem
	}
	var scoredItems []scored
	for _, item := range b.Items {
		if item.Status == StatusStruck {
			continue
		}
		if excludeAuthor != "" && item.Author == excludeAuthor {
			continue
		}
		contentLower := strings.ToLower(item.Content)
		score := 0.0
		for _, w := range queryWords {
			if len(w) > 2 && strings.Contains(contentLower, w) {
				score++
			}
		}
		if score > 0 {
			scoredItems = append(scoredItems, scored{score, item})
		}
	}
	results := make([]*BoardItem, 0, len(scoredItems))
	for _, s := range scoredItems {
		results = append(results, s.item)
	}
	if limit > 0 && len(results) > limit {
		results = results[:limit]
	}
	return results
}

func (b *SharedBoard) RenderFull() string {
	b.mu.RLock()
	defer b.mu.RUnlock()
	var lines []string
	lines = append(lines, fmt.Sprintf("# Shared Board — Goal: %s", b.Goal))
	lines = append(lines, fmt.Sprintf("Total turns: %d", b.Turn))
	lines = append(lines, fmt.Sprintf("Total items: %d", len(b.Items)))
	lines = append(lines, "")
	for _, status := range []BoardItemStatus{StatusPinned, StatusOpen, StatusResolved, StatusStruck} {
		var items []*BoardItem
		for _, item := range b.Items {
			if item.Status == status {
				items = append(items, item)
			}
		}
		if len(items) == 0 {
			continue
		}
		lines = append(lines, fmt.Sprintf("## %s", strings.ToUpper(string(status))))
		for _, item := range items {
			lines = append(lines, fmt.Sprintf("[%s] (%s, t%d, conf=%.2f) %s", item.ID, item.Author, item.TurnNumber, item.Confidence, summary(item.Content, 300)))
		}
		lines = append(lines, "")
	}
	return strings.Join(lines, "\n")
}

func summary(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen] + "..."
}