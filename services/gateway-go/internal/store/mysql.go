package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	mysqldriver "github.com/go-sql-driver/mysql"
	"github.com/google/uuid"
)

// MySQL is the production Store implementation.
//
// Note: the schema is owned by the Java control plane (Flyway). This package only
// reads/writes data, which keeps one migration source of truth for the whole system.
type MySQL struct {
	db *sql.DB
}

func OpenMySQL(dsn string) (*MySQL, error) {
	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, fmt.Errorf("open mysql: %w", err)
	}
	db.SetMaxOpenConns(20)
	db.SetMaxIdleConns(10)
	db.SetConnMaxLifetime(5 * time.Minute)
	db.SetConnMaxIdleTime(2 * time.Minute)
	return &MySQL{db: db}, nil
}

func (s *MySQL) Ping(ctx context.Context) error {
	return s.db.PingContext(ctx)
}

func (s *MySQL) SchemaReady(ctx context.Context) error {
	var count int
	const q = `SELECT COUNT(*) FROM information_schema.tables
	           WHERE table_schema = DATABASE()
	             AND table_name IN ('asset','upload_session','upload_chunk','judgment','rule_snapshot')`
	if err := s.db.QueryRowContext(ctx, q).Scan(&count); err != nil {
		return err
	}
	if count != 5 {
		return fmt.Errorf("schema not ready: expected 5 core tables, found %d (run control-plane migrations)", count)
	}
	return nil
}

func (s *MySQL) Close() error { return s.db.Close() }

func (s *MySQL) CreateUpload(ctx context.Context, asset *model.Asset, session *model.UploadSession) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	_, err = tx.ExecContext(ctx, `INSERT INTO asset
		(id, tenant_id, type, status, size_bytes, mime_type, filename, caption, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, NULLIF(?, ''), ?, ?)`,
		asset.ID, asset.TenantID, asset.Type, asset.Status, asset.SizeBytes, asset.MimeType,
		asset.Filename, asset.Caption, asset.CreatedAt, asset.UpdatedAt)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO upload_session
		(id, asset_id, tenant_id, chunk_size, total_chunks, received_chunks, status, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)`,
		session.ID, session.AssetID, session.TenantID, session.ChunkSize, session.TotalChunks,
		session.Status, session.CreatedAt, session.CreatedAt)
	if err != nil {
		return err
	}
	return tx.Commit()
}

func (s *MySQL) CreateAsset(ctx context.Context, asset *model.Asset) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO asset
		(id, tenant_id, type, status, source_url, digest, size_bytes, mime_type, filename, caption, created_at, updated_at)
		VALUES (?, ?, ?, ?, NULLIF(?, ''), NULLIF(?, ''), ?, NULLIF(?, ''), NULLIF(?, ''), NULLIF(?, ''), ?, ?)`,
		asset.ID, asset.TenantID, asset.Type, asset.Status, asset.SourceURL, asset.Digest,
		asset.SizeBytes, asset.MimeType, asset.Filename, asset.Caption, asset.CreatedAt, asset.UpdatedAt)
	if isDuplicateKey(err) {
		return ErrDuplicateDigest
	}
	return err
}

func (s *MySQL) UpdateAssetParse(ctx context.Context, tenantID, assetID, parseStatus, parseMeta string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE asset SET parse_status = ?, parse_meta = NULLIF(?, '')
		WHERE tenant_id = ? AND id = ?`, parseStatus, parseMeta, tenantID, assetID)
	return err
}

func (s *MySQL) GetUploadSessionByAsset(ctx context.Context, tenantID, assetID string) (*model.UploadSession, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, asset_id, tenant_id, chunk_size, total_chunks,
		received_chunks, status, created_at FROM upload_session WHERE tenant_id = ? AND asset_id = ?`,
		tenantID, assetID)
	var u model.UploadSession
	err := row.Scan(&u.ID, &u.AssetID, &u.TenantID, &u.ChunkSize, &u.TotalChunks, &u.ReceivedChunks,
		&u.Status, &u.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	return &u, err
}

