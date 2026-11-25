package quota

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/store"
)

type fakeRedis struct {
	mu     sync.Mutex
	counts map[string]int64
	fail   bool
}

func (f *fakeRedis) funcs() Funcs {
	return Funcs{
		Incr: func(_ context.Context, key string, value int64) (int64, error) {
			f.mu.Lock()
			defer f.mu.Unlock()
			if f.fail {
				return 0, context.DeadlineExceeded
			}
			if f.counts == nil {
				f.counts = map[string]int64{}
			}
			f.counts[key] += value
			return f.counts[key], nil
		},
		Decr: func(_ context.Context, key string, value int64) (int64, error) {
			f.mu.Lock()
			defer f.mu.Unlock()
			f.counts[key] -= value
			return f.counts[key], nil
		},
		Expire: func(context.Context, string, time.Duration) error { return nil },
	}
}

func testLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func TestReserveAllowsAndWeighsByType(t *testing.T) {
	limiter := NewWithFuncs(store.NewMemory(), true, testLogger(), (&fakeRedis{}).funcs(), nil)
	reservation := limiter.Reserve(context.Background(), "demo-platform", "video")
	if !reservation.Allowed || reservation.Weight != 5 {
		t.Fatalf("video should weigh 5 and be allowed: %+v", reservation)
	}
}

func TestReserveRejectsWhenQuotaExceededAndRollsBack(t *testing.T) {
	fake := &fakeRedis{}
	limiter := NewWithFuncs(store.NewMemory(), true, testLogger(), fake.funcs(),
		func() time.Time { return time.Date(2026, 11, 25, 10, 0, 0, 0, time.UTC) })
	// other-tenant has a quota of 10000; seed the counter so a video (weight 5) exceeds it.
	fake.counts = map[string]int64{"adguard:quota:other-tenant:20261125": 9998}
	reservation := limiter.Reserve(context.Background(), "other-tenant", "video")
	if reservation.Allowed {
		t.Fatalf("expected quota rejection, got %+v", reservation)
	}
	if reservation.Reason != ReasonExceeded || reservation.RetryAfter <= 0 {
		t.Fatalf("unexpected rejection: %+v", reservation)
	}
	if got := fake.counts["adguard:quota:other-tenant:20261125"]; got != 9998 {
		t.Fatalf("rejection must roll back the reserved weight, got counter=%d", got)
	}
}

func TestReserveFailsOpenWhenRedisDown(t *testing.T) {
	fake := &fakeRedis{fail: true}
	limiter := NewWithFuncs(store.NewMemory(), true, testLogger(), fake.funcs(), nil)
	reservation := limiter.Reserve(context.Background(), "demo-platform", "text")
	if !reservation.Allowed {
		t.Fatalf("redis failure must fail open, got %+v", reservation)
	}
}

func TestReserveUnknownTenant(t *testing.T) {
	limiter := NewWithFuncs(store.NewMemory(), true, testLogger(), (&fakeRedis{}).funcs(), nil)
	reservation := limiter.Reserve(context.Background(), "no-such-tenant", "text")
	if reservation.Allowed || reservation.Reason != ReasonTenantMissing {
		t.Fatalf("unknown tenant must be rejected: %+v", reservation)
	}
}

func TestDisabledLimiterAllowsEverything(t *testing.T) {
	limiter := NewWithFuncs(store.NewMemory(), false, testLogger(), (&fakeRedis{}).funcs(), nil)
	if !limiter.Reserve(context.Background(), "no-such-tenant", "video").Allowed {
		t.Fatal("disabled limiter must allow")
	}
}
