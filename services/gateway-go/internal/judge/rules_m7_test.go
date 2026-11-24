package judge

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/pipeline"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
)

// m7Pipeline serves /v1/scan, /v1/parse and /v1/judge for the multimodal judge tests.
func m7Pipeline(t *testing.T, parseAsset pipeline.ParsedAsset, scan pipeline.ScanResult) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/scan":
			_ = json.NewEncoder(w).Encode(scan)
		case "/v1/parse":
			if err := r.ParseMultipartForm(8 << 20); err != nil {
				http.Error(w, "bad multipart", http.StatusBadRequest)
				return
			}
			if parseAsset.AssetType == "landing_page" && r.FormValue("source_url") == "" {
				http.Error(w, "missing source_url", http.StatusBadRequest)
				return
			}
			_ = json.NewEncoder(w).Encode(parseAsset)
		case "/v1/judge":
			var request map[string]string
			_ = json.NewDecoder(r.Body).Decode(&request)
			decision := "pass"
			violations := []pipeline.Violation{}
			if strings.Contains(request["text"], "最低价") {
				decision = "block"
				violations = []pipeline.Violation{{
					RuleID: "ADV-LAW-ART9-3", Canonical: "最低价", Span: []int{0, 5}, Evidence: "最低价",
					ClauseID: "ADV-LAW-ART9-ITEM3", Confidence: 0.9,
				}}
			}
			_ = json.NewEncoder(w).Encode(pipeline.JudgeResponse{
				Decision: decision, Reason: "test", Violations: violations,
				JudgeVersion: "rule-m3-v1", IndexVersion: "idx",
			})
		default:
			http.NotFound(w, r)
		}
	}))
}

func newJudgeStore(t *testing.T, asset *model.Asset) *store.Memory {
	t.Helper()
	mem := store.NewMemory()
	session := &model.UploadSession{ID: "s1", AssetID: asset.ID, TenantID: asset.TenantID,
		ChunkSize: 1024, TotalChunks: 1, Status: "uploading", CreatedAt: time.Now()}
	if err := mem.CreateUpload(context.Background(), asset, session); err != nil {
		t.Fatalf("create upload: %v", err)
	}
	return mem
}

func TestRuleJudgeImageParsesOCRAndAttachesBBox(t *testing.T) {
	clean := pipeline.ScanResult{Status: "clean", Engine: "eicar"}
	parsed := pipeline.ParsedAsset{
		AssetType: "image", Text: "全网最低价", Degraded: false,
		Scan:  &clean,
		Lines: []pipeline.ParseElement{{Text: "全网最低价", BBox: []float64{10, 10, 200, 60}, Role: "title"}},
		Meta:  map[string]any{"engine": "fake-ocr"},
	}
	server := m7Pipeline(t, parsed, clean)
	defer server.Close()

	objs := objectstore.NewMemory()
	asset := textAsset("image-bytes")
	asset.Type = "image"
	_ = objs.PutChunk(context.Background(), asset.ObjectKey, strings.NewReader("image-bytes"), 11, "image/png")
	mem := newJudgeStore(t, asset)

	judge := NewRuleJudge(pipeline.New(server.URL, time.Second, testLogger()), objs, nil, testLogger(), mem)
	judgment, err := judge.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "block" {
		t.Fatalf("expected block from parsed OCR text, got %s (%s)", judgment.Decision, judgment.Reason)
	}
	if len(judgment.Violations) != 1 {
		t.Fatalf("expected 1 violation, got %d", len(judgment.Violations))
	}
	bbox := judgment.Violations[0].BBox
	if len(bbox) != 4 || bbox[0] != 10 || bbox[3] != 60 {
		t.Fatalf("expected OCR bbox on violation, got %+v", bbox)
	}
	stored, err := mem.GetAsset(context.Background(), asset.TenantID, asset.ID)
	if err != nil {
		t.Fatalf("get asset: %v", err)
	}
	if stored.ParseStatus != "parsed" || !strings.Contains(stored.ParseMeta, "fake-ocr") {
		t.Fatalf("expected parse evidence stored, got status=%q meta=%q", stored.ParseStatus, stored.ParseMeta)
	}
}

func TestRuleJudgeDegradedParseGoesManual(t *testing.T) {
	clean := pipeline.ScanResult{Status: "clean", Engine: "eicar"}
	parsed := pipeline.ParsedAsset{
		AssetType: "image", Text: "", Degraded: true,
		Warnings: []string{"OCR unavailable: rapidocr unavailable"},
		Scan:     &clean,
	}
	server := m7Pipeline(t, parsed, clean)
	defer server.Close()

	objs := objectstore.NewMemory()
	asset := textAsset("img")
	asset.Type = "image"
	_ = objs.PutChunk(context.Background(), asset.ObjectKey, strings.NewReader("img"), 3, "image/png")
	mem := newJudgeStore(t, asset)
	judge := NewRuleJudge(pipeline.New(server.URL, time.Second, testLogger()), objs, nil, testLogger(), mem)
	judgment, err := judge.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "manual" || !strings.Contains(judgment.Reason, "degraded") {
		t.Fatalf("expected degraded manual, got %s / %q", judgment.Decision, judgment.Reason)
	}
	stored, _ := mem.GetAsset(context.Background(), asset.TenantID, asset.ID)
	if stored.ParseStatus != "degraded" {
		t.Fatalf("expected parse_status degraded, got %q", stored.ParseStatus)
	}
}

func TestRuleJudgeInfectedAssetQuarantined(t *testing.T) {
	infected := pipeline.ScanResult{Status: "infected", Engine: "eicar", Signature: "EICAR-Test-File"}
	server := m7Pipeline(t, pipeline.ParsedAsset{AssetType: "image", Text: "x", Scan: &infected}, infected)
	defer server.Close()

	objs := objectstore.NewMemory()
	asset := textAsset("virus-bytes")
	asset.Type = "image"
	_ = objs.PutChunk(context.Background(), asset.ObjectKey, strings.NewReader("virus-bytes"), 11, "image/png")
	mem := newJudgeStore(t, asset)
	judge := NewRuleJudge(pipeline.New(server.URL, time.Second, testLogger()), objs, nil, testLogger(), mem)
	judgment, err := judge.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "manual" || !strings.Contains(judgment.Reason, "antivirus") {
		t.Fatalf("expected antivirus quarantine, got %s / %q", judgment.Decision, judgment.Reason)
	}
}

func TestRuleJudgeLandingPage(t *testing.T) {
	clean := pipeline.ScanResult{Status: "clean", Engine: "eicar"}
	parsed := pipeline.ParsedAsset{
		AssetType: "landing_page", Text: "全网最低价 限时抢购", Degraded: false,
		Meta: map[string]any{"url": "https://example.com/promo"},
	}
	server := m7Pipeline(t, parsed, clean)
	defer server.Close()

	asset := &model.Asset{
		ID: "lp-1", TenantID: "demo-platform", Type: "landing_page",
		Status: model.AssetStatusQueued, SourceURL: "https://example.com/promo",
	}
	mem := store.NewMemory()
	if err := mem.CreateAsset(context.Background(), asset); err != nil {
		t.Fatalf("create asset: %v", err)
	}
	judge := NewRuleJudge(pipeline.New(server.URL, time.Second, testLogger()), objectstore.NewMemory(), nil, testLogger(), mem)
	judgment, err := judge.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "block" {
		t.Fatalf("expected block from landing page text, got %s (%s)", judgment.Decision, judgment.Reason)
	}
}
