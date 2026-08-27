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

// Package testutil provides shared test fixtures (Postgres + Redis via
// testcontainers-go).
package testutil

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/pressly/goose/v3"
	"github.com/testcontainers/testcontainers-go"
	tcredis "github.com/testcontainers/testcontainers-go/modules/redis"
	tcpg "github.com/testcontainers/testcontainers-go/modules/postgres"
	"github.com/testcontainers/testcontainers-go/wait"
)

// PostgresFixture starts a Postgres container and returns a connected pgxpool
// with all migrations applied.
func PostgresFixture(t *testing.T) *pgxpool.Pool {
	t.Helper()
	ctx := context.Background()
	_, skip := os.LookupEnv("SKIP_INTEGRATION_TESTS")
	if skip {
		t.Skip("SKIP_INTEGRATION_TESTS set")
	}
	container, err := tcpg.Run(ctx, "postgres:16-alpine",
		tcpg.WithDatabase("hivemindtest"),
		tcpg.WithUsername("test"),
		tcpg.WithPassword("test"),
		testcontainers.WithWaitStrategy(
			wait.ForLog("database system is ready to accept connections").WithOccurrence(2).WithStartupTimeout(60*time.Second),
		),
	)
	if err != nil {
		t.Skipf("postgres container unavailable: %v", err)
	}
	t.Cleanup(func() {
		if err := container.Terminate(ctx); err != nil {
			t.Logf("terminate postgres: %v", err)
		}
	})
	connStr, err := container.ConnectionString(ctx, "sslmode=disable")
	if err != nil {
		t.Fatalf("connection string: %v", err)
	}
	pool, err := pgxpool.New(ctx, connStr)
	if err != nil {
		t.Fatalf("connect pool: %v", err)
	}
	t.Cleanup(pool.Close)
	runMigrations(t, connStr)
	return pool
}

func runMigrations(t *testing.T, connStr string) {
	t.Helper()
	db, err := goose.OpenDBWithDriver("pgx", connStr)
	if err != nil {
		t.Fatalf("open migration db: %v", err)
	}
	defer db.Close()
	migrationsDir := findMigrationsDir(t)
	if err := goose.Up(db, migrationsDir); err != nil {
		t.Fatalf("run migrations: %v", err)
	}
}

func findMigrationsDir(t *testing.T) string {
	t.Helper()
	candidates := []string{
		"../../migrations",
		"../../../internal/database/migrations",
		"./internal/database/migrations",
	}
	for _, c := range candidates {
		if _, err := os.Stat(c); err == nil {
			abs, _ := filepath.Abs(c)
			return abs
		}
	}
	t.Fatal("migrations directory not found")
	return ""
}

// RedisFixture starts a Redis container and returns its connection URL.
func RedisFixture(t *testing.T) string {
	t.Helper()
	ctx := context.Background()
	_, skip := os.LookupEnv("SKIP_INTEGRATION_TESTS")
	if skip {
		t.Skip("SKIP_INTEGRATION_TESTS set")
	}
	container, err := tcredis.Run(ctx, "redis:7-alpine")
	if err != nil {
		t.Skipf("redis container unavailable: %v", err)
	}
	t.Cleanup(func() {
		if err := container.Terminate(ctx); err != nil {
			t.Logf("terminate redis: %v", err)
		}
	})
	host, err := container.Host(ctx)
	if err != nil {
		t.Fatalf("redis host: %v", err)
	}
	port, err := container.MappedPort(ctx, "6379")
	if err != nil {
		t.Fatalf("redis port: %v", err)
	}
	return fmt.Sprintf("redis://%s:%s", host, port.Port())
}