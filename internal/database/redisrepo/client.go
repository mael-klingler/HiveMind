// Copyright 2026 Mael Klingler
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// Package redisrepo implements the Redis-backed repository interfaces
// (queue stream, dedup, lock, pubsub, rate-limit).
package redisrepo

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/maelklingler/hivemind/internal/database/repository"
)

const (
	streamKey          = "hivemind:queue"
	streamGroup        = "hivemind-workers"
	dedupKeyPrefix     = "hivemind:dedup:"
	lockKeyPrefix      = "hivemind:lock:"
	pubsubChannelPrefix = "hivemind:pubsub:"
	rateLimitKeyPrefix = "hivemind:rl:"
)

// Client wraps a redis client and implements all Redis-backed repositories.
type Client struct {
	rdb *redis.Client
}

// New creates a new Redis client and pings the server.
func New(ctx context.Context, redisURL string) (*Client, error) {
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, fmt.Errorf("parse redis URL: %w", err)
	}
	rdb := redis.NewClient(opts)
	if err := rdb.Ping(ctx).Err(); err != nil {
		return nil, fmt.Errorf("ping redis: %w", err)
	}
	return &Client{rdb: rdb}, nil
}

// Close closes the underlying redis client.
func (c *Client) Close() error {
	return c.rdb.Close()
}

// ensureGroup creates the consumer group for the queue stream if it doesn't exist.
func (c *Client) ensureGroup(ctx context.Context) error {
	err := c.rdb.XGroupCreateMkStream(ctx, streamKey, streamGroup, "$").Err()
	if err != nil {
		if errContains(err, "BUSYGROUP") {
			return nil
		}
		return fmt.Errorf("create consumer group: %w", err)
	}
	return nil
}

func errContains(err error, substr string) bool {
	return err != nil && (err.Error() == "BUSYGROUP Consumer Group name already exists" ||
		(len(err.Error()) >= len(substr) && err.Error() != "" && containsSubstr(err.Error(), substr)))
}

func containsSubstr(s, substr string) bool {
	for i := 0; i+len(substr) <= len(s); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

// --- QueueStreamRepository ---

func (c *Client) Publish(ctx context.Context, ticketID string, priority int) (string, error) {
	id, err := c.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: streamKey,
		Values: map[string]interface{}{
			"ticket_id": ticketID,
			"priority":  strconv.Itoa(priority),
		},
	}).Result()
	if err != nil {
		return "", fmt.Errorf("xadd: %w", err)
	}
	return id, nil
}

func (c *Client) Consume(ctx context.Context, consumer string, count int64, blockMillis int64) ([]*repository.QueueStreamEntry, error) {
	if err := c.ensureGroup(ctx); err != nil {
		return nil, err
	}
	streams, err := c.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    streamGroup,
		Consumer: consumer,
		Streams:  []string{streamKey, ">"},
		Count:    count,
		Block:    time.Duration(blockMillis) * time.Millisecond,
	}).Result()
	if err != nil {
		if err == redis.Nil {
			return nil, nil
		}
		return nil, fmt.Errorf("xreadgroup: %w", err)
	}
	if len(streams) == 0 {
		return nil, nil
	}
	entries := make([]*repository.QueueStreamEntry, 0, len(streams[0].Messages))
	for _, msg := range streams[0].Messages {
		entry := &repository.QueueStreamEntry{StreamID: msg.ID}
		if v, ok := msg.Values["ticket_id"].(string); ok {
			entry.TicketID = v
		}
		if v, ok := msg.Values["priority"].(string); ok {
			if p, err := strconv.Atoi(v); err == nil {
				entry.Priority = p
			}
		}
		entries = append(entries, entry)
	}
	return entries, nil
}

func (c *Client) Ack(ctx context.Context, consumer, streamID string) error {
	_, err := c.rdb.XAck(ctx, streamKey, streamGroup, streamID).Result()
	return err
}

func (c *Client) Reclaim(ctx context.Context, ticketID string, priority int) (string, error) {
	return c.Publish(ctx, ticketID, priority)
}

func (c *Client) Pending(ctx context.Context, consumer string) (int64, error) {
	res, err := c.rdb.XPending(ctx, streamKey, streamGroup).Result()
	if err != nil {
		return 0, err
	}
	return res.Count, nil
}

// --- DedupRepository ---

func (c *Client) IsDuplicate(ctx context.Context, eventID string, ttl time.Duration) (bool, error) {
	key := dedupKeyPrefix + eventID
	ok, err := c.rdb.SetNX(ctx, key, "1", ttl).Result()
	if err != nil {
		return false, fmt.Errorf("dedup setnx: %w", err)
	}
	return !ok, nil
}

// --- LockRepository ---

func (c *Client) Acquire(ctx context.Context, name string, ttl time.Duration) (bool, error) {
	key := lockKeyPrefix + name
	token := fmt.Sprintf("%d", time.Now().UnixNano())
	ok, err := c.rdb.SetNX(ctx, key, token, ttl).Result()
	if err != nil {
		return false, fmt.Errorf("lock setnx: %w", err)
	}
	if !ok {
		return false, nil
	}
	return true, nil
}

func (c *Client) Release(ctx context.Context, name, token string) error {
	key := lockKeyPrefix + name
	val, err := c.rdb.Get(ctx, key).Result()
	if err != nil {
		if err == redis.Nil {
			return nil
		}
		return err
	}
	if val != token {
		return nil
	}
	return c.rdb.Del(ctx, key).Err()
}

// --- PubSubRepository ---

func (c *Client) PublishEvent(ctx context.Context, channel string, payload []byte) error {
	ch := pubsubChannelPrefix + channel
	return c.rdb.Publish(ctx, ch, payload).Err()
}

func (c *Client) Subscribe(ctx context.Context, channel string) (<-chan []byte, func(), error) {
	ch := pubsubChannelPrefix + channel
	pubsub := c.rdb.Subscribe(ctx, ch)
	msgCh := pubsub.Channel()
	out := make(chan []byte, 100)
	go func() {
		defer close(out)
		for msg := range msgCh {
			out <- []byte(msg.Payload)
		}
	}()
	cancel := func() {
		_ = pubsub.Close()
	}
	return out, cancel, nil
}

// --- RateLimitRepository ---

func (c *Client) Allow(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
	k := rateLimitKeyPrefix + key
	count, err := c.rdb.Incr(ctx, k).Result()
	if err != nil {
		return false, fmt.Errorf("rate limit incr: %w", err)
	}
	if count == 1 {
		_ = c.rdb.Expire(ctx, k, window).Err()
	}
	return count <= int64(limit), nil
}

// Compile-time interface checks.
var (
	_ repository.QueueStreamRepository = (*Client)(nil)
	_ repository.DedupRepository       = (*Client)(nil)
	_ repository.LockRepository        = (*Client)(nil)
	_ repository.PubSubRepository      = (*Client)(nil)
	_ repository.RateLimitRepository   = (*Client)(nil)
)