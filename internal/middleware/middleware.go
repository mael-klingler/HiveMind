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

package middleware

import (
	"crypto/subtle"
	"net/http"
	"strings"
	"sync"
	"time"
)

func CORS(allowedOrigins []string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")
			allowed := false
			hasWildcard := false
			for _, o := range allowedOrigins {
				if o == "*" {
					hasWildcard = true
					allowed = true
					break
				}
				if o == origin {
					allowed = true
					break
				}
			}
			if allowed {
				if hasWildcard {
					w.Header().Set("Access-Control-Allow-Origin", "*")
				} else {
					w.Header().Set("Access-Control-Allow-Origin", origin)
				}
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")
				if !hasWildcard {
					w.Header().Set("Access-Control-Allow-Credentials", "true")
				}
			}
			if r.Method == "OPTIONS" {
				w.WriteHeader(http.StatusNoContent)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func APIKeyAuth(apiKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if apiKey == "" {
				next.ServeHTTP(w, r)
				return
			}
			path := r.URL.Path
			if path == "/healthz" || path == "/readyz" || path == "/metrics" || path == "/" {
				next.ServeHTTP(w, r)
				return
			}
			if !strings.HasPrefix(path, "/api/") && !strings.HasPrefix(path, "/webhooks/") {
				next.ServeHTTP(w, r)
				return
			}
			provided := r.Header.Get("X-API-Key")
			if provided == "" {
				provided = strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
			}
			if subtle.ConstantTimeCompare([]byte(provided), []byte(apiKey)) != 1 {
				http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

type rateLimiter struct {
	mu       sync.Mutex
	requests map[string][]time.Time
	limit    int
	window   time.Duration
}

func RateLimit(perMinute int) func(http.Handler) http.Handler {
	limiter := &rateLimiter{
		requests: make(map[string][]time.Time),
		limit:    perMinute,
		window:   time.Minute,
	}

	// Background sweeper to prevent unbounded growth
	go func() {
		for {
			time.Sleep(limiter.window)
			limiter.mu.Lock()
			now := time.Now()
			for ip, times := range limiter.requests {
				valid := times[:0]
				for _, t := range times {
					if now.Sub(t) < limiter.window {
						valid = append(valid, t)
					}
				}
				if len(valid) == 0 {
					delete(limiter.requests, ip)
				} else {
					limiter.requests[ip] = valid
				}
			}
			limiter.mu.Unlock()
		}
	}()

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			ip := r.RemoteAddr
			limiter.mu.Lock()
			now := time.Now()
			requests := limiter.requests[ip]
			valid := make([]time.Time, 0, len(requests))
			for _, t := range requests {
				if now.Sub(t) < limiter.window {
					valid = append(valid, t)
				}
			}
			if len(valid) >= limiter.limit {
				limiter.mu.Unlock()
				http.Error(w, `{"error":"rate limit exceeded"}`, http.StatusTooManyRequests)
				return
			}
			valid = append(valid, now)
			limiter.requests[ip] = valid
			limiter.mu.Unlock()

			next.ServeHTTP(w, r)
		})
	}
}

func MaxBodySize(maxBytes int64) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			r.Body = http.MaxBytesReader(w, r.Body, maxBytes)
			next.ServeHTTP(w, r)
		})
	}
}