package worker

import (
	"context"
	"errors"
	"log/slog"

	"github.com/Liyou2778/adguard/services/gateway/internal/judge"
	"github.com/Liyou2778/adguard/services/gateway/internal/metrics"
	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/queue"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/google/uuid"
)

// Worker consumes audit tasks and writes judgments.
//
// Idempotency: a redelivered task checks for an existing judgment first, so at-least-once
// queue semantics never create duplicate judgments for the same asset.
type Worker struct {
	store       store.Store
	judge       judge.Service
	maxAttempts int
	metrics     *metrics.Metrics
	logger      *slog.Logger
}

func New(st store.Store, judging judge.Service, maxAttempts int, m *metrics.Metrics,
	logger *slog.Logger) *Worker {
	return &Worker{store: st, judge: judging, maxAttempts: maxAttempts, metrics: m, logger: logger}
}

func (w *Worker) Run(ctx context.Context, consumer queue.Consumer) error {
	if consumer == nil {
		return nil
	}
	return consumer.Run(ctx, w.ProcessOne)
}

// ProcessOne runs the M1 judging pipeline for one asset (single step: stub judge).
func (w *Worker) ProcessOne(ctx context.Context, task model.AuditTask) error {
	asset, err := w.store.GetAsset(ctx, task.TenantID, task.AssetID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			w.logger.Error("audit task references unknown asset; dropping",
				"asset_id", task.AssetID, "tenant_id", task.TenantID)
			return nil
		}
		return err
	}
	if asset.Status == model.AssetStatusJudged || asset.Status == model.AssetStatusDuplicate {
		return nil
	}
	if existing, err := w.store.LatestJudgment(ctx, task.TenantID, task.AssetID); err == nil && existing != nil {
		// Redelivery after a successful judgment: finish the state machine, emit nothing.
		_ = w.store.MarkAssetStatus(ctx, task.TenantID, task.AssetID, model.AssetStatusJudged)
		return nil
	}
	if err := w.store.MarkAssetStatus(ctx, task.TenantID, task.AssetID, model.AssetStatusJudging); err != nil {
		return err
	}

	judgment, err := w.judge.Judge(ctx, asset)
	if err != nil {
		w.failOrRetry(ctx, task, err)
		return nil
	}
	// Defense in depth: every judge must produce a stable judgment id (it is the
	// primary key and the id clients query afterwards).
	if judgment.ID == "" {
		judgment.ID = uuid.NewString()
	}
	if err := w.store.CreateJudgment(ctx, judgment); err != nil {
		w.failOrRetry(ctx, task, err)
		return nil
	}
	if err := w.store.MarkAssetStatus(ctx, task.TenantID, task.AssetID, model.AssetStatusJudged); err != nil {
		return err
	}
	if w.metrics != nil {
		w.metrics.Judgments.WithLabelValues(judgment.Decision).Inc()
	}
	w.logger.Info("asset judged",
		"asset_id", task.AssetID, "tenant_id", task.TenantID,
		"decision", judgment.Decision, "judgment_id", judgment.ID,
		"snapshot_id", judgment.RuleSnapshotID, "latency_ms", judgment.LatencyMS)
	return nil
}

func (w *Worker) failOrRetry(ctx context.Context, task model.AuditTask, cause error) {
	permanent := task.Attempt+1 >= w.maxAttempts
	w.logger.Warn("judging failed",
		"asset_id", task.AssetID, "attempt", task.Attempt, "permanent", permanent, "err", cause)
	if !permanent {
		return // queue layer re-publishes with attempt+1
	}
	if w.metrics != nil {
		w.metrics.AssetFailures.Inc()
	}
	if err := w.store.MarkAssetStatus(ctx, task.TenantID, task.AssetID, model.AssetStatusFailed); err != nil {
		w.logger.Error("failed to mark asset as failed", "asset_id", task.AssetID, "err", err)
	}
}
