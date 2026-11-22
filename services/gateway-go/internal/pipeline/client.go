package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
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
