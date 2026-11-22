package judge

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/pipeline"
)

func testLogger() *slog.Logger { return slog.New(slog.NewTextHandler(io.Discard, nil)) }

func textAsset(content string) *model.Asset {
	return &model.Asset{
		ID:        "asset-1",
		TenantID:  "demo-platform",
		Type:      "text",
		Status:    model.AssetStatusQueued,
		ObjectKey: "assets/demo-platform/asset-1/original",
		SizeBytes: int64(len(content)),
	}
}

func putText(t *testing.T, objs *objectstore.Memory, asset *model.Asset, content string) {
	t.Helper()
	if err := objs.PutChunk(context.Background(), asset.ObjectKey, strings.NewReader(content),
		int64(len(content)), "text/plain"); err != nil {
		t.Fatalf("put text asset: %v", err)
	}
}

func blockPipeline(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/judge" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		var request map[string]string
		_ = json.NewDecoder(r.Body).Decode(&request)
		if !strings.Contains(request["text"], "最低价") {
			_, _ = w.Write([]byte(`{"decision":"pass","reason":"no hit","violations":[],
				"judge_version":"rule-m3-v1","retrieval_warnings":[],"dropped_stale":0,"index_version":"idx"}`))
			return
		}
		penaltyMin, penaltyMax := 200000, 1000000
		response := pipeline.JudgeResponse{
			Decision: "block",
			Reason:   "命中 1 处条款级规则违规",
			Violations: []pipeline.Violation{{
				RuleID: "ADV-LAW-ART9-3", Family: "absolute_terms", Canonical: "最低价",
				VariantKind: "canonical", Span: []int{2, 5}, Evidence: "最低价",
				ClauseID: "ADV-LAW-ART9-ITEM3", RegulationID: "ADV-LAW", Article: "第九条",
				ItemLabel: "（三）", ClauseRevision: strings.Repeat("a", 64),
				ClauseText:    "使用“国家级”、“最高级”、“最佳”等用语；",
				PenaltyLawRef: "第五十七条", PenaltyMin: &penaltyMin, PenaltyMax: &penaltyMax,
				Confidence: 0.95,
			}},
			JudgeVersion: "rule-m3-v1", IndexVersion: "idx-1",
		}
		_ = json.NewEncoder(w).Encode(response)
	}))
}

func TestRuleJudgeBlockMapsClauseEvidence(t *testing.T) {
	server := blockPipeline(t)
	defer server.Close()
	objs := objectstore.NewMemory()
	asset := textAsset("全网最低价，错过再等一年")
	putText(t, objs, asset, "全网最低价，错过再等一年")

	j := NewRuleJudge(pipeline.New(server.URL, time.Second, testLogger()), objs, nil, testLogger())
	judgment, err := j.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "block" {
		t.Fatalf("expected block, got %s (reason=%s)", judgment.Decision, judgment.Reason)
	}
	if judgment.ModelVersion != "rule-m3-v1" {
		t.Fatalf("expected rule judge version, got %s", judgment.ModelVersion)
	}
	if judgment.ID == "" {
		t.Fatal("judgment id must be assigned by the judge")
	}
	if len(judgment.Violations) != 1 {
		t.Fatalf("expected 1 violation, got %d", len(judgment.Violations))
	}
	violation := judgment.Violations[0]
	if violation.RuleID != "ADV-LAW-ART9-3" || violation.ClauseID != "ADV-LAW-ART9-ITEM3" {
		t.Fatalf("unexpected violation: %+v", violation)
	}
	if violation.Article != "第九条" || violation.ItemLabel != "（三）" {
		t.Fatalf("expected clause fields, got %+v", violation)
	}
	if violation.PenaltyMin == nil || *violation.PenaltyMin != 200000 {
		t.Fatalf("expected penalty min, got %+v", violation.PenaltyMin)
	}
	if violation.SpanStart == nil || *violation.SpanStart != 2 || *violation.SpanEnd != 5 {
		t.Fatalf("unexpected span: %+v", violation)
	}
	if len(violation.ClauseRevision) != 64 {
		t.Fatalf("expected clause revision, got %q", violation.ClauseRevision)
	}
}

func TestRuleJudgePassesCleanText(t *testing.T) {
	server := blockPipeline(t)
	defer server.Close()
	objs := objectstore.NewMemory()
	asset := textAsset("本店商品支持七天无理由退换")
	putText(t, objs, asset, "本店商品支持七天无理由退换")

	j := NewRuleJudge(pipeline.New(server.URL, time.Second, testLogger()), objs, nil, testLogger())
	judgment, err := j.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "pass" {
		t.Fatalf("expected pass, got %s", judgment.Decision)
	}
}

func TestRuleJudgeDegradesWhenPipelineDown(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer server.Close()
	objs := objectstore.NewMemory()
	asset := textAsset("全网最低价")
	putText(t, objs, asset, "全网最低价")

	j := NewRuleJudge(pipeline.New(server.URL, 500*time.Millisecond, testLogger()), objs, nil, testLogger())
	judgment, err := j.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("degrade must not fail the task: %v", err)
	}
	if judgment.Decision != "manual" {
		t.Fatalf("expected manual fallback, got %s", judgment.Decision)
	}
	if !strings.Contains(judgment.Reason, "unavailable") {
		t.Fatalf("expected degraded reason, got %q", judgment.Reason)
	}
}

func TestRuleJudgeNonTextAssetGoesManual(t *testing.T) {
	objs := objectstore.NewMemory()
	asset := textAsset("")
	asset.Type = "image"
	j := NewRuleJudge(nil, objs, nil, testLogger())
	judgment, err := j.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "manual" {
		t.Fatalf("expected manual for image asset, got %s", judgment.Decision)
	}
	if !strings.Contains(judgment.Reason, "OCR") {
		t.Fatalf("expected OCR limitation reason, got %q", judgment.Reason)
	}
}

func TestRuleJudgeOversizedTextGoesManual(t *testing.T) {
	objs := objectstore.NewMemory()
	big := strings.Repeat("a", maxRuleJudgeTextBytes+1)
	asset := textAsset(big)
	putText(t, objs, asset, big)
	j := NewRuleJudge(nil, objs, nil, testLogger())
	judgment, err := j.Judge(context.Background(), asset)
	if err != nil {
		t.Fatalf("judge: %v", err)
	}
	if judgment.Decision != "manual" || !strings.Contains(judgment.Reason, "limit") {
		t.Fatalf("expected size-limit manual, got %s / %q", judgment.Decision, judgment.Reason)
	}
}
