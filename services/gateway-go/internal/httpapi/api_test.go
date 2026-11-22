package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Liyou2778/adguard/services/gateway/internal/config"
	"github.com/Liyou2778/adguard/services/gateway/internal/judge"
	"github.com/Liyou2778/adguard/services/gateway/internal/metrics"
	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/queue"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/Liyou2778/adguard/services/gateway/internal/worker"
)

const tenantA = "demo-platform"

type testEnv struct {
	server  *Server
	store   *store.Memory
	objects *objectstore.Memory
	queue   *queue.Memory
	worker  *worker.Worker
}

func newTestEnv(t *testing.T) *testEnv {
	t.Helper()
	cfg := &config.Config{
		MaxAssetBytes:    1 << 20,
		DefaultChunkSize: 5,
		MinChunkSize:     1,
		MaxChunks:        1000,
		RateLimitPerMin:  0,
		MaxAttempts:      3,
		StubModelVersion: "stub-test",
		WorkerEnabled:    false,
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	st := store.NewMemory()
	objs := objectstore.NewMemory()
	q := queue.NewMemory()
	m := metrics.New()
	stub := judge.NewStub(nil, cfg.StubModelVersion, logger)
	wk := worker.New(st, stub, cfg.MaxAttempts, m, logger)
	srv := New(cfg, st, objs, q, m, logger, func(context.Context) error { return nil })
	return &testEnv{server: srv, store: st, objects: objs, queue: q, worker: wk}
}

func (e *testEnv) request(t *testing.T, method, path, tenant string, body []byte) *httptest.ResponseRecorder {
	t.Helper()
	var reader io.Reader
	if body != nil {
		reader = bytes.NewReader(body)
	}
	req := httptest.NewRequest(method, path, reader)
	if tenant != "" {
		req.Header.Set("X-Tenant-Id", tenant)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	rec := httptest.NewRecorder()
	e.server.Handler().ServeHTTP(rec, req)
	return rec
}

type createResponse struct {
	AssetID     string `json:"asset_id"`
	UploadID    string `json:"upload_id"`
	ChunkSize   int    `json:"chunk_size"`
	TotalChunks int    `json:"total_chunks"`
}

func (e *testEnv) createAsset(t *testing.T, tenant, content string, chunkSize int) createResponse {
	t.Helper()
	body := map[string]any{
		"filename":   "creative.png",
		"size_bytes": len(content),
		"mime_type":  "image/png",
		"type":       "image",
		"chunk_size": chunkSize,
	}
	raw, _ := json.Marshal(body)
	rec := e.request(t, http.MethodPost, "/api/v1/assets", tenant, raw)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create asset: expected 201, got %d body=%s", rec.Code, rec.Body.String())
	}
	var resp createResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode create response: %v", err)
	}
	return resp
}

func (e *testEnv) uploadChunks(t *testing.T, tenant, assetID, content string, chunkSize, total int) {
	t.Helper()
	for i := 0; i < total; i++ {
		start := i * chunkSize
		end := start + chunkSize
		if end > len(content) {
			end = len(content)
		}
		rec := e.request(t, http.MethodPut,
			"/api/v1/assets/"+assetID+"/chunks/"+itoa(i), tenant, []byte(content[start:end]))
		if rec.Code != http.StatusNoContent {
			t.Fatalf("upload chunk %d: expected 204, got %d body=%s", i, rec.Code, rec.Body.String())
		}
	}
}

func (e *testEnv) drainQueueAndJudge(t *testing.T) {
	t.Helper()
	for _, task := range e.queue.Drain() {
		if err := e.worker.ProcessOne(context.Background(), task); err != nil {
			t.Fatalf("worker process: %v", err)
		}
	}
}