func (s *MySQL) AddChunk(ctx context.Context, tenantID, assetID string, index int, size int64) (int, error) {
	// Idempotent: client retries of the same chunk index must not double count.
	_, err := s.db.ExecContext(ctx, `INSERT IGNORE INTO upload_chunk
		(asset_id, tenant_id, chunk_index, size_bytes) VALUES (?, ?, ?, ?)`,
		assetID, tenantID, index, size)
	if err != nil {
		return 0, err
	}
	return s.CountChunks(ctx, tenantID, assetID)
}

func (s *MySQL) CountChunks(ctx context.Context, tenantID, assetID string) (int, error) {
	var n int
	err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM upload_chunk WHERE tenant_id = ? AND asset_id = ?`,
		tenantID, assetID).Scan(&n)
	return n, err
}

func (s *MySQL) CompleteSession(ctx context.Context, tenantID, assetID string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE upload_session SET status = 'completed'
		WHERE tenant_id = ? AND asset_id = ?`, tenantID, assetID)
	return err
}

func (s *MySQL) GetAsset(ctx context.Context, tenantID, assetID string) (*model.Asset, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, tenant_id, type, status, COALESCE(object_key,''),
		COALESCE(source_url,''), COALESCE(parse_status,''), COALESCE(CAST(parse_meta AS CHAR),''),
		COALESCE(digest,''), size_bytes, COALESCE(mime_type,''), COALESCE(filename,''),
		COALESCE(caption,''), COALESCE(deduped_from,''), created_at, updated_at
		FROM asset WHERE tenant_id = ? AND id = ?`, tenantID, assetID)
	return scanAsset(row)
}

func (s *MySQL) FindAssetByDigest(ctx context.Context, tenantID, digest string) (*model.Asset, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, tenant_id, type, status, COALESCE(object_key,''),
		COALESCE(source_url,''), COALESCE(parse_status,''), COALESCE(CAST(parse_meta AS CHAR),''),
		COALESCE(digest,''), size_bytes, COALESCE(mime_type,''), COALESCE(filename,''),
		COALESCE(caption,''), COALESCE(deduped_from,''), created_at, updated_at
		FROM asset WHERE tenant_id = ? AND digest = ? LIMIT 1`, tenantID, digest)
	return scanAsset(row)
}

func (s *MySQL) MarkAssetQueued(ctx context.Context, tenantID, assetID, objectKey, digest string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE asset SET object_key = ?, digest = ?, status = 'queued'
		WHERE tenant_id = ? AND id = ? AND status IN ('uploading','queued')`,
		objectKey, digest, tenantID, assetID)
	if isDuplicateKey(err) {
		return ErrDuplicateDigest
	}
	return err
}

func (s *MySQL) MarkAssetStatus(ctx context.Context, tenantID, assetID, status string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE asset SET status = ? WHERE tenant_id = ? AND id = ?`,
		status, tenantID, assetID)
	return err
}

