package objectstore

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"sync"
	"time"
)

// Memory is an in-memory Store for tests.
type Memory struct {
	mu      sync.RWMutex
	objects map[string][]byte
	calls   int
}

func NewMemory() *Memory {
	return &Memory{objects: map[string][]byte{}}
}

func (m *Memory) EnsureBucket(_ context.Context) error { return nil }

func (m *Memory) PutChunk(_ context.Context, key string, r io.Reader, _ int64, _ string) error {
	data, err := io.ReadAll(r)
	if err != nil {
		return err
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.objects[key] = data
	m.calls++
	return nil
}

func (m *Memory) Compose(_ context.Context, dstKey string, srcKeys []string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	var buf bytes.Buffer
	for _, key := range srcKeys {
		data, ok := m.objects[key]
		if !ok {
			return fmt.Errorf("compose: source object %s not found", key)
		}
		buf.Write(data)
	}
	m.objects[dstKey] = buf.Bytes()
	m.calls++
	return nil
}

func (m *Memory) Open(_ context.Context, key string) (io.ReadCloser, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	data, ok := m.objects[key]
	if !ok {
		return nil, fmt.Errorf("object %s not found", key)
	}
	copied := make([]byte, len(data))
	copy(copied, data)
	return io.NopCloser(bytes.NewReader(copied)), nil
}

func (m *Memory) Remove(_ context.Context, key string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.objects, key)
	return nil
}

func (m *Memory) PresignGet(_ context.Context, key string, _ time.Duration) (string, error) {
	return "memory://adguard-assets/" + key, nil
}

// Get returns a stored object (test helper).
func (m *Memory) Get(key string) ([]byte, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	data, ok := m.objects[key]
	return data, ok
}

// HasPrefixCount counts objects under a prefix (test helper, e.g. temp chunks cleaned up).
func (m *Memory) HasPrefixCount(prefix string) int {
	m.mu.RLock()
	defer m.mu.RUnlock()
	n := 0
	for key := range m.objects {
		if len(key) >= len(prefix) && key[:len(prefix)] == prefix {
			n++
		}
	}
	return n
}
