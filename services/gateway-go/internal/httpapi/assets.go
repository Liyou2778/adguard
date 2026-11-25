package httpapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/Liyou2778/adguard/services/gateway/internal/model"
	"github.com/Liyou2778/adguard/services/gateway/internal/store"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
)

const (
	maxJSONBodyBytes  = 64 * 1024
	maxChunkSizeBytes = 64 * 1024 * 1024
	maxFilenameRunes  = 120
	downloadURLTTL    = 15 * time.Minute
	allowedAssetTypes = "image|video|text|landing_page"
)

type createAssetRequest struct {
	Filename  string `json:"filename"`
	SizeBytes int64  `json:"size_bytes"`
	MimeType  string `json:"mime_type"`
	Type      string `json:"type"`
	SourceURL string `json:"source_url"` // landing_page only (M7)
	ChunkSize int    `json:"chunk_size"`
}

type createAssetResponse struct {
	AssetID       string `json:"asset_id"`
	UploadID      string `json:"upload_id"`
	ChunkSize     int    `json:"chunk_size"`
	TotalChunks   int    `json:"total_chunks"`
	MaxAssetBytes int64  `json:"max_asset_bytes"`
}

func (s *Server) createAsset(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)

	var req createAssetRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, maxJSONBodyBytes))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		writeError(w, r, http.StatusBadRequest, "INVALID_BODY",
			"request body must be valid JSON: "+err.Error(), nil)
		return
	}

	assetType := strings.TrimSpace(req.Type)
	if !isAllowedAssetType(assetType) {
		writeError(w, r, http.StatusBadRequest, "INVALID_ASSET_TYPE",
			"type must be one of "+allowedAssetTypes, nil)
		return
	}
	if assetType == "landing_page" {
		s.createLandingPage(w, r, tenant, req)
		return
	}
	if req.SizeBytes <= 0 {
		writeError(w, r, http.StatusBadRequest, "INVALID_SIZE", "size_bytes must be > 0", nil)
		return
	}
	if req.SizeBytes > s.cfg.MaxAssetBytes {
		writeError(w, r, http.StatusRequestEntityTooLarge, "ASSET_TOO_LARGE",
			fmt.Sprintf("size_bytes %d exceeds limit %d", req.SizeBytes, s.cfg.MaxAssetBytes), nil)
		return
	}

	chunkSize := req.ChunkSize
	if chunkSize == 0 {
		chunkSize = s.cfg.DefaultChunkSize
	}
	if chunkSize < s.cfg.MinChunkSize || chunkSize > maxChunkSizeBytes {
		writeError(w, r, http.StatusBadRequest, "INVALID_CHUNK_SIZE",
			fmt.Sprintf("chunk_size must be between %d and %d bytes",
				s.cfg.MinChunkSize, maxChunkSizeBytes), nil)
		return
	}
	totalChunks := int((req.SizeBytes + int64(chunkSize) - 1) / int64(chunkSize))
	if totalChunks > s.cfg.MaxChunks {
		writeError(w, r, http.StatusBadRequest, "TOO_MANY_CHUNKS",
			fmt.Sprintf("upload would need %d chunks, limit is %d", totalChunks, s.cfg.MaxChunks), nil)
		return
	}

	if s.enforceQuota(w, r, tenant, assetType) {
		return
	}

	now := time.Now().UTC()
	asset := &model.Asset{
		ID:        uuid.NewString(),
		TenantID:  tenant,
		Type:      assetType,
		Status:    model.AssetStatusUploading,
		SizeBytes: req.SizeBytes,
		MimeType:  normaliseMime(req.MimeType),
		Filename:  sanitiseFilename(req.Filename),
		CreatedAt: now,
		UpdatedAt: now,
	}
	session := &model.UploadSession{
		ID:          uuid.NewString(),
		AssetID:     asset.ID,
		TenantID:    tenant,
		ChunkSize:   chunkSize,
		TotalChunks: totalChunks,
		Status:      "uploading",
		CreatedAt:   now,
	}
	if err := s.store.CreateUpload(r.Context(), asset, session); err != nil {
		s.logger.Error("create upload failed", "tenant", tenant, "err", err)
		writeError(w, r, http.StatusInternalServerError, "INTERNAL_ERROR", "failed to create upload", nil)
		return
	}
	s.metrics.AssetsCreated.WithLabelValues(assetType).Inc()
	s.logger.Info("upload created", "tenant", tenant, "asset_id", asset.ID,
		"type", assetType, "size_bytes", req.SizeBytes, "total_chunks", totalChunks,
		"request_id", requestIDFrom(r))

	writeJSON(w, http.StatusCreated, createAssetResponse{
		AssetID:       asset.ID,
		UploadID:      session.ID,
		ChunkSize:     chunkSize,
		TotalChunks:   totalChunks,
		MaxAssetBytes: s.cfg.MaxAssetBytes,
	})
}

