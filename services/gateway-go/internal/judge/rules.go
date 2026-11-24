package judge

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"os"
	"strings"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/pipeline"
	"github.com/Liyou2778/adguard/services/gateway/internal/snapshot"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/google/uuid"
)

const (
	maxRuleJudgeTextBytes = 256 * 1024
	// maxParseBytes bounds how much of an object is buffered for multimodal parsing (FR-2).
	// Larger assets degrade to manual review instead of risking the gateway's memory.
	maxParseBytes    = 64 * 1024 * 1024
	maxParseTextRune = 8000 // stored in asset.parse_meta as reviewer evidence
)

// RuleJudge is the rule-based judging service: it reads/parses assets, calls the Python
// pipeline (/v1/scan, /v1/parse, /v1/judge = M2 matcher + M3 clause retrieval) and turns the
// answer into a judgment with clause-level violations (plus OCR bboxes from M7 onwards).
//
// Degradation policy (inherited from P1): an unavailable pipeline never fails the upload; the
// asset is routed to manual review with an explicit reason.
type RuleJudge struct {
	client    *pipeline.Client
	objects   objectstore.Store
	snapshots *snapshot.Client
	store     store.Store
	logger    *slog.Logger
}

func NewRuleJudge(client *pipeline.Client, objects objectstore.Store, snapshots *snapshot.Client,
	logger *slog.Logger, stores ...store.Store) *RuleJudge {
	j := &RuleJudge{client: client, objects: objects, snapshots: snapshots, logger: logger}
	if len(stores) > 0 {
		j.store = stores[0]
	}
	return j
}

func (j *RuleJudge) Judge(ctx context.Context, asset *model.Asset) (*model.Judgment, error) {
	started := time.Now()
	switch asset.Type {
	case "text":
		return j.judgeText(ctx, asset, started)
	case "image", "video":
		return j.judgeBinary(ctx, asset, started)
	case "landing_page":
		return j.judgeLandingPage(ctx, asset, started)
	default:
		return j.manual(ctx, asset, started,
			fmt.Sprintf("unsupported asset type %q - routed to manual review", asset.Type))
	}
}

func (j *RuleJudge) judgeText(ctx context.Context, asset *model.Asset, started time.Time) (*model.Judgment, error) {
	if asset.ObjectKey == "" {
		return nil, fmt.Errorf("text asset %s has no stored object", asset.ID)
	}
	reader, err := j.objects.Open(ctx, asset.ObjectKey)
	if err != nil {
		return nil, fmt.Errorf("read text asset: %w", err)
	}
	data, readErr := io.ReadAll(io.LimitReader(reader, maxRuleJudgeTextBytes+1))
	_ = reader.Close()
	if readErr != nil {
		return nil, fmt.Errorf("read text asset: %w", readErr)
	}
	if len(data) > maxRuleJudgeTextBytes {
		return j.manual(ctx, asset, started,
			"text asset exceeds the rule-judge limit (256 KiB) - routed to manual review")
	}
	text := strings.ToValidUTF8(string(data), "")

	// Best-effort antivirus scan: an old pipeline without /v1/scan must not break judging, but
	// an explicit infection verdict is authoritative.
	if j.client != nil {
		if scan, scanErr := j.client.Scan(ctx, data, asset.Filename); scanErr != nil {
			j.logger.Warn("antivirus scan unavailable; continuing", "asset_id", asset.ID, "err", scanErr)
		} else if quarantine, qerr := j.scanOutcome(ctx, asset, started, scan); quarantine != nil || qerr != nil {
			return quarantine, qerr
		}
	}
	return j.judgeWithPipeline(ctx, asset, started, text, nil)
}

func (j *RuleJudge) judgeBinary(ctx context.Context, asset *model.Asset, started time.Time) (*model.Judgment, error) {
	if j.client == nil {
		return j.manual(ctx, asset, started,
			"multimodal OCR/parse is unavailable (pipeline not configured) - routed to manual review")
	}
	if asset.ObjectKey == "" {
		return nil, fmt.Errorf("%s asset %s has no stored object", asset.Type, asset.ID)
	}
	reader, err := j.objects.Open(ctx, asset.ObjectKey)
	if err != nil {
		return nil, fmt.Errorf("read %s asset: %w", asset.Type, err)
	}
	data, readErr := io.ReadAll(io.LimitReader(reader, maxParseBytes+1))
	_ = reader.Close()
	if readErr != nil {
		return nil, fmt.Errorf("read %s asset: %w", asset.Type, readErr)
	}
	if len(data) > maxParseBytes {
		return j.manual(ctx, asset, started,
			fmt.Sprintf("%s asset exceeds the multimodal parse limit (%d MiB) - routed to manual review",
				asset.Type, maxParseBytes/(1024*1024)))
	}
	parsed, parseErr := j.client.Parse(ctx, pipeline.ParseRequest{
		AssetType: asset.Type,
		MimeType:  asset.MimeType,
		Filename:  asset.Filename,
		Data:      data,
	})
	if parseErr != nil {
		j.logger.Warn("multimodal parse unavailable; degrading to manual",
			"asset_id", asset.ID, "tenant_id", asset.TenantID, "err", parseErr)
		return j.manual(ctx, asset, started,
			"multimodal parse failed (pipeline error) - routed to manual review (degraded mode)")
	}
	return j.fromParsed(ctx, asset, started, parsed)
}

