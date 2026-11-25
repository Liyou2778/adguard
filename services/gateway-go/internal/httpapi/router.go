package httpapi

import (
	"context"
	"log/slog"
	"net/http"

	"github.com/Liyou2778/adguard/services/gateway/internal/config"
	"github.com/Liyou2778/adguard/services/gateway/internal/metrics"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/queue"
	"github.com/Liyou2778/adguard/services/gateway/internal/quota"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/go-chi/chi/v5"
)

// Server wires the HTTP layer. Dependencies are interfaces so tests can run the
// whole API against in-memory adapters without Docker.
type Server struct {
	cfg     *config.Config
	store   store.Store
	objects objectstore.Store
	queue   queue.Producer
	metrics *metrics.Metrics
	logger  *slog.Logger
	ready   func(ctx context.Context) error
	limiter *rateLimiter
	quota   *quota.Quota
	handler http.Handler
}

func New(cfg *config.Config, st store.Store, objects objectstore.Store, producer queue.Producer,
	m *metrics.Metrics, logger *slog.Logger, ready func(ctx context.Context) error,
	quotas ...*quota.Quota) *Server {
	if logger == nil {
		logger = slog.Default()
	}
	if m == nil {
		m = metrics.New()
	}
	s := &Server{
		cfg:     cfg,
		store:   st,
		objects: objects,
		queue:   producer,
		metrics: m,
		logger:  logger,
		ready:   ready,
		limiter: newRateLimiter(cfg.RateLimitPerMin),
	}
	if len(quotas) > 0 {
		s.quota = quotas[0]
	}
	s.handler = s.routes()
	return s
}

func (s *Server) Handler() http.Handler { return s.handler }

func (s *Server) routes() http.Handler {
	r := chi.NewRouter()
	r.Use(requestIDMiddleware)
	r.Use(recoverMiddleware(s.logger))
	r.Use(accessLogMiddleware(s.logger))
	r.Use(metricsMiddleware(s.metrics))

	r.Get("/healthz", s.healthz)
	r.Get("/readyz", s.readyz)
	r.Method(http.MethodGet, "/metrics", s.metrics.Handler())

	r.Route("/api/v1", func(r chi.Router) {
		r.Use(authMiddleware(s.cfg))
		r.Use(rateLimitMiddleware(s.limiter))

		r.Post("/assets", s.createAsset)
		r.Put("/assets/{assetID}/chunks/{index}", s.uploadChunk)
		r.Post("/assets/{assetID}/complete", s.completeAsset)
		r.Get("/assets/{assetID}", s.getAsset)
		r.Get("/assets/{assetID}/download-url", s.downloadURL)
		r.Get("/judgments/{judgmentID}", s.getJudgment)
		r.Get("/usage", s.usage)
	})
	return r
}
