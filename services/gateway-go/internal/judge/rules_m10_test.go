package judge

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/pipeline"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
)

func m10Pipeline(t *testing.T, captionConflict bool, consistencyCalls *int32) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/scan":
			_ = json.NewEncoder(w).Encode(pipeline.ScanResult{Status: "clean", Engine: "eicar"})
		case "/v1/parse":
			_ = json.NewEncoder(w).Encode(pipeline.ParsedAsset{
				AssetType: "image",
				Text:      "活动价 199 元",
				Lines:     []pipeline.ParseElement{{Text: "活动价 199 元", BBox: []float64{10, 20, 200, 60}, Role: "body"}},
				Degraded:  false,
				Meta:      map[string]any{"engine": "fake-ocr"},
			})
		case "/v1/consistency":
			atomic.AddInt32(consistencyCalls, 1)
			if !captionConflict {
				_ = json.NewEncoder(w).Encode(pipeline.ConsistencyResult{})
				return
			}
			_ = json.NewEncoder(w).Encode(pipeline.ConsistencyResult{
				Conflicts: []pipeline.ConsistencyConflict{{
					Attribute: "price", CaptionValue: "99", ImageValue: "199",
					EvidenceCaption: "到手价 99 元", EvidenceImage: "活动价 199 元",
					Confidence: 0.95, Source: "rule", SpanImage: []int{0, 5},
					BBox: []float64{10, 20, 200, 60},
				}},
				Engine: "rules",
			})
		case "/v1/judge":
			_ = json.NewEncoder(w).Encode(pipeline.JudgeResponse{
				Decision: "pass", Reason: "no keyword hit", Violations: []pipeline.Violation{},
				JudgeVersion: "rule-m3-v1", IndexVersion: "idx",
			})
		default:
			http.NotFound(w, r)
		}
	}))
}

func newM10Judge(t *testing.T, serverURL string, caption string, calls *int32) (*RuleJudge, *model.Asset, *store.Memory) {
	t.Helper()
	objs := objectstore.NewMemory()
	asset := &model.Asset{
		ID: "asset-m10", TenantID: "demo-platform", Type: "image",
		Status: model.AssetStatusQueued, ObjectKey: "assets/demo-platform/asset-m10/original",
		Caption: caption, Filename: "creative.png",
	}
	_ = objs.PutChunk(context.Background(), asset.ObjectKey, strings.NewReader("img"), 3, "image/png")
	mem := store.NewMemory()
	session := &model.UploadSession{ID: "s-m10", AssetID: asset.ID, TenantID: asset.TenantID,
		ChunkSize: 1024, TotalChunks: 1, Status: "uploading", CreatedAt: time.Now()}
	if err := mem.CreateUpload(context.Background(), asset, session); err != nil {
		t.Fatalf("create upload: %v", err)
	}
	judge := NewRuleJudge(pipeline.New(serverURL, time.Second, testLogger()), objs, nil, testLogger(), mem)
	return judge, asset, mem
}

func TestRuleJudgeConsistencyConflictRoutesToManual(t *testing.T) {
	var calls int32
	server := m10Pipeline(t, true, &calls)
	defer server.Close()
	judge, asset, mem := newM10Judge(t, server.URL, "到手价 99 元", &calls)

	judgment, err := judge.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if calls != 1 {
		t.Fatalf("expected one consistency call, got %d", calls)
	}
	if judgment.Decision != "manual" {
		t.Fatalf("consistency conflict must downgrade pass to manual, got %s", judgment.Decision)
	}
	found := false
	for _, violation := range judgment.Violations {
		if violation.Family == "cross_modal_consistency" && violation.RuleID == "CONSISTENCY-PRICE" {
			found = true
			if len(violation.BBox) != 4 || violation.BBox[0] != 10 {
				t.Fatalf("conflict bbox not mapped: %+v", violation.BBox)
			}
			if !strings.Contains(violation.EvidenceText, "到手价 99 元") ||
				!strings.Contains(violation.EvidenceText, "活动价 199 元") {
				t.Fatalf("conflict must carry both evidence snippets, got %q", violation.EvidenceText)
			}
		}
	}
	if !found {
		t.Fatalf("expected CONSISTENCY-PRICE violation, got %+v", judgment.Violations)
	}
	if !strings.Contains(judgment.Reason, "图文一致性") {
		t.Fatalf("reason must explain the consistency conflict, got %q", judgment.Reason)
	}
	stored, err := mem.GetAsset(context.Background(), asset.TenantID, asset.ID)
	if err != nil {
		t.Fatalf("get asset: %v", err)
	}
	if !strings.Contains(stored.ParseMeta, "consistency") || !strings.Contains(stored.ParseMeta, "活动价 199 元") {
		t.Fatalf("parse_meta must record consistency evidence, got %q", stored.ParseMeta)
	}
}

func TestRuleJudgeSkipsConsistencyWithoutCaption(t *testing.T) {
	var calls int32
	server := m10Pipeline(t, true, &calls)
	defer server.Close()
	judge, asset, _ := newM10Judge(t, server.URL, "", &calls)

	judgment, err := judge.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if calls != 0 {
		t.Fatalf("consistency must not be called without a caption, got %d calls", calls)
	}
	if judgment.Decision != "pass" {
		t.Fatalf("expected pass without consistency, got %s", judgment.Decision)
	}
}
