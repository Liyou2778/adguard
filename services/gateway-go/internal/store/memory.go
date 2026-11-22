package store

import (
	"context"
	"sort"
	"sync"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
)

// Memory is an in-memory Store used by HTTP/pipeline tests. It mirrors the MySQL
// semantics that matter for correctness (tenant isolation, idempotent chunks,
// digest dedupe), not the SQL.
type Memory struct {
	mu        sync.RWMutex
	assets    map[string]*model.Asset // key tenant|asset
	byDigest  map[string]string       // key tenant|digest -> asset id
	sessions  map[string]*model.UploadSession
	chunks    map[string]map[int]int64 // key tenant|asset -> index -> size
	judgments []*model.Judgment
}

func NewMemory() *Memory {
	return &Memory{
		assets:   map[string]*model.Asset{},
		byDigest: map[string]string{},
		sessions: map[string]*model.UploadSession{},
		chunks:   map[string]map[int]int64{},
	}
}

func k(parts ...string) string {
	out := ""
	for i, p := range parts {
		if i > 0 {
			out += "|"
		}
		out += p
	}
	return out
}

func (m *Memory) CreateUpload(_ context.Context, asset *model.Asset, session *model.UploadSession) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.assets[k(asset.TenantID, asset.ID)]; ok {
		return ErrDuplicateDigest
	}
	copyA := *asset
	m.assets[k(asset.TenantID, asset.ID)] = &copyA
	copyS := *session
	m.sessions[k(session.TenantID, session.AssetID)] = &copyS
	return nil
}

func (m *Memory) GetUploadSessionByAsset(_ context.Context, tenantID, assetID string) (*model.UploadSession, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	s, ok := m.sessions[k(tenantID, assetID)]
	if !ok {
		return nil, ErrNotFound
	}
	copyS := *s
	return &copyS, nil
}

func (m *Memory) AddChunk(_ context.Context, tenantID, assetID string, index int, size int64) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	key := k(tenantID, assetID)
	if _, ok := m.sessions[key]; !ok {
		return 0, ErrNotFound
	}
	if m.chunks[key] == nil {
		m.chunks[key] = map[int]int64{}
	}
	m.chunks[key][index] = size
	return len(m.chunks[key]), nil
}

func (m *Memory) CountChunks(_ context.Context, tenantID, assetID string) (int, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return len(m.chunks[k(tenantID, assetID)]), nil
}

func (m *Memory) CompleteSession(_ context.Context, tenantID, assetID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.sessions[k(tenantID, assetID)]
	if !ok {
		return ErrNotFound
	}
	s.Status = "completed"
	return nil
}

func (m *Memory) GetAsset(_ context.Context, tenantID, assetID string) (*model.Asset, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	a, ok := m.assets[k(tenantID, assetID)]
	if !ok {
		return nil, ErrNotFound
	}
	copyA := *a
	return &copyA, nil
}

func (m *Memory) FindAssetByDigest(_ context.Context, tenantID, digest string) (*model.Asset, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	id, ok := m.byDigest[k(tenantID, digest)]
	if !ok {
		return nil, ErrNotFound
	}
	copyA := *m.assets[k(tenantID, id)]
	return &copyA, nil
}

func (m *Memory) MarkAssetQueued(_ context.Context, tenantID, assetID, objectKey, digest string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if existing, ok := m.byDigest[k(tenantID, digest)]; ok && existing != assetID {
		return ErrDuplicateDigest
	}
	a, ok := m.assets[k(tenantID, assetID)]
	if !ok {
		return ErrNotFound
	}
	a.ObjectKey = objectKey
	a.Digest = digest
	a.Status = model.AssetStatusQueued
	a.UpdatedAt = time.Now()
	m.byDigest[k(tenantID, digest)] = assetID
	return nil
}

func (m *Memory) MarkAssetStatus(_ context.Context, tenantID, assetID, status string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.assets[k(tenantID, assetID)]
	if !ok {
		return ErrNotFound
	}
	a.Status = status
	a.UpdatedAt = time.Now()
	return nil
}

func (m *Memory) MarkAssetDuplicate(_ context.Context, tenantID, assetID, existingAssetID string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.assets[k(tenantID, assetID)]
	if !ok {
		return ErrNotFound
	}
	a.Status = model.AssetStatusDuplicate
	a.DedupedFrom = existingAssetID
	a.UpdatedAt = time.Now()
	return nil
}

func (m *Memory) CreateJudgment(_ context.Context, j *model.Judgment) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	copyJ := *j
	if copyJ.Violations == nil {
		copyJ.Violations = make([]model.Violation, 0)
	}
	m.judgments = append(m.judgments, &copyJ)
	return nil
}

func (m *Memory) GetJudgment(_ context.Context, tenantID, judgmentID string) (*model.Judgment, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	for _, j := range m.judgments {
		if j.TenantID == tenantID && j.ID == judgmentID {
			copyJ := *j
			return &copyJ, nil
		}
	}
	return nil, ErrNotFound
}

func (m *Memory) LatestJudgment(_ context.Context, tenantID, assetID string) (*model.Judgment, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	var latest *model.Judgment
	for _, j := range m.judgments {
		if j.TenantID == tenantID && j.AssetID == assetID {
			if latest == nil || j.CreatedAt.After(latest.CreatedAt) {
				latest = j
			}
		}
	}
	if latest == nil {
		return nil, ErrNotFound
	}
	copyJ := *latest
	return &copyJ, nil
}

func (m *Memory) Ping(_ context.Context) error        { return nil }
func (m *Memory) SchemaReady(_ context.Context) error { return nil }
func (m *Memory) Close() error                        { return nil }

// JudgmentsSorted is a test helper to inspect created judgments in order.
func (m *Memory) JudgmentsSorted() []*model.Judgment {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]*model.Judgment, len(m.judgments))
	copy(out, m.judgments)
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.Before(out[j].CreatedAt) })
	return out
}
