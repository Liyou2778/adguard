package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/config"
	"github.com/Liyou2778/adguard/services/gateway/internal/httpapi"
	"github.com/Liyou2778/adguard/services/gateway/internal/judge"
	"github.com/Liyou2778/adguard/services/gateway/internal/metrics"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/pipeline"
	"github.com/Liyou2778/adguard/services/gateway/internal/queue"
	"github.com/Liyou2778/adguard/services/gateway/internal/snapshot"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/Liyou2778/adguard/services/gateway/internal/worker"
	"github.com/redis/go-redis/v9"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	if err := run(logger); err != nil {
		logger.Error("gateway exited with error", "err", err)
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	cfg, err := config.Load()
	if err != nil {
		return fmt.Errorf("config: %w", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	m := metrics.New()

	// --- MySQL (schema is migrated by the Java control plane) ---
	st, err := store.OpenMySQL(cfg.MySQLDSN)
	if err != nil {
		return err
	}
	defer st.Close()

	startupCtx, cancelStartup := context.WithTimeout(ctx, 90*time.Second)
	defer cancelStartup()
	if err := waitFor(startupCtx, "mysql", logger, func(ctx context.Context) error {
		if err := st.Ping(ctx); err != nil {
			return err
		}
		return st.SchemaReady(ctx)
	}); err != nil {
		return err
	}

	// --- Redis (queue) ---
	rdb := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPassword,
		DB:       cfg.RedisDB,
	})
	defer rdb.Close()
	if err := waitFor(startupCtx, "redis", logger, func(ctx context.Context) error {
		return rdb.Ping(ctx).Err()
	}); err != nil {
		return err
	}

	// --- Object storage ---
	objects, err := objectstore.NewMinio(cfg.MinioEndpoint, cfg.MinioAccessKey, cfg.MinioSecretKey,
		cfg.MinioUseSSL, cfg.MinioBucket, cfg.MinioPublicEndpoint)
	if err != nil {
		return err
	}
	if err := waitFor(startupCtx, "minio", logger, func(ctx context.Context) error {
		return objects.EnsureBucket(ctx)
	}); err != nil {
		return err
	}

	// --- Queue + judging pipeline ---
	streams := queue.NewRedisStreams(rdb, "audit:tasks", cfg.WorkerGroup, cfg.WorkerName,
		cfg.MaxAttempts, logger)
	snapshots := snapshot.New(cfg.ControlPlaneURL, cfg.SnapshotTimeout)

	var judging judge.Service
	switch strings.ToLower(cfg.JudgeBackend) {
	case "stub":
		logger.Warn("judge backend = stub (M1 placeholder): every asset is routed to manual")
		judging = judge.NewStub(snapshots, cfg.StubModelVersion, logger)
	default:
		pipelineClient := pipeline.New(cfg.PipelineURL, cfg.PipelineTimeout, logger)
		judging = judge.NewRuleJudge(pipelineClient, objects, snapshots, logger)
		logger.Info("judge backend = rule (M2 matcher + M3 clause retrieval)",
			"pipeline_url", cfg.PipelineURL)
	}
	auditWorker := worker.New(st, judging, cfg.MaxAttempts, m, logger)

	if cfg.WorkerEnabled {
		go func() {
			logger.Info("audit worker started", "group", cfg.WorkerGroup, "consumer", cfg.WorkerName)
			if err := auditWorker.Run(ctx, streams); err != nil && !errors.Is(err, context.Canceled) {
				logger.Error("audit worker stopped", "err", err)
			}
		}()
	}

	// --- HTTP ---
	ready := func(ctx context.Context) error {
		if err := st.Ping(ctx); err != nil {
			return fmt.Errorf("mysql: %w", err)
		}
		if err := st.SchemaReady(ctx); err != nil {
			return err
		}
		if err := rdb.Ping(ctx).Err(); err != nil {
			return fmt.Errorf("redis: %w", err)
		}
		return nil
	}
	server := httpapi.New(cfg, st, objects, streams, m, logger, ready)
	httpServer := &http.Server{
		Addr:              cfg.HTTPAddr,
		Handler:           server.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       120 * time.Second,
		WriteTimeout:      180 * time.Second,
		IdleTimeout:       120 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() {
		logger.Info("gateway listening", "addr", cfg.HTTPAddr,
			"worker_enabled", cfg.WorkerEnabled, "max_asset_bytes", cfg.MaxAssetBytes)
		if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		logger.Info("shutdown signal received")
	}

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	return httpServer.Shutdown(shutdownCtx)
}

func waitFor(ctx context.Context, name string, logger *slog.Logger, check func(context.Context) error) error {
	const maxAttempts = 45
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		if err := check(ctx); err == nil {
			logger.Info("dependency ready", "dependency", name, "attempt", attempt)
			return nil
		} else {
			lastErr = err
		}
		logger.Warn("dependency not ready yet", "dependency", name, "attempt", attempt, "err", lastErr)
		select {
		case <-ctx.Done():
			return fmt.Errorf("%s not ready: %w", name, lastErr)
		case <-time.After(2 * time.Second):
		}
	}
	return fmt.Errorf("%s not ready after %d attempts: %w", name, maxAttempts, lastErr)
}
