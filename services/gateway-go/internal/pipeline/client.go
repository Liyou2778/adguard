package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"mime/multipart"
	"net/http"
	"strings"
	"time"
)

// ErrUnavailable is returned when no pipeline backend is configured or reachable.
var ErrUnavailable = errors.New("pipeline service unavailable")

// Violation is the clause-level evidence returned by the M3 rule judge.
type Violation struct {
	RuleID         string  `json:"rule_id"`
	Family         string  `json:"family"`
	Canonical      string  `json:"canonical"`
	VariantKind    string  `json:"variant_kind"`
	Span           []int   `json:"span"`
	Evidence       string  `json:"evidence"`
	ClauseID       string  `json:"clause_id"`
	RegulationID   string  `json:"regulation_id"`
	Article        string  `json:"article"`
	ItemLabel      string  `json:"item_label"`
	ClauseRevision string  `json:"clause_revision"`
	ClauseText     string  `json:"clause_text"`
	PenaltyLawRef  string  `json:"penalty_law_ref"`
	PenaltyMin     *int    `json:"penalty_min"`
	PenaltyMax     *int    `json:"penalty_max"`
	Confidence     float64 `json:"confidence"`
}

// JudgeResponse mirrors the Python /v1/judge contract.
type JudgeResponse struct {
	Decision          string      `json:"decision"`
	Reason            string      `json:"reason"`
	Violations        []Violation `json:"violations"`
	JudgeVersion      string      `json:"judge_version"`
	RetrievalWarnings []string    `json:"retrieval_warnings"`
	DroppedStale      int         `json:"dropped_stale"`
	IndexVersion      string      `json:"index_version"`
}

// Client talks to the Python compliance pipeline (M2 matcher + M3 retrieval/judge).
type Client struct {
	baseURL string
	http    *http.Client
	logger  *slog.Logger
}

func New(baseURL string, timeout time.Duration, logger *slog.Logger) *Client {
	if strings.TrimSpace(baseURL) == "" {
		return nil
	}
	if timeout <= 0 {
		timeout = 5 * time.Second
	}
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    &http.Client{Timeout: timeout},
		logger:  logger,
	}
}

// Judge asks the pipeline for a rule-based judgment.
func (c *Client) Judge(ctx context.Context, text string) (*JudgeResponse, error) {
	if c == nil {
		return nil, ErrUnavailable
	}
	payload, err := json.Marshal(map[string]string{"text": text})
	if err != nil {
		return nil, fmt.Errorf("encode judge request: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/judge", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%w: pipeline returned %d: %s", ErrUnavailable, resp.StatusCode, string(body))
	}
	var parsed JudgeResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return nil, fmt.Errorf("decode judge response: %w", err)
	}
	return &parsed, nil
}

// Healthy checks the pipeline liveness endpoint (used by ops tooling, not by the request path).
func (c *Client) Healthy(ctx context.Context) error {
	if c == nil {
		return ErrUnavailable
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/healthz", nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("pipeline health %d", resp.StatusCode)
	}
	return nil
}

// --------------------------------------------------------------------------- M7 parsing
// ScanResult mirrors the Python /v1/scan contract.
type ScanResult struct {
	Status    string   `json:"status"` // clean | infected | unknown
	Engine    string   `json:"engine"`
	Signature string   `json:"signature"`
	Warnings  []string `json:"warnings"`
}

// ParseElement/ParseLine mirror the Python /v1/parse element contract (bbox is
// [x0,y0,x1,y1] in original image pixels).
type ParseElement struct {
	Text       string    `json:"text"`
	BBox       []float64 `json:"bbox"`
	Confidence float64   `json:"confidence"`
	Role       string    `json:"role"`
	Frame      *int      `json:"frame"`
}

// ParsedAsset mirrors the Python /v1/parse response.
type ParsedAsset struct {
	AssetType  string         `json:"asset_type"`
	Text       string         `json:"text"`
	Degraded   bool           `json:"degraded"`
	Warnings   []string       `json:"warnings"`
	Scan       *ScanResult    `json:"scan"`
	Elements   []ParseElement `json:"elements"`
	Lines      []ParseElement `json:"lines"`
	RoleCounts map[string]int `json:"role_counts"`
	Frames     int            `json:"frames"`
	Meta       map[string]any `json:"meta"`
}

// ParseRequest carries either uploaded bytes (text/image/video) or a landing-page URL.
type ParseRequest struct {
	AssetType string
	MimeType  string
	Filename  string
	SourceURL string
	Data      []byte
}

// Scan asks the pipeline to run antivirus checks on one asset.
func (c *Client) Scan(ctx context.Context, data []byte, filename string) (*ScanResult, error) {
	if c == nil {
		return nil, ErrUnavailable
	}
	body, contentType, err := multipartPayload(nil, "file", filename, data)
	if err != nil {
		return nil, err
	}
	raw, err := c.post(ctx, "/v1/scan", contentType, body, 30*time.Second)
	if err != nil {
		return nil, err
	}
	var out ScanResult
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("decode scan response: %w", err)
	}
	return &out, nil
}

