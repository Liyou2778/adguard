package judge

import (
	"context"
	"log/slog"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/snapshot"
	"github.com/google/uuid"
)

// Service is the judging port. M2-M4 replace the stub with normalize -> retrieve ->
// model -> merge; the interface stays the same.
type Service interface {
	Judge(ctx context.Context, asset *model.Asset) (*model.Judgment, error)
}

// Stub is the M1 placeholder judge.
//
// Why "manual" and not "pass": ADR-2 says a miss is unrecoverable while a false
// positive is post-processable. Until the real pipeline exists, every asset is
// routed to human review, and the reason states exactly why. This keeps the
// closed loop honest instead of fabricating a decision.
type Stub struct {
	snapshots    *snapshot.Client
	modelVersion string
	logger       *slog.Logger
}

func NewStub(snapshots *snapshot.Client, modelVersion string, logger *slog.Logger) *Stub {
	return &Stub{snapshots: snapshots, modelVersion: modelVersion, logger: logger}
}

func (s *Stub) Judge(ctx context.Context, asset *model.Asset) (*model.Judgment, error) {
	started := time.Now()
	snapshotID := ""
	if s.snapshots != nil {
		id, err := s.snapshots.LatestSnapshotID(asset.TenantID)
		if err != nil {
			s.logger.Warn("rule snapshot unavailable; judgment will not be bound to a snapshot",
				"asset_id", asset.ID, "tenant_id", asset.TenantID, "err", err)
		} else {
			snapshotID = id
		}
	}
	return &model.Judgment{
		ID:             uuid.NewString(),
		AssetID:        asset.ID,
		TenantID:       asset.TenantID,
		RuleSnapshotID: snapshotID,
		ModelVersion:   s.modelVersion,
		Decision:       "manual",
		Reason: "M1 stub judge: OCR/normalization/clause retrieval/model pipeline arrives in M2-M4; " +
			"routes to human review by design (ADR-2)",
		LatencyMS:  int(time.Since(started).Milliseconds()),
		Violations: make([]model.Violation, 0),
		CreatedAt:  time.Now().UTC(),
	}, nil
}