func TestUploadToJudgmentClosedLoop(t *testing.T) {
	env := newTestEnv(t)
	content := "0123456789"

	created := env.createAsset(t, tenantA, content, 5)
	if created.TotalChunks != 2 {
		t.Fatalf("expected 2 chunks, got %d", created.TotalChunks)
	}
	env.uploadChunks(t, tenantA, created.AssetID, content, created.ChunkSize, created.TotalChunks)

	rec := env.request(t, http.MethodPost, "/api/v1/assets/"+created.AssetID+"/complete", tenantA, nil)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("complete: expected 202, got %d body=%s", rec.Code, rec.Body.String())
	}
	env.drainQueueAndJudge(t)

	rec = env.request(t, http.MethodGet, "/api/v1/assets/"+created.AssetID, tenantA, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("get asset: expected 200, got %d", rec.Code)
	}
	var asset struct {
		Status           string `json:"status"`
		Digest           string `json:"digest"`
		SizeBytes        int64  `json:"size_bytes"`
		LatestJudgmentID string `json:"latest_judgment_id"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &asset); err != nil {
		t.Fatalf("decode asset: %v", err)
	}
	if asset.Status != model.AssetStatusJudged {
		t.Fatalf("expected judged, got %s", asset.Status)
	}
	if len(asset.Digest) != 64 {
		t.Fatalf("expected sha256 digest, got %q", asset.Digest)
	}
	if asset.SizeBytes != int64(len(content)) {
		t.Fatalf("expected size %d, got %d", len(content), asset.SizeBytes)
	}
	if asset.LatestJudgmentID == "" {
		t.Fatal("expected latest_judgment_id")
	}

	rec = env.request(t, http.MethodGet, "/api/v1/judgments/"+asset.LatestJudgmentID, tenantA, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("get judgment: expected 200, got %d", rec.Code)
	}
	var judgment model.Judgment
	if err := json.Unmarshal(rec.Body.Bytes(), &judgment); err != nil {
		t.Fatalf("decode judgment: %v", err)
	}
	if judgment.Decision != "manual" {
		t.Fatalf("expected manual decision from stub judge, got %s", judgment.Decision)
	}
	if judgment.Violations == nil {
		t.Fatal("violations must serialise as [] and never null")
	}
	if !strings.Contains(judgment.Reason, "M1 stub judge") {
		t.Fatalf("expected stub reason, got %q", judgment.Reason)
	}

	// Object must be merged and chunks cleaned up.
	if _, ok := env.objects.Get("assets/" + tenantA + "/" + created.AssetID + "/original"); !ok {
		t.Fatal("merged object not found in object store")
	}
	if n := env.objects.HasPrefixCount("tmp/"); n != 0 {
		t.Fatalf("expected temp chunks to be cleaned up, %d left", n)
	}

	// Download URL is issued through the presign path.
	rec = env.request(t, http.MethodGet, "/api/v1/assets/"+created.AssetID+"/download-url", tenantA, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("download-url: expected 200, got %d", rec.Code)
	}
	var dl struct {
		URL string `json:"url"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &dl)
	if !strings.HasPrefix(dl.URL, "memory://") {
		t.Fatalf("expected presigned url, got %q", dl.URL)
	}
}

func TestDuplicateContentIsDeduplicated(t *testing.T) {
	env := newTestEnv(t)
	content := "same-bytes-same-digest"

	first := env.createAsset(t, tenantA, content, 8)
	env.uploadChunks(t, tenantA, first.AssetID, content, first.ChunkSize, first.TotalChunks)
	if rec := env.request(t, http.MethodPost, "/api/v1/assets/"+first.AssetID+"/complete", tenantA, nil); rec.Code != http.StatusAccepted {
		t.Fatalf("first complete expected 202, got %d", rec.Code)
	}

	second := env.createAsset(t, tenantA, content, 8)
	env.uploadChunks(t, tenantA, second.AssetID, content, second.ChunkSize, second.TotalChunks)
	rec := env.request(t, http.MethodPost, "/api/v1/assets/"+second.AssetID+"/complete", tenantA, nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("duplicate complete expected 200, got %d body=%s", rec.Code, rec.Body.String())
	}
	var dedup struct {
		AssetID string `json:"asset_id"`
		Deduped bool   `json:"deduped"`
	}
	_ = json.Unmarshal(rec.Body.Bytes(), &dedup)
	if !dedup.Deduped || dedup.AssetID != first.AssetID {
		t.Fatalf("expected dedupe to first asset %s, got %+v", first.AssetID, dedup)
	}

	secondAsset, err := env.store.GetAsset(context.Background(), tenantA, second.AssetID)
	if err != nil {
		t.Fatalf("load second asset: %v", err)
	}
	if secondAsset.Status != model.AssetStatusDuplicate {
		t.Fatalf("expected duplicate status, got %s", secondAsset.Status)
	}
}

