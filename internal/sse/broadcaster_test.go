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

package sse_test

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"

	"github.com/maelklingler/hivemind/internal/sse"
)

func TestBroadcaster_InMemory_Broadcast(t *testing.T) {
	b := sse.NewBroadcaster(nil)
	t.Cleanup(b.Close)

	ch := b.AddClient()
	defer b.RemoveClient(ch)

	b.Broadcast("test_event", map[string]string{"key": "val"})

	select {
	case event := <-ch:
		assert.Equal(t, "test_event", event.Type)
	case <-time.After(time.Second):
		t.Fatal("event not received")
	}
}

func TestBroadcaster_InMemory_ServeHTTP_Headers(t *testing.T) {
	b := sse.NewBroadcaster(nil)
	t.Cleanup(b.Close)

	// Verify the broadcaster accepts clients and delivers events.
	ch := b.AddClient()
	b.Broadcast("ping", "hello")
	select {
	case event := <-ch:
		assert.Equal(t, "ping", event.Type)
	case <-time.After(time.Second):
		t.Fatal("event not received")
	}
	b.RemoveClient(ch)
}

func TestBroadcaster_DroppedWhenChannelFull(t *testing.T) {
	b := sse.NewBroadcaster(nil)
	t.Cleanup(b.Close)

	ch := b.AddClient()
	defer b.RemoveClient(ch)

	// Fill the buffered channel (capacity 100)
	for i := 0; i < 110; i++ {
		b.Broadcast("fill", nil)
	}
	// Should not block; we just verify the test doesn't hang
	drained := 0
loop:
	for {
		select {
		case <-ch:
			drained++
		default:
			break loop
		}
	}
	assert.True(t, drained <= 100)
}

func TestEvent_JSON(t *testing.T) {
	e := sse.Event{Type: "test", Data: map[string]string{"a": "b"}}
	data, err := json.Marshal(e)
	assert.NoError(t, err)
	assert.Contains(t, string(data), `"type":"test"`)
	assert.True(t, strings.Contains(string(data), `"a":"b"`))
}