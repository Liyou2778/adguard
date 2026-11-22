package store

import (
	"context"
	"errors"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
)

var (
	// ErrNotFound means the row does not exist *for this tenant*. Tenants never learn
	// whether another tenant owns the id.
	ErrNotFound = errors.New("not found")
	// ErrDuplicateDigest means (tenant_id, digest) already exists: the object was
	// uploaded before and must be deduplicated.
	ErrDuplicateDigest = errors.New("duplicate digest")
)

// Store is the persistence port. The MySQL implementation is used in production;
// the in-memory implementation keeps HTTP tests fast and Docker-free.
type Store interface {
	CreateUpload(ctx context.Context, asset *model.Asset, session *model.UploadSession) error
	GetUploadSessionByAsset(ctx context.Context, tenantID, assetID string) (*model.UploadSession, error)
	AddChunk(ctx context.Context, tenantID, assetID string, index int, size int64) (int, error)
	CountChunks(ctx context.Context, tenantID, assetID string) (int, error)
	CompleteSession(ctx context.Context, tenantID, assetID string) error

	GetAsset(ctx context.Context, tenantID, assetID string) (*model.Asset, error)
	FindAssetByDigest(ctx context.Context, tenantID, digest string) (*model.Asset, error)
	MarkAssetQueued(ctx context.Context, tenantID, assetID, objectKey, digest string) error
	MarkAssetStatus(ctx context.Context, tenantID, assetID, status string) error
	MarkAssetDuplicate(ctx context.Context, tenantID, assetID, existingAssetID string) error

	CreateJudgment(ctx context.Context, judgment *model.Judgment) error
	GetJudgment(ctx context.Context, tenantID, judgmentID string) (*model.Judgment, error)
	LatestJudgment(ctx context.Context, tenantID, assetID string) (*model.Judgment, error)

	Ping(ctx context.Context) error
	SchemaReady(ctx context.Context) error
	Close() error
}