// Parse asks the pipeline to parse an uploaded asset (OCR/layout/video keyframes) or a URL.
func (c *Client) Parse(ctx context.Context, request ParseRequest) (*ParsedAsset, error) {
	if c == nil {
		return nil, ErrUnavailable
	}
	fields := map[string]string{
		"asset_type": request.AssetType,
		"mime_type":  request.MimeType,
		"filename":   request.Filename,
		"source_url": request.SourceURL,
	}
	var body []byte
	var contentType string
	var err error
	if request.AssetType == "landing_page" {
		body, contentType, err = multipartPayload(fields, "", "", nil)
	} else {
		body, contentType, err = multipartPayload(fields, "file", request.Filename, request.Data)
	}
	if err != nil {
		return nil, err
	}
	raw, err := c.post(ctx, "/v1/parse", contentType, body, 180*time.Second)
	if err != nil {
		return nil, err
	}
	var out ParsedAsset
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("decode parse response: %w", err)
	}
	return &out, nil
}

func (c *Client) post(ctx context.Context, path, contentType string, body []byte, timeout time.Duration) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", contentType)
	client := c.http
	if timeout > c.http.Timeout {
		client = &http.Client{Timeout: timeout}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 16<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%w: %s returned %d: %s", ErrUnavailable, path, resp.StatusCode, string(raw))
	}
	return raw, nil
}

func multipartPayload(fields map[string]string, fileField, filename string, data []byte) ([]byte, string, error) {
	buf := &bytes.Buffer{}
	writer := multipart.NewWriter(buf)
	for key, value := range fields {
		if value == "" {
			continue
		}
		if err := writer.WriteField(key, value); err != nil {
			return nil, "", fmt.Errorf("encode multipart field %s: %w", key, err)
		}
	}
	if fileField != "" {
		part, err := writer.CreateFormFile(fileField, filename)
		if err != nil {
			return nil, "", fmt.Errorf("encode multipart file: %w", err)
		}
		if _, err := part.Write(data); err != nil {
			return nil, "", fmt.Errorf("encode multipart file body: %w", err)
		}
	}
	if err := writer.Close(); err != nil {
		return nil, "", err
	}
	return buf.Bytes(), writer.FormDataContentType(), nil
}

// --------------------------------------------------------------------------- M10 consistency (FR-6)
// ConsistencyConflict mirrors one Python /v1/consistency conflict item.
type ConsistencyConflict struct {
	Attribute       string    `json:"attribute"`
	CaptionValue    string    `json:"caption_value"`
	ImageValue      string    `json:"image_value"`
	EvidenceCaption string    `json:"evidence_caption"`
	EvidenceImage   string    `json:"evidence_image"`
	Confidence      float64   `json:"confidence"`
	Source          string    `json:"source"`
	SpanImage       []int     `json:"span_image"`
	BBox            []float64 `json:"bbox"`
}

// ConsistencyResult mirrors the Python /v1/consistency response.
type ConsistencyResult struct {
	Conflicts []ConsistencyConflict `json:"conflicts"`
	Degraded  bool                  `json:"degraded"`
	LLMUsed   bool                  `json:"llm_used"`
	Warnings  []string              `json:"warnings"`
	Engine    string                `json:"engine"`
}

type consistencyLine struct {
	Text string    `json:"text"`
	BBox []float64 `json:"bbox,omitempty"`
	Role string    `json:"role,omitempty"`
}

// Consistency asks the pipeline to compare a merchant caption against OCR text (FR-6).
func (c *Client) Consistency(ctx context.Context, caption, imageText string,
	lines []ParseElement) (*ConsistencyResult, error) {
	if c == nil {
		return nil, ErrUnavailable
	}
	requestLines := make([]consistencyLine, 0, len(lines))
	for _, line := range lines {
		requestLines = append(requestLines, consistencyLine{Text: line.Text, BBox: line.BBox,
			Role: line.Role})
	}
	payload, err := json.Marshal(map[string]any{
		"caption":    caption,
		"image_text": imageText,
		"lines":      requestLines,
	})
	if err != nil {
		return nil, fmt.Errorf("encode consistency request: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/consistency",
		bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json; charset=utf-8")
	client := c.http
	timeout := 90 * time.Second
	if timeout > c.http.Timeout {
		client = &http.Client{Timeout: timeout}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%w: /v1/consistency returned %d: %s", ErrUnavailable,
			resp.StatusCode, string(body))
	}
	var parsed ConsistencyResult
	if err := json.Unmarshal(body, &parsed); err != nil {
		return nil, fmt.Errorf("decode consistency response: %w", err)
	}
	return &parsed, nil
}