// createLandingPage handles URL assets (FR-1): no chunk upload; the pipeline fetches/parses the
// page. Dedupe is by URL digest so re-submitting the same page does not create new judgments.
func (s *Server) createLandingPage(w http.ResponseWriter, r *http.Request, tenant string, req createAssetRequest) {
	source := strings.TrimSpace(req.SourceURL)
	parsed, err := url.Parse(source)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		writeError(w, r, http.StatusBadRequest, "INVALID_SOURCE_URL",
			"source_url must be a valid http(s) URL for landing_page assets", nil)
		return
	}
	finalURL := parsed.String()
	if s.enforceQuota(w, r, tenant, "landing_page") {
		return
	}
	sum := sha256.Sum256([]byte(finalURL))
	digest := hex.EncodeToString(sum[:])

	if existing, findErr := s.store.FindAssetByDigest(r.Context(), tenant, digest); findErr == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"asset_id": existing.ID, "status": existing.Status, "deduped": true,
		})
		return
	}
	now := time.Now().UTC()
	asset := &model.Asset{
		ID:        uuid.NewString(),
		TenantID:  tenant,
		Type:      "landing_page",
		Status:    model.AssetStatusQueued,
		SourceURL: finalURL,
		Digest:    digest,
		MimeType:  "text/html",
		Filename:  sanitiseFilename(parsed.Host),
		CreatedAt: now,
		UpdatedAt: now,
	}
	if err := s.store.CreateAsset(r.Context(), asset); err != nil {
		if errors.Is(err, store.ErrDuplicateDigest) {
			if existing, findErr := s.store.FindAssetByDigest(r.Context(), tenant, digest); findErr == nil {
				writeJSON(w, http.StatusOK, map[string]any{
					"asset_id": existing.ID, "status": existing.Status, "deduped": true,
				})
				return
			}
		}
		s.internalError(w, r, "create landing_page asset", err)
		return
	}
	s.metrics.AssetsCreated.WithLabelValues("landing_page").Inc()
	s.logger.Info("landing_page asset created", "tenant", tenant, "asset_id", asset.ID,
		"url", finalURL, "request_id", requestIDFrom(r))
	if err := s.queue.Enqueue(r.Context(), model.AuditTask{AssetID: asset.ID, TenantID: tenant}); err != nil {
		s.logger.Error("failed to enqueue landing_page audit", "asset_id", asset.ID, "err", err)
	}
	writeJSON(w, http.StatusAccepted, map[string]any{
		"asset_id": asset.ID, "status": model.AssetStatusQueued, "deduped": false,
	})
}

