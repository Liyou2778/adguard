package objectstore

import (
	"context"
	"io"
	"time"
)

// Store is the object storage port (MinIO locally, OSS in production).
type Store interface {
	EnsureBucket(ctx context.Context) error
	PutChunk(ctx context.Context, key string, r io.Reader, size int64, contentType string) error
	// Compose merges chunk objects into a single object, in the given order.
	Compose(ctx context.Context, dstKey string, srcKeys []string) error
	Open(ctx context.Context, key string) (io.ReadCloser, error)
	Remove(ctx context.Context, key string) error
	PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error)
}