func (s *MySQL) MarkAssetDuplicate(ctx context.Context, tenantID, assetID, existingAssetID string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE asset SET status = 'duplicate', deduped_from = ?
		WHERE tenant_id = ? AND id = ?`, existingAssetID, tenantID, assetID)
	return err
}

func (s *MySQL) CreateJudgment(ctx context.Context, j *model.Judgment) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback() }()

	if _, err := tx.ExecContext(ctx, `INSERT INTO judgment
		(id, asset_id, tenant_id, rule_snapshot_id, model_version, decision, reason, latency_ms,
		 cost_micro, created_at)
		VALUES (?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?, ?)`,
		j.ID, j.AssetID, j.TenantID, j.RuleSnapshotID, j.ModelVersion, j.Decision, j.Reason,
		j.LatencyMS, j.CostMicro, j.CreatedAt); err != nil {
		return err
	}
	for _, violation := range j.Violations {
		violationID := violation.ID
		if violationID == "" {
			violationID = uuid.NewString()
		}
		if _, err := tx.ExecContext(ctx, `INSERT INTO violation
			(id, judgment_id, rule_id, span_start, span_end, bbox, evidence_text, confidence,
			 family, canonical, variant_kind, clause_id, regulation_id, article, item_label,
			 clause_revision, clause_text, penalty_law_ref, penalty_min, penalty_max)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			violationID, j.ID, violation.RuleID, violation.SpanStart, violation.SpanEnd,
			bboxValue(violation.BBox), violation.EvidenceText, violation.Confidence, violation.Family, violation.Canonical,
			violation.VariantKind, violation.ClauseID, violation.RegulationID, violation.Article,
			violation.ItemLabel, violation.ClauseRevision, violation.ClauseText,
			violation.PenaltyLawRef, violation.PenaltyMin, violation.PenaltyMax); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func (s *MySQL) GetJudgment(ctx context.Context, tenantID, judgmentID string) (*model.Judgment, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, asset_id, tenant_id, COALESCE(rule_snapshot_id,''),
		COALESCE(model_version,''), decision, COALESCE(reason,''), COALESCE(latency_ms,0),
		COALESCE(cost_micro,0), created_at
		FROM judgment WHERE tenant_id = ? AND id = ?`, tenantID, judgmentID)
	j, err := scanJudgment(row)
	if err != nil {
		return nil, err
	}
	return s.loadViolations(ctx, j)
}

func (s *MySQL) LatestJudgment(ctx context.Context, tenantID, assetID string) (*model.Judgment, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, asset_id, tenant_id, COALESCE(rule_snapshot_id,''),
		COALESCE(model_version,''), decision, COALESCE(reason,''), COALESCE(latency_ms,0),
		COALESCE(cost_micro,0), created_at
		FROM judgment WHERE tenant_id = ? AND asset_id = ? ORDER BY created_at DESC LIMIT 1`,
		tenantID, assetID)
	j, err := scanJudgment(row)
	if err != nil {
		return nil, err
	}
	return s.loadViolations(ctx, j)
}

func (s *MySQL) loadViolations(ctx context.Context, j *model.Judgment) (*model.Judgment, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT id, rule_id, COALESCE(family,''), COALESCE(canonical,''),
		COALESCE(variant_kind,''), span_start, span_end, bbox, COALESCE(evidence_text,''), COALESCE(confidence,0),
		COALESCE(clause_id,''), COALESCE(regulation_id,''), COALESCE(article,''), COALESCE(item_label,''),
		COALESCE(clause_revision,''), COALESCE(clause_text,''), COALESCE(penalty_law_ref,''),
		penalty_min, penalty_max
		FROM violation WHERE judgment_id = ? ORDER BY id`,
		j.ID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	violations := make([]model.Violation, 0)
	for rows.Next() {
		var violation model.Violation
		var spanStart, spanEnd *int
		var confidence float64
		var bboxRaw []byte
		if err := rows.Scan(&violation.ID, &violation.RuleID, &violation.Family, &violation.Canonical,
			&violation.VariantKind, &spanStart, &spanEnd, &bboxRaw, &violation.EvidenceText, &confidence,
			&violation.ClauseID, &violation.RegulationID, &violation.Article, &violation.ItemLabel,
			&violation.ClauseRevision, &violation.ClauseText, &violation.PenaltyLawRef,
			&violation.PenaltyMin, &violation.PenaltyMax); err != nil {
			return nil, err
		}
		violation.SpanStart = spanStart
		violation.SpanEnd = spanEnd
		violation.Confidence = confidence
		if len(bboxRaw) > 0 {
			_ = json.Unmarshal(bboxRaw, &violation.BBox)
		}
		violations = append(violations, violation)
	}
	j.Violations = violations
	return j, rows.Err()
}

type scanner interface {
	Scan(dest ...any) error
}

// --------------------------------------------------------------------------- M9 quota/billing
func (s *MySQL) GetTenant(ctx context.Context, tenantID string) (*model.Tenant, error) {
	var tenant model.Tenant
	err := s.db.QueryRowContext(ctx, `SELECT id, name, status, daily_quota FROM tenant WHERE id = ?`,
		tenantID).Scan(&tenant.ID, &tenant.Name, &tenant.Status, &tenant.DailyQuota)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &tenant, nil
}

func (s *MySQL) PriceForJudge(ctx context.Context, assetType string) (model.JudgePrice, error) {
	price := model.JudgePrice{AssetType: assetType, Weight: 1, CostMicro: 100}
	err := s.db.QueryRowContext(ctx, `SELECT quota_weight, unit_price_micro FROM billing_price
		WHERE code = ?`, "judge."+assetType).Scan(&price.Weight, &price.CostMicro)
	if errors.Is(err, sql.ErrNoRows) {
		// Safe default for an asset type that has no price row yet; never blocks judgment.
		return price, nil
	}
	if err != nil {
		return model.JudgePrice{}, err
	}
	return price, nil
}

func (s *MySQL) RecordUsage(ctx context.Context, usage *model.UsageRecord) error {
	_, err := s.db.ExecContext(ctx, `INSERT INTO usage_record
		(id, tenant_id, asset_id, judgment_id, asset_type, weight, cost_micro, billed_date, created_at)
		VALUES (?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?, ?)`,
		usage.ID, usage.TenantID, usage.AssetID, usage.JudgmentID, usage.AssetType, usage.Weight,
		usage.CostMicro, usage.BilledDate.Format("2006-01-02"), usage.CreatedAt)
	return err
}

func (s *MySQL) UsageExists(ctx context.Context, judgmentID string) (bool, error) {
	if judgmentID == "" {
		return false, nil
	}
	var count int
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM usage_record WHERE judgment_id = ?`,
		judgmentID).Scan(&count); err != nil {
		return false, err
	}
	return count > 0, nil
}