func (j *RuleJudge) judgeLandingPage(ctx context.Context, asset *model.Asset, started time.Time) (*model.Judgment, error) {
	if j.client == nil {
		return j.manual(ctx, asset, started,
			"landing-page parsing is unavailable (pipeline not configured) - routed to manual review")
	}
	if strings.TrimSpace(asset.SourceURL) == "" {
		return j.manual(ctx, asset, started,
			"landing_page asset has no source_url - routed to manual review")
	}
	parsed, parseErr := j.client.Parse(ctx, pipeline.ParseRequest{
		AssetType: "landing_page",
		SourceURL: asset.SourceURL,
	})
	if parseErr != nil {
		j.logger.Warn("landing-page parse unavailable; degrading to manual",
			"asset_id", asset.ID, "url", asset.SourceURL, "err", parseErr)
		return j.manual(ctx, asset, started,
			"landing-page fetch/render failed (pipeline error) - routed to manual review (degraded mode)")
	}
	return j.fromParsed(ctx, asset, started, parsed)
}

// fromParsed applies scan policy, degrades on empty/degraded parses and otherwise judges the
// extracted text, mapping violation spans back to OCR pixel boxes.
func (j *RuleJudge) fromParsed(ctx context.Context, asset *model.Asset, started time.Time,
	parsed *pipeline.ParsedAsset) (*model.Judgment, error) {
	if parsed.Scan != nil {
		if quarantine, qerr := j.scanOutcome(ctx, asset, started, parsed.Scan); quarantine != nil || qerr != nil {
			return quarantine, qerr
		}
	}
	if strings.TrimSpace(parsed.Text) == "" {
		status := "degraded"
		j.storeParse(ctx, asset, status, parsed)
		reason := "multimodal parse produced no usable text (degraded) - routed to manual review"
		if len(parsed.Warnings) > 0 {
			reason = "multimodal parse degraded: " + truncate(strings.Join(parsed.Warnings, "; "), 300)
		}
		return j.manual(ctx, asset, started, reason)
	}
	status := "parsed"
	if parsed.Degraded {
		status = "degraded"
	}
	j.storeParse(ctx, asset, status, parsed)
	judgment, err := j.judgeWithPipeline(ctx, asset, started, parsed.Text, parsed.Lines)
	if err != nil {
		return nil, err
	}
	if parsed.Degraded && len(parsed.Warnings) > 0 {
		judgment.Reason = truncate(judgment.Reason+"（parse degraded: "+
			strings.Join(parsed.Warnings, "; ")+"）", 500)
	}
	return judgment, nil
}

func (j *RuleJudge) scanOutcome(ctx context.Context, asset *model.Asset, started time.Time,
	scan *pipeline.ScanResult) (*model.Judgment, error) {
	switch scan.Status {
	case "infected":
		j.logger.Warn("asset failed antivirus scan", "asset_id", asset.ID, "signature", scan.Signature)
		return j.manual(ctx, asset, started,
			"security: antivirus scan rejected the asset (signature="+scan.Signature+") - quarantined for manual review")
	case "unknown":
		if scanRequired() {
			return j.manual(ctx, asset, started,
				"security: antivirus scan unavailable (scanner required) - routed to manual review")
		}
		j.logger.Warn("antivirus scan unknown; continuing", "asset_id", asset.ID, "warnings", scan.Warnings)
	}
	return nil, nil
}

func (j *RuleJudge) judgeWithPipeline(ctx context.Context, asset *model.Asset, started time.Time,
	text string, lines []pipeline.ParseElement) (*model.Judgment, error) {
	if j.client == nil {
		return j.manual(ctx, asset, started, "rule judge backend is not configured - routed to manual review")
	}
	response, err := j.client.Judge(ctx, text)
	if err != nil {
		j.logger.Warn("pipeline judge unavailable; degrading to manual",
			"asset_id", asset.ID, "tenant_id", asset.TenantID, "err", err)
		return j.manual(ctx, asset, started,
			"rule judge unavailable (pipeline error) - routed to manual review (degraded mode)")
	}
	judgment := j.buildJudgment(ctx, asset, started, response)
	attachBBoxes(judgment.Violations, lines)
	return judgment, nil
}

