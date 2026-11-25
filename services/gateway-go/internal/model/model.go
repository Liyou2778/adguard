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
	SourceURL   string    `json:"source_url,omitempty"`   // landing_page assets only (M7)
	ParseStatus string    `json:"parse_status,omitempty"` // pending|parsed|degraded (M7)
	ParseMeta   string    `json:"parse_meta,omitempty"`   // raw JSON: OCR/layout/scan evidence (M7)
	Digest      string    `json:"digest,omitempty"`
	SizeBytes   int64     `json:"size_bytes"`
	MimeType    string    `json:"mime_type,omitempty"`
	Filename    string    `json:"filename,omitempty"`
	Caption     string    `json:"caption,omitempty"` // merchant copy used for FR-6 consistency
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

// Violation is one clause-level finding: the cited rule, the original-text span and
// (from M3) the exact clause + penalty that the decision is based on.
type Violation struct {
	ID             string    `json:"violation_id,omitempty"`
	RuleID         string    `json:"rule_id"`
	Family         string    `json:"family,omitempty"`
	Canonical      string    `json:"canonical,omitempty"`
	VariantKind    string    `json:"variant_kind,omitempty"`
	SpanStart      *int      `json:"span_start,omitempty"`
	SpanEnd        *int      `json:"span_end,omitempty"`
	BBox           []float64 `json:"bbox,omitempty"` // OCR pixel box [x0,y0,x1,y1] (M7)
	EvidenceText   string    `json:"evidence_text,omitempty"`
	Confidence     float64   `json:"confidence"`
	ClauseID       string    `json:"clause_id,omitempty"`
	RegulationID   string    `json:"regulation_id,omitempty"`
	Article        string    `json:"article,omitempty"`
	ItemLabel      string    `json:"item_label,omitempty"`
	ClauseRevision string    `json:"clause_revision,omitempty"`
	ClauseText     string    `json:"clause_text,omitempty"`
	PenaltyLawRef  string    `json:"penalty_law_ref,omitempty"`
	PenaltyMin     *int      `json:"penalty_min,omitempty"`
	PenaltyMax     *int      `json:"penalty_max,omitempty"`
}

// AuditTask is the queue payload that triggers the judging pipeline.
type AuditTask struct {
	AssetID  string `json:"asset_id"`
	TenantID string `json:"tenant_id"`
	Attempt  int    `json:"attempt"`
}

// --------------------------------------------------------------------------- M9 quota/billing
// Tenant mirrors the control-plane tenant table (status + daily quota in weight points).
type Tenant struct {
	ID         string `json:"tenant_id"`
	Name       string `json:"name"`
	Status     string `json:"status"`
	DailyQuota int    `json:"daily_quota"`
}

// JudgePrice is one row of billing_price for the judge service.
type JudgePrice struct {
	AssetType string `json:"asset_type"`
	Weight    int    `json:"quota_weight"`
	CostMicro int64  `json:"unit_price_micro"`
}

// UsageRecord is one ledger row written per judged asset (billing ground truth).
type UsageRecord struct {
	ID         string    `json:"id"`
	TenantID   string    `json:"tenant_id"`
	AssetID    string    `json:"asset_id"`
	JudgmentID string    `json:"judgment_id"`
	AssetType  string    `json:"asset_type"`
	Weight     int       `json:"weight"`
	CostMicro  int64     `json:"cost_micro"`
	BilledDate time.Time `json:"billed_date"`
	CreatedAt  time.Time `json:"created_at"`
}

// UsageItem is an aggregate row for the merchant usage endpoint.
type UsageItem struct {
	AssetType string `json:"asset_type"`
	Calls     int64  `json:"calls"`
	Weight    int64  `json:"weight"`
	CostMicro int64  `json:"cost_micro"`
}

// UsageSummary aggregates usage_records for one tenant and date range.
type UsageSummary struct {
	TenantID  string      `json:"tenant_id"`
	From      string      `json:"from"`
	To        string      `json:"to"`
	Calls     int64       `json:"calls"`
	Weight    int64       `json:"weight"`
	CostMicro int64       `json:"cost_micro"`
	Items     []UsageItem `json:"items"`
}
