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

package middleware_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/stretchr/testify/assert"

	"github.com/maelklingler/hivemind/internal/middleware"
)

func okHandler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})
}

func TestAPIKeyAuth_NoKey(t *testing.T) {
	h := middleware.APIKeyAuth("")(okHandler())
	req := httptest.NewRequest("GET", "/api/tickets", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestAPIKeyAuth_ValidHeader(t *testing.T) {
	h := middleware.APIKeyAuth("secret")(okHandler())
	req := httptest.NewRequest("GET", "/api/tickets", nil)
	req.Header.Set("X-API-Key", "secret")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestAPIKeyAuth_ValidBearer(t *testing.T) {
	h := middleware.APIKeyAuth("secret")(okHandler())
	req := httptest.NewRequest("GET", "/api/tickets", nil)
	req.Header.Set("Authorization", "Bearer secret")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestAPIKeyAuth_Invalid(t *testing.T) {
	h := middleware.APIKeyAuth("secret")(okHandler())
	req := httptest.NewRequest("GET", "/api/tickets", nil)
	req.Header.Set("X-API-Key", "wrong")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusUnauthorized, rec.Code)
}

func TestAPIKeyAuth_SkipsHealthz(t *testing.T) {
	h := middleware.APIKeyAuth("secret")(okHandler())
	req := httptest.NewRequest("GET", "/healthz", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
}

func TestCORS_Wildcard(t *testing.T) {
	h := middleware.CORS([]string{"*"})(okHandler())
	req := httptest.NewRequest("OPTIONS", "/api/tickets", nil)
	req.Header.Set("Origin", "http://evil.com")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusNoContent, rec.Code)
	assert.Equal(t, "*", rec.Header().Get("Access-Control-Allow-Origin"))
}

func TestCORS_SpecificOrigin(t *testing.T) {
	h := middleware.CORS([]string{"http://localhost:3000"})(okHandler())
	req := httptest.NewRequest("OPTIONS", "/api/tickets", nil)
	req.Header.Set("Origin", "http://localhost:3000")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusNoContent, rec.Code)
	assert.Equal(t, "http://localhost:3000", rec.Header().Get("Access-Control-Allow-Origin"))
	assert.Equal(t, "true", rec.Header().Get("Access-Control-Allow-Credentials"))
}

func TestCORS_DisallowedOrigin(t *testing.T) {
	h := middleware.CORS([]string{"http://localhost:3000"})(okHandler())
	req := httptest.NewRequest("OPTIONS", "/api/tickets", nil)
	req.Header.Set("Origin", "http://evil.com")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusNoContent, rec.Code)
	assert.Empty(t, rec.Header().Get("Access-Control-Allow-Origin"))
}

func TestRateLimit_InMemory(t *testing.T) {
	h := middleware.RateLimit(2)(okHandler())
	for i := 0; i < 2; i++ {
		req := httptest.NewRequest("GET", "/api/tickets", nil)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		assert.Equal(t, http.StatusOK, rec.Code)
	}
	req := httptest.NewRequest("GET", "/api/tickets", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusTooManyRequests, rec.Code)
}

func TestMaxBodySize(t *testing.T) {
	h := middleware.MaxBodySize(10)(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	req := httptest.NewRequest("POST", "/api/tickets", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	assert.Equal(t, http.StatusOK, rec.Code)
}