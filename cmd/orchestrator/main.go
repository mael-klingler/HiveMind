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

package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"

	"github.com/pressly/goose/v3"

	"github.com/maelklingler/hivemind/internal/api"
	"github.com/maelklingler/hivemind/internal/background"
	"github.com/maelklingler/hivemind/internal/config"
	"github.com/maelklingler/hivemind/internal/database"
	"github.com/maelklingler/hivemind/internal/database/redisrepo"
	"github.com/maelklingler/hivemind/internal/database/pgxrepo"
	"github.com/maelklingler/hivemind/internal/database/repository"
	"github.com/maelklingler/hivemind/internal/k8s"
	"github.com/maelklingler/hivemind/internal/llm"
	llmprovider "github.com/maelklingler/hivemind/internal/llm/provider"
	"github.com/maelklingler/hivemind/internal/sse"
	"github.com/maelklingler/hivemind/internal/workspace"
)

func main() {
	migrateCmd := flag.Bool("migrate", false, "Run database migrations")
	portFlag := flag.Int("port", 0, "Override port (default from ORCHESTRATOR_PORT env)")
	flag.Parse()

	cfg := config.Load()
	if *portFlag != 0 {
		cfg.Port = fmt.Sprintf("%d", *portFlag)
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	if *migrateCmd {
		if err := runMigrations(cfg); err != nil {
			slog.Error("migration failed", "error", err)
			os.Exit(1)
		}
		slog.Info("migrations complete")
		return
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	db, err := database.New(ctx, cfg.DatabaseURL)
	if err != nil {
		slog.Error("failed to connect to database", "error", err)
		os.Exit(1)
	}
	defer db.Close()

	if err := runMigrations(cfg); err != nil {
		slog.Warn("auto-migration warning", "error", err)
	}

	settingsRepo := pgxrepo.NewSettingsRepo(db.Pool())
	cfg = config.LoadFromDB(func(key string) (string, error) {
		return settingsRepo.GetSetting(ctx, key)
	})
	slog.Info("settings loaded from database")

	k8sClient, err := k8s.NewClient(cfg.AgentNamespace)
	if err != nil {
		slog.Warn("kubernetes client not available (running outside cluster?)", "error", err)
	} else {
		if err := k8sClient.EnsureNamespace(ctx, cfg.AgentNamespace); err != nil {
			slog.Warn("failed to ensure agent namespace", "error", err)
		}
		if err := k8sClient.EnsureSecrets(ctx, k8s.SecretParams{
			GitLabToken:       cfg.GitLabToken,
			GitHubToken:       cfg.GitHubToken,
			OllamaCloudAPIKey: cfg.OllamaCloudAPIKey,
			OpenAIAPIKey:      cfg.OpenAIAPIKey,
			AnthropicAPIKey:   cfg.AnthropicAPIKey,
			HivemindAPIKey:    cfg.HivemindAPIKey,
		}); err != nil {
			slog.Warn("failed to ensure agent secrets", "error", err)
		}
	}

	llmClient := llm.NewLLMClient(cfg)

	hostname, _ := os.Hostname()
	if hostname == "" {
		hostname = fmt.Sprintf("orchestrator-%d", os.Getpid())
	}
	leaderElector := background.NewLeaderElector(db.Pool(), "agent-monitor", hostname, 60*time.Second)

	llmProvider, err := llmprovider.NewFromConfig(cfg)
	if err != nil {
		slog.Warn("failed to create LLM provider", "error", err)
	}
	var wsBuilder *workspace.Builder
	if llmProvider != nil {
		wsBuilder = workspace.NewBuilder(llmProvider)
	}

	var redisClient *redisrepo.Client
	var pubsubRepo repository.PubSubRepository
	if cfg.RedisURL != "" {
		redisClient, err = redisrepo.New(ctx, cfg.RedisURL)
		if err != nil {
			slog.Warn("redis not available, falling back to in-memory", "error", err)
		} else {
			pubsubRepo = redisClient
			slog.Info("redis connected", "url", cfg.RedisURL)
		}
	}

	if err := db.EnsureAgentPool(ctx, 3); err != nil {
		slog.Warn("failed to ensure agent pool", "error", err)
	}

	// Startup orphan recovery: reset running tickets whose agent pod no
	// longer exists back to queued, so they get reprocessed.
	if k8sClient != nil {
		acq, err := leaderElector.TryAcquire(ctx)
		if err == nil && acq {
			slog.Info("acquired leader lock for startup orphan recovery")
			running, err := db.ListTicketsPaged(ctx, "running", 500, 0)
			if err == nil {
				for _, t := range running {
					podName := "agent-worker-" + strings.ToLower(t.ID)
					pod, err := k8sClient.GetPod(ctx, podName)
					if err == nil && pod == nil {
						slog.Info("orphan ticket detected at startup, requeueing", "ticket_id", t.ID)
						if err := db.UpdateTicketStatus(ctx, t.ID, "queued"); err != nil {
							slog.Error("failed to requeue orphan ticket", "ticket_id", t.ID, "error", err)
						}
					}
				}
			}
			_ = leaderElector.Release(ctx)
		} else {
			slog.Info("not leader, skipping startup orphan recovery")
		}
	}

	broadcaster := sse.NewBroadcaster(pubsubRepo)

	queueProcessor := background.NewQueueProcessor(cfg, db, k8sClient, llmClient, wsBuilder)
	agentMonitor := background.NewAgentMonitor(cfg, db, k8sClient, leaderElector)
	reviewMonitor := background.NewReviewMonitor(cfg, db, k8sClient, broadcaster)
	pipelineEngine := background.NewPipelineEngine(cfg, db, broadcaster)
	planner := background.NewPlanner(cfg, db, llmProvider, broadcaster)
	learningWorker := background.NewLearningWorker(cfg, db)

	var wg sync.WaitGroup
	wg.Add(6)
	go func() { defer wg.Done(); queueProcessor.Run(ctx) }()
	go func() { defer wg.Done(); agentMonitor.Run(ctx) }()
	go func() { defer wg.Done(); reviewMonitor.Run(ctx) }()
	go func() { defer wg.Done(); pipelineEngine.Run(ctx) }()
	go func() { defer wg.Done(); planner.Run(ctx) }()
	go func() { defer wg.Done(); learningWorker.Run(ctx) }()

	server := api.NewServer(cfg, db, k8sClient, broadcaster, redisClient, redisClient, pipelineEngine, getStaticFS())

	addr := fmt.Sprintf(":%s", cfg.Port)
	httpServer := &http.Server{
		Addr:         addr,
		Handler:      server.Router,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGTERM, syscall.SIGINT)

	go func() {
		slog.Info("orchestrator starting", "addr", addr, "vcs_provider", cfg.VCSProvider,
			"api_key", boolToStr(cfg.HivemindAPIKey != ""), "k8s_namespace", cfg.AgentNamespace)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("server error, initiating graceful shutdown", "error", err)
			cancel()
		}
	}()

	<-sigCh
	slog.Info("shutting down gracefully")

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer shutdownCancel()

	queueProcessor.Stop()
	agentMonitor.Stop()  // no-op in informer mode
	reviewMonitor.Stop()
	pipelineEngine.Stop()
	planner.Stop()
	learningWorker.Stop()

	done := make(chan struct{})
	go func() {
		wg.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(30 * time.Second):
		slog.Warn("worker shutdown timed out after 30s, proceeding")
	}

	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		slog.Error("server shutdown error", "error", err)
	}

	broadcaster.Close()
	if redisClient != nil {
		redisClient.Close()
	}

	slog.Info("shutdown complete")
}

func runMigrations(cfg *config.Config) error {
	if cfg.DatabaseURL == "" {
		slog.Info("no DATABASE_URL set, skipping migrations")
		return nil
	}
	db, err := goose.OpenDBWithDriver("postgres", cfg.DatabaseURL)
	if err != nil {
		return fmt.Errorf("open migration db: %w", err)
	}
	defer db.Close()

	migrationsDir, err := database.MigrationsDir()
	if err != nil {
		return fmt.Errorf("resolve migrations dir: %w", err)
	}
	if err := goose.Up(db, migrationsDir); err != nil {
		return fmt.Errorf("run migrations: %w", err)
	}
	return nil
}

func boolToStr(b bool) string {
	if b {
		return "enabled"
	}
	return "disabled"
}