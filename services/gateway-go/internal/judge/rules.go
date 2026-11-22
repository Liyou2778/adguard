package judge

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"strings"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/objectstore"
	"github.com/Liyou2778/adguard/services/gateway/internal/pipeline"
	"github.com/Liyou2778/adguard/services/gateway/internal/snapshot"
	"github.com/google/uuid"
)

const maxRuleJudgeTextBytes = 256 * 1024

// RuleJudge is the M3 judging service: it reads text assets, calls the Python
// pipeline (/v1/judge = M2 matcher + M3 clause retrieval) and turns the answer into
// a judgment with clause-level violations.
//
// Degradation policy (inherited from P1): an unavailable pipeline never fails the
// upload; the asset is routed to manual review with an explicit reason.
type RuleJudge struct {
	client    *pipeline.Client
	objects   objectstore.Store
	snapshots *snapshot.Client
	logger    *slog.Logger
}

func NewRuleJudge(client *pipeline.Client, objects objectstore.Store, snapshots *snapshot.Client,
	logger *slog.Logger) *RuleJudge {
	return &RuleJudge{client: client, objects: objects, snapshots: snapshots, logger: logger}
}

func (j *RuleJudge) Judge(ctx context.Context, asset *model.Asset) (*model.Judgment, error) {
	started := time.Now()

	if asset.Type != "text" {
		return j.manual(ctx, asset, started,
			"M3 rule judge only handles text assets; OCR/multimodal parsing arrives in M4 - routed to manual review")
	}
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
			"text asset exceeds the M3 rule-judge limit (256 KiB) - routed to manual review")
	}
	text := strings.ToValidUTF8(string(data), "")

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
	return judgment, nil
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
