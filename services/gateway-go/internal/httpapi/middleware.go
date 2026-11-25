package httpapi

import (
	"context"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/auth"
	"github.com/Liyou2778/adguard/services/gateway/internal/config"
	"github.com/Liyou2778/adguard/services/gateway/internal/metrics"
	"github.com/google/uuid"
)

type contextKey string

const (
	ctxTenant    contextKey = "tenant"
	ctxRequestID contextKey = "request_id"
)

var tenantPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{2,63}$`)

func tenantFrom(r *http.Request) string {
	tenant, _ := r.Context().Value(ctxTenant).(string)
	return tenant
}

func requestIDFrom(r *http.Request) string {
	id, _ := r.Context().Value(ctxRequestID).(string)
	return id
}

// statusRecorder captures the status code for logs and metrics.
type statusRecorder struct {
	http.ResponseWriter
	status int
	bytes  int
}

func (w *statusRecorder) WriteHeader(status int) {
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *statusRecorder) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.status = http.StatusOK
	}
	n, err := w.ResponseWriter.Write(b)
	w.bytes += n
	return n, err
}

func requestIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-Id")
		if id == "" || len(id) > 128 {
			id = uuid.NewString()
		}
		w.Header().Set("X-Request-Id", id)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxRequestID, id)))
	})
}

func recoverMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rec := recover(); rec != nil {
					logger.Error("panic recovered", "panic", rec, "path", r.URL.Path,
						"request_id", requestIDFrom(r))
					writeError(w, r, http.StatusInternalServerError, "INTERNAL_ERROR",
						"internal error, see server logs", nil)
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

func accessLogMiddleware(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			started := time.Now()
			rec := &statusRecorder{ResponseWriter: w}
			next.ServeHTTP(rec, r)
			if rec.status == 0 {
				rec.status = http.StatusOK
			}
			logger.Info("http",
				"method", r.Method,
				"path", r.URL.Path,
				"status", rec.status,
				"duration_ms", time.Since(started).Milliseconds(),
				"bytes", rec.bytes,
				"tenant", tenantFrom(r),
				"request_id", requestIDFrom(r))
		})
	}
}

func metricsMiddleware(m *metrics.Metrics) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			started := time.Now()
			rec := &statusRecorder{ResponseWriter: w}
			next.ServeHTTP(rec, r)
			status := rec.status
			if status == 0 {
				status = http.StatusOK
			}
			route := r.URL.Path // low-cardinality route patterns are nice-to-have; path is a safe M1 default
			m.HTTPRequests.WithLabelValues(r.Method, route, http.StatusText(status)).Inc()
			m.HTTPDuration.WithLabelValues(r.Method, route).Observe(time.Since(started).Seconds())
		})
	}
}

func tenantMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		tenant := strings.TrimSpace(r.Header.Get("X-Tenant-Id"))
		if tenant == "" {
			writeError(w, r, http.StatusBadRequest, "MISSING_TENANT", "X-Tenant-Id header is required", nil)
			return
		}
		if !tenantPattern.MatchString(tenant) {
			writeError(w, r, http.StatusBadRequest, "INVALID_TENANT",
				"tenant id must match ^[a-z0-9][a-z0-9_-]{2,63}$", nil)
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxTenant, tenant)))
	})
}

func apiKeyMiddleware(apiKey string) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		if apiKey == "" {
			return next
		}
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("X-API-Key") != apiKey {
				writeError(w, r, http.StatusUnauthorized, "UNAUTHORIZED", "invalid or missing X-API-Key", nil)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

type rateWindow struct {
	windowStart time.Time
	count       int
}

// rateLimiter is a per-tenant fixed-window limiter (M1: single instance).
// A shared Redis limiter is the M6 upgrade once the gateway runs multiple replicas.
type rateLimiter struct {
	mu     sync.Mutex
	limit  int
	window map[string]*rateWindow
}

func newRateLimiter(perMinute int) *rateLimiter {
	if perMinute <= 0 {
		perMinute = 600
	}
	return &rateLimiter{limit: perMinute, window: map[string]*rateWindow{}}
}

func (l *rateLimiter) allow(key string) (bool, int) {
	if l == nil || l.limit <= 0 {
		return true, 0
	}
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	win, ok := l.window[key]
	if !ok || now.Sub(win.windowStart) >= time.Minute {
		l.window[key] = &rateWindow{windowStart: now, count: 1}
		if len(l.window) > 100_000 {
			for k, v := range l.window {
				if now.Sub(v.windowStart) >= time.Minute {
					delete(l.window, k)
				}
			}
		}
		return true, 0
	}
	if win.count >= l.limit {
		retry := int(time.Minute.Seconds() - now.Sub(win.windowStart).Seconds())
		if retry < 1 {
			retry = 1
		}
		return false, retry
	}
	win.count++
	return true, 0
}

func rateLimitMiddleware(l *rateLimiter) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			allowed, retryAfter := l.allow(tenantFrom(r))
			if !allowed {
				w.Header().Set("Retry-After", itoa(retryAfter))
				writeError(w, r, http.StatusTooManyRequests, "RATE_LIMITED",
					"rate limit exceeded for this tenant", nil)
				return
			}
			next.ServeHTTP(w, r)
		})
	}
}

func itoa(n int) string {
	return strconv.Itoa(n)
}

// authMiddleware is the production identity gate (M8 hardening):
//   - user JWT from the control plane (tenant users only; admin/reviewer tokens are rejected
//     for asset APIs), or
//   - the service X-API-Key (automation/smoke) with an explicit X-Tenant-Id.
//
// When neither auth mode is configured (local development), the legacy tenant header is
// accepted so existing tooling keeps working; ECS sets GATEWAY_AUTH_REQUIRED=true.
func authMiddleware(cfg *config.Config) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			authorization := r.Header.Get("Authorization")
			if strings.HasPrefix(authorization, "Bearer ") {
				claims, err := auth.Verify(strings.TrimSpace(authorization[len("Bearer "):]), cfg.JWTSecret)
				if err != nil {
					writeError(w, r, http.StatusUnauthorized, "UNAUTHORIZED",
						"invalid or expired access token", nil)
					return
				}
				if !claims.TenantUser() {
					writeError(w, r, http.StatusForbidden, "TENANT_REQUIRED",
						"asset APIs require a tenant (owner/member) account", nil)
					return
				}
				tenant := strings.TrimSpace(claims.TenantID)
				if !tenantPattern.MatchString(tenant) {
					writeError(w, r, http.StatusUnauthorized, "INVALID_TENANT", "token tenant is invalid", nil)
					return
				}
				next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxTenant, tenant)))
				return
			}
			if cfg.APIKey != "" && r.Header.Get("X-API-Key") == cfg.APIKey {
				tenant := strings.TrimSpace(r.Header.Get("X-Tenant-Id"))
				if !tenantPattern.MatchString(tenant) {
					writeError(w, r, http.StatusBadRequest, "MISSING_TENANT",
						"X-Tenant-Id header is required with the service API key", nil)
					return
				}
				next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxTenant, tenant)))
				return
			}
			if !cfg.AuthRequired && cfg.APIKey == "" {
				// Local development: legacy tenant header mode.
				tenant := strings.TrimSpace(r.Header.Get("X-Tenant-Id"))
				if tenant == "" {
					writeError(w, r, http.StatusBadRequest, "MISSING_TENANT",
						"X-Tenant-Id header is required", nil)
					return
				}
				if !tenantPattern.MatchString(tenant) {
					writeError(w, r, http.StatusBadRequest, "INVALID_TENANT",
						"tenant id must match ^[a-z0-9][a-z0-9_-]{2,63}$", nil)
					return
				}
				next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), ctxTenant, tenant)))
				return
			}
			writeError(w, r, http.StatusUnauthorized, "UNAUTHORIZED",
				"authentication required (Bearer token or X-API-Key)", nil)
		})
	}
}