func (s *MySQL) UsageSummary(ctx context.Context, tenantID string, from, to time.Time) (*model.UsageSummary, error) {
	summary := &model.UsageSummary{TenantID: tenantID, From: from.Format("2006-01-02"),
		To: to.Format("2006-01-02"), Items: make([]model.UsageItem, 0)}
	rows, err := s.db.QueryContext(ctx, `SELECT asset_type, COUNT(*), COALESCE(SUM(weight),0),
		COALESCE(SUM(cost_micro),0) FROM usage_record
		WHERE tenant_id = ? AND billed_date BETWEEN ? AND ?
		GROUP BY asset_type ORDER BY asset_type`,
		tenantID, from.Format("2006-01-02"), to.Format("2006-01-02"))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var item model.UsageItem
		if err := rows.Scan(&item.AssetType, &item.Calls, &item.Weight, &item.CostMicro); err != nil {
			return nil, err
		}
		summary.Items = append(summary.Items, item)
		summary.Calls += item.Calls
		summary.Weight += item.Weight
		summary.CostMicro += item.CostMicro
	}
	return summary, rows.Err()
}
func scanAsset(row scanner) (*model.Asset, error) {
	var a model.Asset
	err := row.Scan(&a.ID, &a.TenantID, &a.Type, &a.Status, &a.ObjectKey, &a.SourceURL,
		&a.ParseStatus, &a.ParseMeta, &a.Digest, &a.SizeBytes,
		&a.MimeType, &a.Filename, &a.Caption, &a.DedupedFrom, &a.CreatedAt, &a.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &a, nil
}

func scanJudgment(row scanner) (*model.Judgment, error) {
	var j model.Judgment
	err := row.Scan(&j.ID, &j.AssetID, &j.TenantID, &j.RuleSnapshotID, &j.ModelVersion,
		&j.Decision, &j.Reason, &j.LatencyMS, &j.CostMicro, &j.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	return &j, nil
}

func bboxValue(b []float64) any {
	if len(b) == 0 {
		return nil
	}
	raw, err := json.Marshal(b)
	if err != nil {
		return nil
	}
	return string(raw)
}

func isDuplicateKey(err error) bool {
	var me *mysqldriver.MySQLError
	return errors.As(err, &me) && me.Number == 1062
}
