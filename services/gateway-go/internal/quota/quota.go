// Package quota enforces the per-tenant daily quota (FR-13) using Redis counters.
//
// The quota unit is a weighted "point": text=1, image=2, video=5, landing_page=3, taken from
// the billing_price table (single source of truth shared with billing). Counters are keyed by
// tenant + UTC day and expire after the day boundary. Redis failures are fail-open with a
// warning: availability of judging is more important than a hard quota stop when the counter
// store is down (the ledger still records every judgment for later reconciliation).
package quota

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/redis/go-redis/v9"
)

// Reason values returned in Reservation.Reason.
const (
	ReasonAllowed        = ""
	ReasonTenantMissing  = "TENANT_NOT_FOUND"
	ReasonTenantDisabled = "TENANT_DISABLED"
	ReasonExceeded       = "QUOTA_EXCEEDED"
)

// Reservation is the decision for one asset submission.
type Reservation struct {
	Allowed    bool
	Used       int
	Limit      int
	Weight     int
	Reason     string
	RetryAfter time.Duration
}

// Funcs are the Redis operations used by the limiter (injectable for tests).
type Funcs struct {
	Incr   func(ctx context.Context, key string, value int64) (int64, error)
	Decr   func(ctx context.Context, key string, value int64) (int64, error)
	Expire func(ctx context.Context, key string, ttl time.Duration) error
}

// Quota reserves weighted points and reports tenant status.
type Quota struct {
	store   store.Store
	logger  *slog.Logger
	enabled bool
	funcs   Funcs

	mu       sync.Mutex
	cache    map[string]cachedWeight
	now      func() time.Time
	cacheTTL time.Duration
}

type cachedWeight struct {
	weight  int
	expires time.Time
}

// New builds a limiter backed by the shared Redis client.
func New(rdb *redis.Client, st store.Store, enabled bool, logger *slog.Logger) *Quota {
	if logger == nil {
		logger = slog.Default()
	}
	q := &Quota{store: st, logger: logger, enabled: enabled && rdb != nil, cache: map[string]cachedWeight{},
		now: time.Now, cacheTTL: 60 * time.Second}
	if rdb != nil {
		q.funcs = Funcs{
			Incr: func(ctx context.Context, key string, value int64) (int64, error) {
				return rdb.IncrBy(ctx, key, value).Result()
			},
			Decr: func(ctx context.Context, key string, value int64) (int64, error) {
				return rdb.DecrBy(ctx, key, value).Result()
			},
			Expire: func(ctx context.Context, key string, ttl time.Duration) error {
				return rdb.Expire(ctx, key, ttl).Err()
			},
		}
	}
	return q
}

// NewWithFuncs builds a limiter with custom clock/redis funcs (tests).
func NewWithFuncs(st store.Store, enabled bool, logger *slog.Logger, funcs Funcs,
	now func() time.Time) *Quota {
	q := New(nil, st, enabled, logger)
	q.funcs = funcs
	if now != nil {
		q.now = now
	}
	q.enabled = enabled
	return q
}

// Enabled reports whether quota enforcement is active.
func (q *Quota) Enabled() bool { return q != nil && q.enabled }

// Reserve asks for permission to submit one asset of the given type.
func (q *Quota) Reserve(ctx context.Context, tenantID, assetType string) Reservation {
	if q == nil || !q.enabled {
		return Reservation{Allowed: true}
	}
	tenant, err := q.store.GetTenant(ctx, tenantID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			return Reservation{Allowed: false, Reason: ReasonTenantMissing}
		}
		q.logger.Warn("tenant lookup failed; allowing submission", "tenant_id", tenantID, "err", err)
		return Reservation{Allowed: true}
	}
	if tenant.Status != "active" {
		return Reservation{Allowed: false, Reason: ReasonTenantDisabled}
	}
	weight := q.weight(ctx, assetType)
	limit := tenant.DailyQuota
	if limit <= 0 {
		return Reservation{Allowed: true, Weight: weight}
	}
	now := q.now().UTC()
	day := now.Format("20060102")
	key := fmt.Sprintf("adguard:quota:%s:%s", tenantID, day)
	ttl := now.Truncate(24 * time.Hour).Add(24*time.Hour + time.Hour).Sub(now)
	used, err := q.funcs.Incr(ctx, key, int64(weight))
	if err != nil {
		q.logger.Warn("redis quota counter failed; allowing submission", "tenant_id", tenantID, "err", err)
		return Reservation{Allowed: true, Weight: weight}
	}
	if err := q.funcs.Expire(ctx, key, ttl); err != nil {
		q.logger.Warn("redis quota expire failed", "tenant_id", tenantID, "err", err)
	}
	if used > int64(limit) {
		_, _ = q.funcs.Decr(ctx, key, int64(weight))
		return Reservation{Allowed: false, Used: int(used - int64(weight)), Limit: limit, Weight: weight,
			Reason: ReasonExceeded, RetryAfter: ttl}
	}
	return Reservation{Allowed: true, Used: int(used), Limit: limit, Weight: weight}
}

func (q *Quota) weight(ctx context.Context, assetType string) int {
	q.mu.Lock()
	if cached, ok := q.cache[assetType]; ok && q.now().Before(cached.expires) {
		q.mu.Unlock()
		return cached.weight
	}
	q.mu.Unlock()
	weight := 1
	pricing, err := q.store.PriceForJudge(ctx, assetType)
	if err == nil && pricing.Weight > 0 {
		weight = pricing.Weight
	}
	q.mu.Lock()
	q.cache[assetType] = cachedWeight{weight: weight, expires: q.now().Add(q.cacheTTL)}
	q.mu.Unlock()
	return weight
}

// Ensure model import is used even when callers only need the package for side effects.
var _ = model.Asset{}
