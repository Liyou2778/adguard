package httpapi

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/config"
	"github.com/Liyou2778/adguard/services/gateway/internal/judge"
	"github.com/Liyou2778/adguard/services/gateway/internal/metrics"
	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/queue"
	"github.com/Liyou2778/adguard/services/gateway/internal/quota"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/Liyou2778/adguard/services/gateway/internal/worker"
)

func baseConfig() *config.Config {
	return &config.Config{
		MaxAssetBytes:    1 << 20,
		DefaultChunkSize: 5,
		MinChunkSize:     1,
		MaxChunks:        1000,
		MaxAttempts:      3,
		StubModelVersion: "stub-test",
	}
}

func newEnvWithConfig(t *testing.T, cfg *config.Config, limiter *quota.Quota) *testEnv {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	st := store.NewMemory()
	objs := objectstore.NewMemory()
	q := queue.NewMemory()
	m := metrics.New()
	stub := judge.NewStub(nil, cfg.StubModelVersion, logger)
	wk := worker.New(st, stub, cfg.MaxAttempts, m, logger)
	srv := New(cfg, st, objs, q, m, logger, func(context.Context) error { return nil }, limiter)
	return &testEnv{server: srv, store: st, objects: objs, queue: q, worker: wk}
}

func signJWT(t *testing.T, secret string, claims map[string]any) string {
	t.Helper()
	header, _ := json.Marshal(map[string]any{"alg": "HS256", "typ": "JWT"})
	payload, _ := json.Marshal(claims)
	headerPart := base64.RawURLEncoding.EncodeToString(header)
	payloadPart := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(headerPart + "." + payloadPart))
	return headerPart + "." + payloadPart + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func TestAPIKeyAndJWTAuthModes(t *testing.T) {
	cfg := baseConfig()
	cfg.AuthRequired = true
	cfg.APIKey = "svc-key"
	cfg.JWTSecret = "jwt-secret"
	env := newEnvWithConfig(t, cfg, nil)

	// 1) no credentials -> 401
	rec := env.request(t, http.MethodGet, "/api/v1/usage", "", nil)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without credentials, got %d", rec.Code)
	}

	// 2) service API key + tenant header -> 200
	req := newRequest(t, http.MethodGet, "/api/v1/usage", "")
	req.Header.Set("X-API-Key", "svc-key")
	req.Header.Set("X-Tenant-Id", tenantA)
	rec = serve(env, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 with service key, got %d body=%s", rec.Code, rec.Body.String())
	}

	// 3) tenant user JWT -> 200, tenant scoped by token (no tenant header)
	userToken := signJWT(t, "jwt-secret", map[string]any{
		"sub": "user-1", "tenant_id": tenantA, "username": "merchant",
		"role": "owner", "exp": time.Now().Add(time.Hour).Unix(),
	})
	req = newRequest(t, http.MethodGet, "/api/v1/usage", "")
	req.Header.Set("Authorization", "Bearer "+userToken)
	rec = serve(env, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200 with user token, got %d body=%s", rec.Code, rec.Body.String())
	}
	var summary model.UsageSummary
	if err := json.Unmarshal(rec.Body.Bytes(), &summary); err != nil {
		t.Fatalf("decode usage: %v", err)
	}
	if summary.TenantID != tenantA {
		t.Fatalf("usage must be scoped to token tenant, got %q", summary.TenantID)
	}

	// 4) admin token cannot use tenant asset APIs
	adminToken := signJWT(t, "jwt-secret", map[string]any{
		"sub": "admin-1", "username": "admin", "role": "admin",
		"exp": time.Now().Add(time.Hour).Unix(),
	})
	req = newRequest(t, http.MethodGet, "/api/v1/usage", "")
	req.Header.Set("Authorization", "Bearer "+adminToken)
	rec = serve(env, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for admin token, got %d", rec.Code)
	}

	// 5) invalid token -> 401
	req = newRequest(t, http.MethodGet, "/api/v1/usage", "")
	req.Header.Set("Authorization", "Bearer not-a-token")
	rec = serve(env, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 for invalid token, got %d", rec.Code)
	}
}

func TestQuotaExceededReturns429(t *testing.T) {
	cfg := baseConfig()
	funcs := quota.Funcs{
		Incr:   func(context.Context, string, int64) (int64, error) { return 100001, nil },
		Decr:   func(context.Context, string, int64) (int64, error) { return 0, nil },
		Expire: func(context.Context, string, time.Duration) error { return nil },
	}
	limiter := quota.NewWithFuncs(store.NewMemory(), true,
		slog.New(slog.NewTextHandler(io.Discard, nil)), funcs, nil)
	env := newEnvWithConfig(t, cfg, limiter)

	body := map[string]any{
		"filename": "a.png", "size_bytes": 10, "mime_type": "image/png",
		"type": "image", "chunk_size": 5,
	}
	raw, _ := json.Marshal(body)
	rec := env.request(t, http.MethodPost, "/api/v1/assets", tenantA, raw)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("expected 429 when quota exceeded, got %d body=%s", rec.Code, rec.Body.String())
	}
	if rec.Header().Get("Retry-After") == "" {
		t.Fatal("429 must carry Retry-After")
	}
	var envelope struct {
		Error struct {
			Code string `json:"code"`
		} `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &envelope); err != nil {
		t.Fatalf("decode error: %v", err)
	}
	if envelope.Error.Code != "QUOTA_EXCEEDED" {
		t.Fatalf("expected QUOTA_EXCEEDED, got %q", envelope.Error.Code)
	}
}

func TestJudgmentWritesUsageLedger(t *testing.T) {
	env := newTestEnv(t)
	content := "0123456789"
	created := env.createAsset(t, tenantA, content, 5)
	env.uploadChunks(t, tenantA, created.AssetID, content, created.ChunkSize, created.TotalChunks)
	rec := env.request(t, http.MethodPost, "/api/v1/assets/"+created.AssetID+"/complete", tenantA, nil)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("complete: expected 202, got %d", rec.Code)
	}
	env.drainQueueAndJudge(t)

	rec = env.request(t, http.MethodGet, "/api/v1/usage", tenantA, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("usage: expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}
	var summary model.UsageSummary
	if err := json.Unmarshal(rec.Body.Bytes(), &summary); err != nil {
		t.Fatalf("decode usage: %v", err)
	}
	if summary.Calls != 1 || summary.Weight != 2 || summary.CostMicro != 500 {
		t.Fatalf("unexpected usage summary: %+v", summary)
	}
	if len(summary.Items) != 1 || summary.Items[0].AssetType != "image" {
		t.Fatalf("expected one image usage item, got %+v", summary.Items)
	}

	// The judgment must carry the same price-list cost.
	rec = env.request(t, http.MethodGet, "/api/v1/assets/"+created.AssetID, tenantA, nil)
	var asset struct {
		LatestJudgmentID string `json:"latest_judgment_id"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &asset)
	rec = env.request(t, http.MethodGet, "/api/v1/judgments/"+asset.LatestJudgmentID, tenantA, nil)
	var judgment model.Judgment
	_ = json.Unmarshal(rec.Body.Bytes(), &judgment)
	if judgment.CostMicro != 500 {
		t.Fatalf("judgment cost_micro = %d, want 500", judgment.CostMicro)
	}
}
func newRequest(t *testing.T, method, path, tenant string) *http.Request {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	if tenant != "" {
		req.Header.Set("X-Tenant-Id", tenant)
	}
	return req
}

func serve(env *testEnv, req *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	env.server.Handler().ServeHTTP(rec, req)
	return rec
}