func (j *RuleJudge) buildJudgment(ctx context.Context, asset *model.Asset, started time.Time, response *pipeline.JudgeResponse) *model.Judgment {
	judgment := &model.Judgment{
		ID:           uuid.NewString(),
		AssetID:      asset.ID,
		TenantID:     asset.TenantID,
		ModelVersion: response.JudgeVersion,
		Decision:     response.Decision,
		Reason:       response.Reason,
		LatencyMS:    int(time.Since(started).Milliseconds()),
		Violations:   make([]model.Violation, 0, len(response.Violations)),
		CreatedAt:    time.Now().UTC(),
	}
	for _, violation := range response.Violations {
		converted := model.Violation{
			RuleID:         violation.RuleID,
			Family:         violation.Family,
			Canonical:      violation.Canonical,
			VariantKind:    violation.VariantKind,
			EvidenceText:   violation.Evidence,
			Confidence:     violation.Confidence,
			ClauseID:       violation.ClauseID,
			RegulationID:   violation.RegulationID,
			Article:        violation.Article,
			ItemLabel:      violation.ItemLabel,
			ClauseRevision: violation.ClauseRevision,
			ClauseText:     violation.ClauseText,
			PenaltyLawRef:  violation.PenaltyLawRef,
			PenaltyMin:     violation.PenaltyMin,
			PenaltyMax:     violation.PenaltyMax,
		}
		if len(violation.Span) == 2 {
			spanStart, spanEnd := violation.Span[0], violation.Span[1]
			converted.SpanStart = &spanStart
			converted.SpanEnd = &spanEnd
		}
		judgment.Violations = append(judgment.Violations, converted)
	}
	j.bindSnapshot(ctx, asset, judgment)
	return judgment
}

// attachBBoxes maps a violation's text span (offset in the concatenated parse text) to the
// OCR element that contains it. The concatenation rule must match the Python parser: lines are
// joined with "\n", exactly as ParsedAsset.Text is assembled there.
func attachBBoxes(violations []model.Violation, lines []pipeline.ParseElement) {
	if len(violations) == 0 || len(lines) == 0 {
		return
	}
	offsets := make([]int, len(lines))
	pos := 0
	for i, line := range lines {
		offsets[i] = pos
		pos += len(line.Text) + 1
	}
	for idx := range violations {
		violation := &violations[idx]
		if violation.SpanStart == nil || len(violation.BBox) > 0 {
			continue
		}
		span := *violation.SpanStart
		for i := len(offsets) - 1; i >= 0; i-- {
			if span >= offsets[i] && len(lines[i].BBox) == 4 {
				violation.BBox = append([]float64(nil), lines[i].BBox...)
				break
			}
		}
	}
}

type parseEvidence struct {
	AssetType  string               `json:"asset_type"`
	Degraded   bool                 `json:"degraded"`
	Warnings   []string             `json:"warnings"`
	RoleCounts map[string]int       `json:"role_counts"`
	Frames     int                  `json:"frames"`
	Scan       *pipeline.ScanResult `json:"scan,omitempty"`
	TextChars  int                  `json:"text_chars"`
	Text       string               `json:"text"`
	Meta       map[string]any       `json:"meta,omitempty"`
}

func (j *RuleJudge) storeParse(ctx context.Context, asset *model.Asset, status string, parsed *pipeline.ParsedAsset) {
	if j.store == nil || parsed == nil {
		return
	}
	evidence := parseEvidence{
		AssetType:  parsed.AssetType,
		Degraded:   parsed.Degraded,
		Warnings:   parsed.Warnings,
		RoleCounts: parsed.RoleCounts,
		Frames:     parsed.Frames,
		Scan:       parsed.Scan,
		TextChars:  len([]rune(parsed.Text)),
		Text:       truncate(parsed.Text, maxParseTextRune),
		Meta:       parsed.Meta,
	}
	raw, err := json.Marshal(evidence)
	if err != nil {
		j.logger.Warn("encode parse evidence failed", "asset_id", asset.ID, "err", err)
		return
	}
	if err := j.store.UpdateAssetParse(ctx, asset.TenantID, asset.ID, status, string(raw)); err != nil {
		j.logger.Warn("store parse evidence failed", "asset_id", asset.ID, "err", err)
	}
}

func (j *RuleJudge) bindSnapshot(ctx context.Context, asset *model.Asset, judgment *model.Judgment) {
	if j.snapshots == nil {
		return
	}
	id, err := j.snapshots.LatestSnapshotID(asset.TenantID)
	if err != nil {
		j.logger.Warn("rule snapshot unavailable; judgment will not be bound to a snapshot",
			"asset_id", asset.ID, "tenant_id", asset.TenantID, "err", err)
		return
	}
	judgment.RuleSnapshotID = id
}

func (j *RuleJudge) manual(ctx context.Context, asset *model.Asset, started time.Time, reason string) (*model.Judgment, error) {
	judgment := &model.Judgment{
		ID:           uuid.NewString(),
		AssetID:      asset.ID,
		TenantID:     asset.TenantID,
		ModelVersion: "rule-m3-v1",
		Decision:     "manual",
		Reason:       reason,
		LatencyMS:    int(time.Since(started).Milliseconds()),
		Violations:   make([]model.Violation, 0),
		CreatedAt:    time.Now().UTC(),
	}
	j.bindSnapshot(ctx, asset, judgment)
	return judgment, nil
}

func truncate(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit])
}

// scanRequired reports whether an unknown antivirus verdict must be routed to manual review.
// Conservative default: not required (the scanner itself may be disabled in local dev), but
// production sets ADGUARD_SCAN_REQUIRED=1 via the gateway env file.
func scanRequired() bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("ADGUARD_SCAN_REQUIRED"))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}