func TestTenantIsolationAndValidation(t *testing.T) {
	env := newTestEnv(t)
	content := "tenant-a-content"
	created := env.createAsset(t, tenantA, content, 64)
	env.uploadChunks(t, tenantA, created.AssetID, content, created.ChunkSize, created.TotalChunks)
	env.request(t, http.MethodPost, "/api/v1/assets/"+created.AssetID+"/complete", tenantA, nil)
	env.drainQueueAndJudge(t)

	// Another tenant must get 404, never a hint that the asset exists.
	rec := env.request(t, http.MethodGet, "/api/v1/assets/"+created.AssetID, "other-tenant", nil)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("cross-tenant read: expected 404, got %d", rec.Code)
	}

	// Missing / invalid tenant header.
	rec = env.request(t, http.MethodGet, "/api/v1/assets/"+created.AssetID, "", nil)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "MISSING_TENANT") {
		t.Fatalf("missing tenant: expected 400 MISSING_TENANT, got %d %s", rec.Code, rec.Body.String())
	}
	rec = env.request(t, http.MethodGet, "/api/v1/assets/x", "Bad Tenant!", nil)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "INVALID_TENANT") {
		t.Fatalf("invalid tenant: expected 400 INVALID_TENANT, got %d %s", rec.Code, rec.Body.String())
	}

	// Invalid asset type / oversize.
	raw, _ := json.Marshal(map[string]any{"filename": "a.png", "size_bytes": 10, "type": "audio"})
	rec = env.request(t, http.MethodPost, "/api/v1/assets", tenantA, raw)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "INVALID_ASSET_TYPE") {
		t.Fatalf("invalid type: expected 400, got %d %s", rec.Code, rec.Body.String())
	}
	raw, _ = json.Marshal(map[string]any{"filename": "a.png", "size_bytes": 2 << 20, "type": "image"})
	rec = env.request(t, http.MethodPost, "/api/v1/assets", tenantA, raw)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversize: expected 413, got %d", rec.Code)
	}
}

func TestIncompleteUploadAndChunkValidation(t *testing.T) {
	env := newTestEnv(t)
	content := "0123456789"
	created := env.createAsset(t, tenantA, content, 5)

	// Only the first chunk: complete must be rejected.
	e := env.request(t, http.MethodPut, "/api/v1/assets/"+created.AssetID+"/chunks/0", tenantA, []byte("01234"))
	if e.Code != http.StatusNoContent {
		t.Fatalf("chunk 0: expected 204, got %d", e.Code)
	}
	rec := env.request(t, http.MethodPost, "/api/v1/assets/"+created.AssetID+"/complete", tenantA, nil)
	if rec.Code != http.StatusConflict || !strings.Contains(rec.Body.String(), "INCOMPLETE_UPLOAD") {
		t.Fatalf("incomplete upload: expected 409, got %d %s", rec.Code, rec.Body.String())
	}

	// Wrong chunk size (chunk 1 is the last chunk: 5 bytes expected).
	rec = env.request(t, http.MethodPut, "/api/v1/assets/"+created.AssetID+"/chunks/1", tenantA, []byte("abc"))
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "INVALID_CHUNK_SIZE") {
		t.Fatalf("bad chunk size: expected 400, got %d %s", rec.Code, rec.Body.String())
	}

	// Out of range index.
	rec = env.request(t, http.MethodPut, "/api/v1/assets/"+created.AssetID+"/chunks/9", tenantA, []byte("abcde"))
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "INVALID_CHUNK_INDEX") {
		t.Fatalf("bad index: expected 400, got %d %s", rec.Code, rec.Body.String())
	}
}

func TestWorkerIsIdempotentOnRedelivery(t *testing.T) {
	env := newTestEnv(t)
	content := "idempotent-judging"
	created := env.createAsset(t, tenantA, content, 64)
	env.uploadChunks(t, tenantA, created.AssetID, content, created.ChunkSize, created.TotalChunks)
	env.request(t, http.MethodPost, "/api/v1/assets/"+created.AssetID+"/complete", tenantA, nil)

	task := model.AuditTask{AssetID: created.AssetID, TenantID: tenantA}
	for i := 0; i < 2; i++ {
		if err := env.worker.ProcessOne(context.Background(), task); err != nil {
			t.Fatalf("process iteration %d: %v", i, err)
		}
	}
	judgments := env.store.JudgmentsSorted()
	if len(judgments) != 1 {
		t.Fatalf("expected exactly 1 judgment after redelivery, got %d", len(judgments))
	}
}

func TestHealthReadinessAndMetrics(t *testing.T) {
	env := newTestEnv(t)
	for _, path := range []string{"/healthz", "/readyz"} {
		rec := env.request(t, http.MethodGet, path, "", nil)
		if rec.Code != http.StatusOK {
			t.Fatalf("%s: expected 200, got %d", path, rec.Code)
		}
	}
	rec := env.request(t, http.MethodGet, "/metrics", "", nil)
	if rec.Code != http.StatusOK {
		t.Fatalf("/metrics: expected 200, got %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "adguard_http_requests_total") {
		t.Fatal("expected gateway metrics in /metrics output")
	}
}

func TestSanitiseFilename(t *testing.T) {
	cases := map[string]string{
		"../../etc/passwd": "passwd",
		"C:\\temp\\a.png":  "a.png",
		"  广告 素材.png  ":    "广告 素材.png",
		"":                 "unnamed",
		"..":               "unnamed",
	}
	for input, want := range cases {
		if got := sanitiseFilename(input); got != want {
			t.Fatalf("sanitiseFilename(%q) = %q, want %q", input, got, want)
		}
	}
}
