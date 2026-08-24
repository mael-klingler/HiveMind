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

package repository

import (
	"context"
	"time"
)

// QueueStreamEntry represents a consumed queue item from a Redis stream.
type QueueStreamEntry struct {
	StreamID string
	TicketID string
	Priority int
}

// QueueStreamRepository defines Redis-stream-based queue operations for
// distributed, multi-replica-safe ticket processing.
type QueueStreamRepository interface {
	// Publish enqueues a ticket ID onto the Redis stream.
	Publish(ctx context.Context, ticketID string, priority int) (string, error)
	// Consume reads up to max entries from the stream using a consumer group.
	// Blocks up to blockMillis if no entries are available.
	Consume(ctx context.Context, consumer string, count int64, blockMillis int64) ([]*QueueStreamEntry, error)
	// Ack acknowledges successful processing of a stream entry.
	Ack(ctx context.Context, consumer, streamID string) error
	// Reclaim re-queues a stream entry back to the stream (e.g. on spawn failure).
	Reclaim(ctx context.Context, ticketID string, priority int) (string, error)
	// Pending returns the count of pending (unacked) entries for a consumer.
	Pending(ctx context.Context, consumer string) (int64, error)
}

// DedupRepository defines idempotency-key / dedup operations (Redis SETNX).
type DedupRepository interface {
	// IsDuplicate returns true if eventID was already seen within ttl.
	// Otherwise records eventID with ttl and returns false.
	IsDuplicate(ctx context.Context, eventID string, ttl time.Duration) (bool, error)
}

// LockRepository defines distributed lock operations (Redis SETNX EX).
type LockRepository interface {
	// Acquire tries to acquire a named lock with ttl. Returns true on success.
	Acquire(ctx context.Context, name string, ttl time.Duration) (bool, error)
	// Release releases a named lock. Only succeeds if the caller holds it (token-checked).
	Release(ctx context.Context, name, token string) error
}

// PubSubRepository defines Redis pub/sub operations for multi-replica SSE fan-out.
type PubSubRepository interface {
	// PublishEvent publishes an event payload to the given channel.
	PublishEvent(ctx context.Context, channel string, payload []byte) error
	// Subscribe subscribes to a channel and returns a channel of messages.
	Subscribe(ctx context.Context, channel string) (<-chan []byte, func(), error)
}

// RateLimitRepository defines distributed rate-limiting via Redis.
type RateLimitRepository interface {
	// Allow returns true if the key is under the limit for the window, and
	// increments the counter. Returns false if the limit is exceeded.
	Allow(ctx context.Context, key string, limit int, window time.Duration) (bool, error)
}