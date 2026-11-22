package objectstore

import (
	"context"
	"fmt"
	"io"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// MinioStore implements Store for any S3-compatible backend.
//
// Two clients are used on purpose:
//   - internal client  -> talks to the container/service endpoint (no public traffic cost);
//   - public client    -> signs download URLs with a browser-reachable host.
//
// P1 hit exactly this bug (presigned URL signed with the -internal endpoint could not
// be opened from a browser). Keeping them separate makes the mistake impossible.
type MinioStore struct {
	internal *minio.Client
	public   *minio.Client
	bucket   string
}

func NewMinio(endpoint, accessKey, secretKey string, useSSL bool, bucket,
	publicEndpoint string) (*MinioStore, error) {
	if publicEndpoint == "" {
		publicEndpoint = endpoint
	}
	credentialsProvider := credentials.NewStaticV4(accessKey, secretKey, "")
	internal, err := minio.New(endpoint, &minio.Options{Creds: credentialsProvider, Secure: useSSL})
	if err != nil {
		return nil, fmt.Errorf("minio internal client: %w", err)
	}
	public, err := minio.New(publicEndpoint, &minio.Options{Creds: credentialsProvider, Secure: useSSL})
	if err != nil {
		return nil, fmt.Errorf("minio public client: %w", err)
	}
	return &MinioStore{internal: internal, public: public, bucket: bucket}, nil
}

func (s *MinioStore) EnsureBucket(ctx context.Context) error {
	exists, err := s.internal.BucketExists(ctx, s.bucket)
	if err != nil {
		return fmt.Errorf("bucket exists check: %w", err)
	}
	if exists {
		return nil
	}
	if err := s.internal.MakeBucket(ctx, s.bucket, minio.MakeBucketOptions{}); err != nil {
		return fmt.Errorf("create bucket %s: %w", s.bucket, err)
	}
	return nil
}

func (s *MinioStore) PutChunk(ctx context.Context, key string, r io.Reader, size int64, contentType string) error {
	_, err := s.internal.PutObject(ctx, s.bucket, key, r, size,
		minio.PutObjectOptions{ContentType: contentType})
	if err != nil {
		return fmt.Errorf("put object %s: %w", key, err)
	}
	return nil
}

func (s *MinioStore) Compose(ctx context.Context, dstKey string, srcKeys []string) error {
	if len(srcKeys) == 0 {
		return fmt.Errorf("compose: no source objects")
	}
	srcs := make([]minio.CopySrcOptions, 0, len(srcKeys))
	for _, key := range srcKeys {
		srcs = append(srcs, minio.CopySrcOptions{Bucket: s.bucket, Object: key})
	}
	dest := minio.CopyDestOptions{Bucket: s.bucket, Object: dstKey}
	if _, err := s.internal.ComposeObject(ctx, dest, srcs...); err != nil {
		return fmt.Errorf("compose %d objects into %s: %w", len(srcKeys), dstKey, err)
	}
	return nil
}

func (s *MinioStore) Open(ctx context.Context, key string) (io.ReadCloser, error) {
	obj, err := s.internal.GetObject(ctx, s.bucket, key, minio.GetObjectOptions{})
	if err != nil {
		return nil, fmt.Errorf("get object %s: %w", key, err)
	}
	return obj, nil
}

func (s *MinioStore) Remove(ctx context.Context, key string) error {
	if err := s.internal.RemoveObject(ctx, s.bucket, key, minio.RemoveObjectOptions{}); err != nil {
		return fmt.Errorf("remove object %s: %w", key, err)
	}
	return nil
}

func (s *MinioStore) PresignGet(ctx context.Context, key string, ttl time.Duration) (string, error) {
	url, err := s.public.PresignedGetObject(ctx, s.bucket, key, ttl, nil)
	if err != nil {
		return "", fmt.Errorf("presign %s: %w", key, err)
	}
	return url.String(), nil
}
