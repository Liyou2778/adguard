package queue

import (
	"context"
	"sync"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
)

// Memory is a Producer used by fast tests; tests drain it explicitly via Drain so
// they can decide when the "worker" runs.
type Memory struct {
	mu    sync.Mutex
	tasks []model.AuditTask
}

func NewMemory() *Memory { return &Memory{} }

func (m *Memory) Enqueue(_ context.Context, task model.AuditTask) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.tasks = append(m.tasks, task)
	return nil
}

// Drain returns and clears queued tasks (test helper).
func (m *Memory) Drain() []model.AuditTask {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := m.tasks
	m.tasks = nil
	return out
}

func (m *Memory) Len() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return len(m.tasks)
}
