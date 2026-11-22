package model

import "time"

// Asset lifecycle states. Mirrors the asset.status column documented in V1__init.sql.
const (
	AssetStatusUploading = "uploading"
	AssetStatusQueued    = "queued"
	AssetStatusJudging   = "judging"
	AssetStatusJudged    = "judged"
	AssetStatusFailed    = "failed"
	AssetStatusDuplicate = "duplicate"
)

// Asset is a marketing creative submitted for compliance review.
type Asset struct {
	ID          string    `json:"asset_id"`
	TenantID    string    `json:"tenant_id"`
	Type        string    `json:"type"`
	Status      string    `json:"status"`
	ObjectKey   string    `json:"object_key,omitempty"`
	Digest      string    `json:"digest,omitempty"`
	SizeBytes   int64     `json:"size_bytes"`
	MimeType    string    `json:"mime_type,omitempty"`
	Filename    string    `json:"filename,omitempty"`
	DedupedFrom string    `json:"deduped_from,omitempty"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
}

// UploadSession tracks a chunked upload for one asset.
type UploadSession struct {
	ID             string    `json:"upload_id"`
	AssetID        string    `json:"asset_id"`
	TenantID       string    `json:"tenant_id"`
	ChunkSize      int       `json:"chunk_size"`
	TotalChunks    int       `json:"total_chunks"`
	ReceivedChunks int       `json:"received_chunks"`
	Status         string    `json:"status"`
	CreatedAt      time.Time `json:"created_at"`
}

// Judgment binds a decision to a rule snapshot and model version (ADR-3).
type Judgment struct {
	ID             string      `json:"judgment_id"`
	AssetID        string      `json:"asset_id"`
	TenantID       string      `json:"tenant_id"`
	RuleSnapshotID string      `json:"rule_snapshot_id,omitempty"`
	ModelVersion   string      `json:"model_version,omitempty"`
	Decision       string      `json:"decision"`
	Reason         string      `json:"reason,omitempty"`
	LatencyMS      int         `json:"latency_ms"`
	CostMicro      int64       `json:"cost_micro"`
	Violations     []Violation `json:"violations"`
	CreatedAt      time.Time   `json:"created_at"`
}

// Violation is one clause-level finding. M1 always emits an empty list (stub judge).
type Violation struct {
	ID           string  `json:"violation_id,omitempty"`
	RuleID       string  `json:"rule_id"`
	SpanStart    *int    `json:"span_start,omitempty"`
	SpanEnd      *int    `json:"span_end,omitempty"`
	EvidenceText string  `json:"evidence_text,omitempty"`
	Confidence   float64 `json:"confidence"`
}

// AuditTask is the queue payload that triggers the judging pipeline.
type AuditTask struct {
	AssetID  string `json:"asset_id"`
	TenantID string `json:"tenant_id"`
	Attempt  int    `json:"attempt"`
}
