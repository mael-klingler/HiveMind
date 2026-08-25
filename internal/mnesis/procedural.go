package mnesis

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type ProceduralMemory struct {
	pool *pgxpool.Pool
}

func NewProceduralMemory(pool *pgxpool.Pool) *ProceduralMemory {
	return &ProceduralMemory{pool: pool}
}

func (pm *ProceduralMemory) RecordPattern(ctx context.Context, patternType PatternType, description, context string, metadata map[string]interface{}) error {
	metaJSON, _ := json.Marshal(metadata)
	_, err := pm.pool.Exec(ctx, `
		INSERT INTO procedural_patterns (pattern_type, description, context, metadata, created_at)
		VALUES ($1, $2, $3, $4, NOW())`,
		string(patternType), description, context, string(metaJSON))
	return err
}

func (pm *ProceduralMemory) ListAll(ctx context.Context) ([]ProceduralPattern, error) {
	rows, err := pm.pool.Query(ctx, `SELECT pattern_type, description, context, metadata, created_at FROM procedural_patterns ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var patterns []ProceduralPattern
	for rows.Next() {
		var p ProceduralPattern
		var pt, meta string
		if err := rows.Scan(&pt, &p.Description, &p.Context, &meta, &p.CreatedAt); err != nil {
			return nil, err
		}
		p.PatternType = PatternType(pt)
		_ = json.Unmarshal([]byte(meta), &p.Metadata)
		patterns = append(patterns, p)
	}
	return patterns, rows.Err()
}

func (pm *ProceduralMemory) GetTopicStats(ctx context.Context, topic string) (*TopicStats, error) {
	patterns, err := pm.ListAll(ctx)
	if err != nil {
		return &TopicStats{Topic: topic, Sessions: 0}, nil
	}
	stats := &TopicStats{Topic: topic}
	agentSetCounts := make(map[string]int)
	agentSetSuccess := make(map[string]int)
	var totalSessions, successfulSessions int
	for _, p := range patterns {
		if p.Metadata == nil {
			continue
		}
		t, _ := p.Metadata["topic"].(string)
		if t != topic {
			continue
		}
		totalSessions++
		if p.PatternType == PatternSuccessful {
			successfulSessions++
		}
		if as, ok := p.Metadata["agent_set"].([]interface{}); ok {
			key := agentSetKey(as)
			agentSetCounts[key]++
			if p.PatternType == PatternSuccessful {
				agentSetSuccess[key]++
			}
		}
	}
	stats.Sessions = totalSessions
	if totalSessions > 0 {
		stats.SuccessRate = float64(successfulSessions) / float64(totalSessions)
	}
	for key, count := range agentSetCounts {
		stat := AgentSetStat{Count: count}
		stat.AgentSet = strings.Split(key, ",")
		if count > 0 {
			stat.SuccessRate = float64(agentSetSuccess[key]) / float64(count)
		}
		if count >= 2 {
			stats.TopAgentSets = append(stats.TopAgentSets, stat)
		}
	}
	if len(stats.TopAgentSets) > 0 {
		best := stats.TopAgentSets[0]
		for _, s := range stats.TopAgentSets {
			if s.SuccessRate > best.SuccessRate {
				best = s
			}
		}
		stats.BestAgentSet = best.AgentSet
	}
	return stats, nil
}

func agentSetKey(arr []interface{}) string {
	strs := make([]string, len(arr))
	for i, v := range arr {
		strs[i] = fmt.Sprintf("%v", v)
	}
	return strings.Join(strs, ",")
}

var _ = time.Now