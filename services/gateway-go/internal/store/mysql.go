package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	mysqldriver "github.com/go-sql-driver/mysql"
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
		(id, tenant_id, type, status, size_bytes, mime_type, filename, created_at, updated_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		asset.ID, asset.TenantID, asset.Type, asset.Status, asset.SizeBytes, asset.MimeType,
		asset.Filename, asset.CreatedAt, asset.UpdatedAt)
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
		COALESCE(digest,''), size_bytes, COALESCE(mime_type,''), COALESCE(filename,''),
		COALESCE(deduped_from,''), created_at, updated_at
		FROM asset WHERE tenant_id = ? AND id = ?`, tenantID, assetID)
	return scanAsset(row)
}

func (s *MySQL) FindAssetByDigest(ctx context.Context, tenantID, digest string) (*model.Asset, error) {
	row := s.db.QueryRowContext(ctx, `SELECT id, tenant_id, type, status, COALESCE(object_key,''),
		COALESCE(digest,''), size_bytes, COALESCE(mime_type,''), COALESCE(filename,''),
		COALESCE(deduped_from,''), created_at, updated_at
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
	_, err := s.db.ExecContext(ctx, `INSERT INTO judgment
		(id, asset_id, tenant_id, rule_snapshot_id, model_version, decision, reason, latency_ms,
		 cost_micro, created_at)
		VALUES (?, ?, ?, NULLIF(?,''), ?, ?, ?, ?, ?, ?)`,
		j.ID, j.AssetID, j.TenantID, j.RuleSnapshotID, j.ModelVersion, j.Decision, j.Reason,
		j.LatencyMS, j.CostMicro, j.CreatedAt)
	return err
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
	j.Violations = make([]model.Violation, 0)
	return j, nil
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
	j.Violations = make([]model.Violation, 0)
	return j, nil
}

type scanner interface {
	Scan(dest ...any) error
}

func scanAsset(row scanner) (*model.Asset, error) {
	var a model.Asset
	err := row.Scan(&a.ID, &a.TenantID, &a.Type, &a.Status, &a.ObjectKey, &a.Digest, &a.SizeBytes,
		&a.MimeType, &a.Filename, &a.DedupedFrom, &a.CreatedAt, &a.UpdatedAt)
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

func isDuplicateKey(err error) bool {
	var me *mysqldriver.MySQLError
	return errors.As(err, &me) && me.Number == 1062
}