// enforceQuota reserves weighted quota points for one submission (FR-13). It writes the
// rejection response itself and reports whether a response was written.
func (s *Server) enforceQuota(w http.ResponseWriter, r *http.Request, tenant, assetType string) bool {
	if s.quota == nil {
		return false
	}
	reservation := s.quota.Reserve(r.Context(), tenant, assetType)
	if reservation.Allowed {
		return false
	}
	switch reservation.Reason {
	case "TENANT_NOT_FOUND":
		writeError(w, r, http.StatusForbidden, "TENANT_NOT_FOUND", "tenant is not provisioned", nil)
	case "TENANT_DISABLED":
		writeError(w, r, http.StatusForbidden, "TENANT_DISABLED", "tenant is disabled", nil)
	case "QUOTA_EXCEEDED":
		seconds := int(reservation.RetryAfter.Seconds())
		if seconds < 1 {
			seconds = 60
		}
		w.Header().Set("Retry-After", strconv.Itoa(seconds))
		writeError(w, r, http.StatusTooManyRequests, "QUOTA_EXCEEDED",
			"daily quota exceeded", map[string]any{
				"used":        reservation.Used,
				"limit":       reservation.Limit,
				"weight":      reservation.Weight,
				"retry_after": seconds,
			})
	default:
		writeError(w, r, http.StatusTooManyRequests, "QUOTA_EXCEEDED",
			"submission rejected by quota", nil)
	}
	return true
}
func (s *Server) uploadChunk(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)
	assetID := chi.URLParam(r, "assetID")
	indexRaw := chi.URLParam(r, "index")
	index, err := strconv.Atoi(indexRaw)
	if err != nil || index < 0 {
		writeError(w, r, http.StatusBadRequest, "INVALID_CHUNK_INDEX",
			"chunk index must be a non-negative integer", nil)
		return
	}

	session, err := s.store.GetUploadSessionByAsset(r.Context(), tenant, assetID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, "ASSET_NOT_FOUND", "asset not found", nil)
			return
		}
		s.internalError(w, r, "load upload session", err)
		return
	}
	if session.Status != "uploading" {
		writeError(w, r, http.StatusConflict, "UPLOAD_NOT_OPEN",
			"upload session is "+session.Status, nil)
		return
	}
	if index >= session.TotalChunks {
		writeError(w, r, http.StatusBadRequest, "INVALID_CHUNK_INDEX",
			fmt.Sprintf("chunk index %d out of range [0,%d)", index, session.TotalChunks), nil)
		return
	}

	expected := int64(session.ChunkSize)
	if index == session.TotalChunks-1 {
		expected = s.assetSize(r.Context(), tenant, assetID) - int64(session.ChunkSize)*int64(session.TotalChunks-1)
	}
	if expected < 0 {
		expected = 0
	}

	body := http.MaxBytesReader(w, r.Body, expected+1)
	data, err := io.ReadAll(body)
	if err != nil {
		writeError(w, r, http.StatusRequestEntityTooLarge, "CHUNK_TOO_LARGE",
			fmt.Sprintf("chunk %d exceeds expected size %d", index, expected), nil)
		return
	}
	if int64(len(data)) != expected {
		writeError(w, r, http.StatusBadRequest, "INVALID_CHUNK_SIZE",
			fmt.Sprintf("chunk %d expected %d bytes, got %d", index, expected, len(data)), nil)
		return
	}

	key := chunkKey(tenant, assetID, index)
	if err := s.objects.PutChunk(r.Context(), key, bytes.NewReader(data), int64(len(data)),
		"application/octet-stream"); err != nil {
		s.internalError(w, r, "store chunk", err)
		return
	}
	count, err := s.store.AddChunk(r.Context(), tenant, assetID, index, int64(len(data)))
	if err != nil {
		s.internalError(w, r, "record chunk", err)
		return
	}
	s.metrics.UploadBytes.Add(float64(len(data)))
	w.Header().Set("X-Chunks-Received", strconv.Itoa(count))
	w.Header().Set("X-Chunks-Total", strconv.Itoa(session.TotalChunks))
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) completeAsset(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)
	assetID := chi.URLParam(r, "assetID")
	ctx := r.Context()

	session, err := s.store.GetUploadSessionByAsset(ctx, tenant, assetID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, "ASSET_NOT_FOUND", "asset not found", nil)
			return
		}
		s.internalError(w, r, "load upload session", err)
		return
	}

	asset, err := s.store.GetAsset(ctx, tenant, assetID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, "ASSET_NOT_FOUND", "asset not found", nil)
			return
		}
		s.internalError(w, r, "load asset", err)
		return
	}

	// Idempotent complete: retries after a successful complete return the current state.
	if session.Status == "completed" {
		writeJSON(w, http.StatusOK, map[string]any{
			"asset_id": asset.ID,
			"status":   asset.Status,
			"deduped":  asset.Status == model.AssetStatusDuplicate,
			"digest":   asset.Digest,
		})
		return
	}

	received, err := s.store.CountChunks(ctx, tenant, assetID)
	if err != nil {
		s.internalError(w, r, "count chunks", err)
		return
	}
	if received != session.TotalChunks {
		writeError(w, r, http.StatusConflict, "INCOMPLETE_UPLOAD",
			"not all chunks were received", map[string]any{
				"received": received,
				"total":    session.TotalChunks,
			})
		return
	}

	keys := make([]string, 0, session.TotalChunks)
	for i := 0; i < session.TotalChunks; i++ {
		keys = append(keys, chunkKey(tenant, assetID, i))
	}
	finalKey := finalObjectKey(tenant, assetID)
	if err := s.objects.Compose(ctx, finalKey, keys); err != nil {
		s.internalError(w, r, "compose object", err)
		return
	}

	digest, size, err := s.digestObject(ctx, finalKey)
	if err != nil {
		s.internalError(w, r, "digest object", err)
		return
	}

	// Content-level dedupe, per tenant (never across tenants: cross-tenant membership
	// would leak the existence of another tenant's asset).
	if existing, findErr := s.store.FindAssetByDigest(ctx, tenant, digest); findErr == nil && existing.ID != assetID {
		_ = s.objects.Remove(ctx, finalKey)
		if err := s.store.MarkAssetDuplicate(ctx, tenant, assetID, existing.ID); err != nil {
			s.internalError(w, r, "mark duplicate", err)
			return
		}
		_ = s.store.CompleteSession(ctx, tenant, assetID)
		s.cleanupChunks(ctx, keys)
		s.logger.Info("upload deduplicated", "tenant", tenant, "asset_id", assetID,
			"existing_asset_id", existing.ID, "digest", digest)
		writeJSON(w, http.StatusOK, map[string]any{
			"asset_id":   existing.ID,
			"status":     model.AssetStatusDuplicate,
			"deduped":    true,
			"digest":     digest,
			"size_bytes": size,
		})
		return
	}

	if err := s.store.MarkAssetQueued(ctx, tenant, assetID, finalKey, digest); err != nil {
		if errors.Is(err, store.ErrDuplicateDigest) {
			// Lost a race with a concurrent complete of identical content.
			if existing, findErr := s.store.FindAssetByDigest(ctx, tenant, digest); findErr == nil {
				_ = s.objects.Remove(ctx, finalKey)
				_ = s.store.MarkAssetDuplicate(ctx, tenant, assetID, existing.ID)
				_ = s.store.CompleteSession(ctx, tenant, assetID)
				s.cleanupChunks(ctx, keys)
				writeJSON(w, http.StatusOK, map[string]any{
					"asset_id": existing.ID,
					"status":   model.AssetStatusDuplicate,
					"deduped":  true,
				})
				return
			}
		}
		s.internalError(w, r, "mark asset queued", err)
		return
	}
	if err := s.store.CompleteSession(ctx, tenant, assetID); err != nil {
		s.internalError(w, r, "complete upload session", err)
		return
	}
	s.cleanupChunks(ctx, keys)

	if err := s.queue.Enqueue(ctx, model.AuditTask{
		AssetID:  assetID,
		TenantID: tenant,
		Attempt:  0,
	}); err != nil {
		// The asset is stored and recorded; failing to enqueue is recoverable by the
		// sweeper (M3). Do not fail the upload.
		s.logger.Error("failed to enqueue audit task", "tenant", tenant, "asset_id", assetID, "err", err)
		writeJSON(w, http.StatusAccepted, map[string]any{
			"asset_id":   assetID,
			"status":     model.AssetStatusQueued,
			"digest":     digest,
			"size_bytes": size,
			"warning":    "audit task enqueue deferred; asset will be picked up by the sweeper",
		})
		return
	}

	writeJSON(w, http.StatusAccepted, map[string]any{
		"asset_id":   assetID,
		"status":     model.AssetStatusQueued,
		"digest":     digest,
		"size_bytes": size,
	})
}

