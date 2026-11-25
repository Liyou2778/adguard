package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

// Config is the gateway runtime configuration. Every field has an env override so
// the same binary runs locally (compose) and on the ECS (systemd/docker).
type Config struct {
	HTTPAddr string

	MaxAssetBytes    int64
	DefaultChunkSize int
	MinChunkSize     int
	MaxChunks        int

	MySQLDSN string

	RedisAddr     string
	RedisPassword string
	RedisDB       int

	MinioEndpoint       string
	MinioPublicEndpoint string
	MinioAccessKey      string
	MinioSecretKey      string
	MinioBucket         string
	MinioUseSSL         bool

	ControlPlaneURL string
	SnapshotTimeout time.Duration

	// M3 judging backend: "rule" calls the Python pipeline (/v1/judge); "stub" keeps
	// the M1 placeholder judge.
	JudgeBackend    string
	PipelineURL     string
	PipelineTimeout time.Duration

	APIKey          string
	RateLimitPerMin int

	// M8 security hardening: production requires either a user JWT (tenant users) or the
	// service X-API-Key (automation). JWT secret is shared with the Java control plane.
	AuthRequired bool
	JWTSecret    string

	// M9 quota: daily weighted quota from tenant.daily_quota, counted in Redis.
	QuotaEnabled bool

	WorkerEnabled bool
	WorkerGroup   string
	WorkerName    string
	MaxAttempts   int

	StubModelVersion string
}

// Load reads configuration from the environment, applying local-dev defaults.
func Load() (*Config, error) {
	cfg := &Config{
		HTTPAddr:            env("GATEWAY_HTTP_ADDR", ":8080"),
		MaxAssetBytes:       envInt64("GATEWAY_MAX_ASSET_BYTES", 100*1024*1024),
		DefaultChunkSize:    envInt("GATEWAY_CHUNK_SIZE", 5*1024*1024),
		MinChunkSize:        envInt("GATEWAY_MIN_CHUNK_SIZE", 1024*1024),
		MaxChunks:           envInt("GATEWAY_MAX_CHUNKS", 10000),
		MySQLDSN:            env("GATEWAY_MYSQL_DSN", "adguard:adguard@tcp(127.0.0.1:3307)/adguard?parseTime=true&charset=utf8mb4&loc=UTC"),
		RedisAddr:           env("GATEWAY_REDIS_ADDR", "127.0.0.1:6379"),
		RedisPassword:       env("GATEWAY_REDIS_PASSWORD", ""),
		RedisDB:             envInt("GATEWAY_REDIS_DB", 0),
		MinioEndpoint:       env("GATEWAY_MINIO_ENDPOINT", "127.0.0.1:9000"),
		MinioPublicEndpoint: env("GATEWAY_MINIO_PUBLIC_ENDPOINT", "127.0.0.1:9000"),
		MinioAccessKey:      env("GATEWAY_MINIO_ACCESS_KEY", "minioadmin"),
		MinioSecretKey:      env("GATEWAY_MINIO_SECRET_KEY", "minioadmin"),
		MinioBucket:         env("GATEWAY_MINIO_BUCKET", "adguard-assets"),
		MinioUseSSL:         envBool("GATEWAY_MINIO_USE_SSL", false),
		ControlPlaneURL:     strings.TrimRight(env("GATEWAY_CONTROL_PLANE_URL", "http://127.0.0.1:8081"), "/"),
		SnapshotTimeout:     envDuration("GATEWAY_SNAPSHOT_TIMEOUT", 2*time.Second),
		JudgeBackend:        env("GATEWAY_JUDGE_BACKEND", "rule"),
		PipelineURL:         strings.TrimRight(env("GATEWAY_PIPELINE_URL", "http://127.0.0.1:8090"), "/"),
		PipelineTimeout:     envDuration("GATEWAY_PIPELINE_TIMEOUT", 10*time.Second),
		APIKey:              env("GATEWAY_API_KEY", ""),
		RateLimitPerMin:     envInt("GATEWAY_RATE_LIMIT_PER_MIN", 600),
		AuthRequired:        envBool("GATEWAY_AUTH_REQUIRED", false),
		JWTSecret:           env("GATEWAY_JWT_SECRET", ""),
		QuotaEnabled:        envBool("GATEWAY_QUOTA_ENABLED", true),
		WorkerEnabled:       envBool("GATEWAY_WORKER_ENABLED", true),
		WorkerGroup:         env("GATEWAY_WORKER_GROUP", "judge-workers"),
		WorkerName:          env("GATEWAY_WORKER_NAME", hostname()),
		MaxAttempts:         envInt("GATEWAY_MAX_ATTEMPTS", 3),
		StubModelVersion:    env("GATEWAY_STUB_MODEL_VERSION", "stub-m1-v0"),
	}
	if cfg.DefaultChunkSize < cfg.MinChunkSize {
		return nil, fmt.Errorf("GATEWAY_CHUNK_SIZE (%d) must be >= GATEWAY_MIN_CHUNK_SIZE (%d)",
			cfg.DefaultChunkSize, cfg.MinChunkSize)
	}
	if cfg.MaxAssetBytes <= 0 || cfg.MaxChunks <= 0 {
		return nil, fmt.Errorf("invalid limits: max_asset_bytes=%d max_chunks=%d", cfg.MaxAssetBytes, cfg.MaxChunks)
	}
	return cfg, nil
}

func hostname() string {
	h, err := os.Hostname()
	if err != nil || h == "" {
		return "gateway"
	}
	return h
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envInt64(key string, def int64) int64 {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			return n
		}
	}
	return def
}

func envBool(key string, def bool) bool {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}

func envDuration(key string, def time.Duration) time.Duration {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return def
}
