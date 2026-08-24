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

package sse

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"

	"github.com/maelklingler/hivemind/internal/database/repository"
)

// Event is a server-sent event.
type Event struct {
	Type string      `json:"type"`
	Data interface{} `json:"data"`
}

const pubsubChannel = "events"

// Broadcaster fans out SSE events to connected HTTP clients.
// When a PubSubRepository is provided, events are published to Redis for
// multi-replica fan-out; a background subscriber relays events to local clients.
// When no PubSubRepository is provided (dev mode), it operates in-memory only.
type Broadcaster struct {
	mu      sync.RWMutex
	clients map[chan Event]struct{}
	pubsub  repository.PubSubRepository
	cancel  context.CancelFunc
}

// NewBroadcaster creates a new broadcaster. If pubsub is non-nil, it subscribes
// to the Redis pub/sub channel and relays events to local clients.
func NewBroadcaster(pubsub repository.PubSubRepository) *Broadcaster {
	b := &Broadcaster{
		clients: make(map[chan Event]struct{}),
		pubsub:  pubsub,
	}
	if pubsub != nil {
		ctx, cancel := context.WithCancel(context.Background())
		b.cancel = cancel
		go b.runSubscriber(ctx)
	}
	return b
}

// Close stops the Redis subscriber if active.
func (b *Broadcaster) Close() {
	if b.cancel != nil {
		b.cancel()
	}
}

func (b *Broadcaster) runSubscriber(ctx context.Context) {
	for {
		if ctx.Err() != nil {
			return
		}
		ch, unsub, err := b.pubsub.Subscribe(ctx, pubsubChannel)
		if err != nil {
			slog.Warn("SSE pubsub subscribe failed, retrying", "error", err)
			return
		}
		for {
			select {
			case payload, ok := <-ch:
				if !ok {
					goto resubscribe
				}
				var event Event
				if err := json.Unmarshal(payload, &event); err != nil {
					slog.Warn("SSE pubsub unmarshal failed", "error", err)
					continue
				}
				b.deliverLocal(event)
			case <-ctx.Done():
				unsub()
				return
			}
		}
	resubscribe:
		unsub()
	}
}

// AddClient registers a new SSE client channel.
func (b *Broadcaster) AddClient() chan Event {
	ch := make(chan Event, 100)
	b.mu.Lock()
	b.clients[ch] = struct{}{}
	b.mu.Unlock()
	return ch
}

// RemoveClient deregisters a client channel.
func (b *Broadcaster) RemoveClient(ch chan Event) {
	b.mu.Lock()
	delete(b.clients, ch)
	b.mu.Unlock()
	close(ch)
}

// Broadcast publishes an event. With Redis, it publishes to the pubsub channel
// (which relays to all replicas including this one). Without Redis, it delivers
// locally.
func (b *Broadcaster) Broadcast(eventType string, data interface{}) {
	event := Event{Type: eventType, Data: data}
	if b.pubsub != nil {
		payload, err := json.Marshal(event)
		if err != nil {
			slog.Warn("SSE broadcast marshal failed", "error", err)
			return
		}
		if err := b.pubsub.PublishEvent(context.Background(), pubsubChannel, payload); err != nil {
			slog.Warn("SSE pubsub publish failed, delivering locally", "error", err)
			b.deliverLocal(event)
		}
		return
	}
	b.deliverLocal(event)
}

func (b *Broadcaster) deliverLocal(event Event) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	for ch := range b.clients {
		select {
		case ch <- event:
		default:
			slog.Warn("SSE event dropped, client channel full", "event_type", event.Type)
		}
	}
}

// ServeHTTP implements the SSE streaming endpoint.
func (b *Broadcaster) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")

	ch := b.AddClient()
	defer b.RemoveClient(ch)

	for {
		select {
		case event, ok := <-ch:
			if !ok {
				return
			}
			data, _ := json.Marshal(event.Data)
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event.Type, string(data))
			flusher.Flush()
		case <-r.Context().Done():
			return
		}
	}
}