func (s *Server) getAsset(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)
	assetID := chi.URLParam(r, "assetID")
	asset, err := s.store.GetAsset(r.Context(), tenant, assetID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, "ASSET_NOT_FOUND", "asset not found", nil)
			return
		}
		s.internalError(w, r, "load asset", err)
		return
	}
	resp := struct {
		*model.Asset
		LatestJudgmentID string `json:"latest_judgment_id,omitempty"`
	}{Asset: asset}
	if judgment, err := s.store.LatestJudgment(r.Context(), tenant, assetID); err == nil {
		resp.LatestJudgmentID = judgment.ID
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) downloadURL(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)
	assetID := chi.URLParam(r, "assetID")
	asset, err := s.store.GetAsset(r.Context(), tenant, assetID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, "ASSET_NOT_FOUND", "asset not found", nil)
			return
		}
		s.internalError(w, r, "load asset", err)
		return
	}
	if asset.ObjectKey == "" {
		writeError(w, r, http.StatusConflict, "ASSET_NOT_READY",
			"asset has no stored object yet (upload not completed)", nil)
		return
	}
	url, err := s.objects.PresignGet(r.Context(), asset.ObjectKey, downloadURLTTL)
	if err != nil {
		s.internalError(w, r, "presign download", err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"asset_id":           asset.ID,
		"url":                url,
		"expires_in_seconds": int(downloadURLTTL.Seconds()),
	})
}

