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

package redisrepo_test

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"

	"github.com/maelklingler/hivemind/internal/database/redisrepo"
	"github.com/maelklingler/hivemind/internal/testutil"
)

func TestDedup_IsDuplicate(t *testing.T) {
	url := testutil.RedisFixture(t)
	ctx := context.Background()
	c, err := redisrepo.New(ctx, url)
	require.NoError(t, err)
	t.Cleanup(func() { _ = c.Close() })

	dup, err := c.IsDuplicate(ctx, "evt-1", 5*time.Second)
	require.NoError(t, err)
	assert.False(t, dup)

	dup, err = c.IsDuplicate(ctx, "evt-1", 5*time.Second)
	require.NoError(t, err)
	assert.True(t, dup)

	dup, err = c.IsDuplicate(ctx, "evt-2", 5*time.Second)
	require.NoError(t, err)
	assert.False(t, dup)
}

func TestLock_AcquireAndRelease(t *testing.T) {
	url := testutil.RedisFixture(t)
	ctx := context.Background()
	c, err := redisrepo.New(ctx, url)
	require.NoError(t, err)
	t.Cleanup(func() { _ = c.Close() })

	acq, err := c.Acquire(ctx, "lock-1", 10*time.Second)
	require.NoError(t, err)
	assert.True(t, acq)

	acq2, err := c.Acquire(ctx, "lock-1", 10*time.Second)
	require.NoError(t, err)
	assert.False(t, acq2)
}

func TestRateLimit_Allow(t *testing.T) {
	url := testutil.RedisFixture(t)
	ctx := context.Background()
	c, err := redisrepo.New(ctx, url)
	require.NoError(t, err)
	t.Cleanup(func() { _ = c.Close() })

	for i := 0; i < 3; i++ {
		allowed, err := c.Allow(ctx, "rl-key", 3, time.Minute)
		require.NoError(t, err)
		assert.True(t, allowed)
	}
	allowed, err := c.Allow(ctx, "rl-key", 3, time.Minute)
	require.NoError(t, err)
	assert.False(t, allowed)
}

func TestQueueStream_PublishConsumeAck(t *testing.T) {
	url := testutil.RedisFixture(t)
	ctx := context.Background()
	c, err := redisrepo.New(ctx, url)
	require.NoError(t, err)
	t.Cleanup(func() { _ = c.Close() })

	// Ensure consumer group exists BEFORE publishing, so XREADGROUP with ">"
	// picks up new entries.
	entries, err := c.Consume(ctx, "test-consumer", 10, 100)
	require.NoError(t, err)
	assert.Empty(t, entries)

	id, err := c.Publish(ctx, "TICKET-1", 5)
	require.NoError(t, err)
	assert.NotEmpty(t, id)

	entries, err = c.Consume(ctx, "test-consumer", 10, 1000)
	require.NoError(t, err)
	require.Len(t, entries, 1)
	assert.Equal(t, "TICKET-1", entries[0].TicketID)

	require.NoError(t, c.Ack(ctx, "test-consumer", entries[0].StreamID))

	pending, err := c.Pending(ctx, "test-consumer")
	require.NoError(t, err)
	assert.Equal(t, int64(0), pending)
}

func TestPubSub_PublishEventSubscribe(t *testing.T) {
	url := testutil.RedisFixture(t)
	ctx := context.Background()
	c, err := redisrepo.New(ctx, url)
	require.NoError(t, err)
	t.Cleanup(func() { _ = c.Close() })

	ch, unsub, err := c.Subscribe(ctx, "events")
	require.NoError(t, err)
	defer unsub()

	time.Sleep(100 * time.Millisecond)

	require.NoError(t, c.PublishEvent(ctx, "events", []byte("hello")))

	select {
	case msg := <-ch:
		assert.Equal(t, "hello", string(msg))
	case <-time.After(2 * time.Second):
		t.Fatal("pubsub message not received")
	}
}