func (s *Server) getJudgment(w http.ResponseWriter, r *http.Request) {
	tenant := tenantFrom(r)
	judgmentID := chi.URLParam(r, "judgmentID")
	judgment, err := s.store.GetJudgment(r.Context(), tenant, judgmentID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, r, http.StatusNotFound, "JUDGMENT_NOT_FOUND", "judgment not found", nil)
			return
		}
		s.internalError(w, r, "load judgment", err)
		return
	}
	if judgment.Violations == nil {
		judgment.Violations = make([]model.Violation, 0)
	}
	writeJSON(w, http.StatusOK, judgment)
}

func (s *Server) healthz(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) readyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if s.ready != nil {
		if err := s.ready(ctx); err != nil {
			writeJSON(w, http.StatusServiceUnavailable, map[string]any{
				"status": "degraded",
				"error":  err.Error(),
			})
			return
		}
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// --- helpers ---

func (s *Server) internalError(w http.ResponseWriter, r *http.Request, what string, err error) {
	s.logger.Error(what+" failed", "err", err, "path", r.URL.Path, "request_id", requestIDFrom(r))
	writeError(w, r, http.StatusInternalServerError, "INTERNAL_ERROR", what+" failed", nil)
}

func (s *Server) assetSize(ctx context.Context, tenant, assetID string) int64 {
	asset, err := s.store.GetAsset(ctx, tenant, assetID)
	if err != nil {
		return 0
	}
	return asset.SizeBytes
}

func (s *Server) digestObject(ctx context.Context, key string) (string, int64, error) {
	reader, err := s.objects.Open(ctx, key)
	if err != nil {
		return "", 0, err
	}
	defer reader.Close()
	hasher := sha256.New()
	size, err := io.Copy(hasher, reader)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(hasher.Sum(nil)), size, nil
}

func (s *Server) cleanupChunks(ctx context.Context, keys []string) {
	for _, key := range keys {
		if err := s.objects.Remove(ctx, key); err != nil {
			s.logger.Warn("failed to clean up chunk object", "key", key, "err", err)
		}
	}
}

func chunkKey(tenant, assetID string, index int) string {
	return fmt.Sprintf("tmp/%s/%s/%06d", tenant, assetID, index)
}

func finalObjectKey(tenant, assetID string) string {
	return fmt.Sprintf("assets/%s/%s/original", tenant, assetID)
}

func isAllowedAssetType(t string) bool {
	switch t {
	case "image", "video", "text", "landing_page":
		return true
	default:
		return false
	}
}

func normaliseMime(mime string) string {
	mime = strings.TrimSpace(mime)
	if mime == "" || len(mime) > 128 {
		return "application/octet-stream"
	}
	return mime
}

// sanitiseFilename strips path components and control characters so that a hostile
// filename can never influence an object key. The object key itself is derived from
// ids only; the filename is metadata.
func sanitiseFilename(name string) string {
	name = strings.TrimSpace(name)
	if name == "" {
		return "unnamed"
	}
	name = strings.ReplaceAll(name, "\\", "/")
	if i := strings.LastIndex(name, "/"); i >= 0 {
		name = name[i+1:]
	}
	var b strings.Builder
	for _, r := range name {
		if r < 32 || r == 127 {
			continue
		}
		b.WriteRune(r)
	}
	out := b.String()
	runes := []rune(out)
	if len(runes) > maxFilenameRunes {
		out = string(runes[:maxFilenameRunes])
	}
	if out == "" || out == "." || out == ".." {
		return "unnamed"
	}
	return out
}
