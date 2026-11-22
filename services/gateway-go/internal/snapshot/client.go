package snapshot

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

// Client reads rule snapshots from the Java control plane.
//
// Degradation policy (inherited from P1): a control-plane outage must not stop
// asset ingestion or judging. Judgments are then written with an empty
// rule_snapshot_id and a warning is logged, instead of refusing the request.
type Client struct {
	baseURL string
	http    *http.Client
}

func New(baseURL string, timeout time.Duration) *Client {
	if baseURL == "" {
		return nil
	}
	if timeout <= 0 {
		timeout = 2 * time.Second
	}
	return &Client{baseURL: baseURL, http: &http.Client{Timeout: timeout}}
}

type snapshotResponse struct {
	ID      string `json:"id"`
	Seq     int    `json:"seq"`
	Payload []any  `json:"payload"`
}

// LatestSnapshotID returns the id of the newest snapshot for a tenant.
func (c *Client) LatestSnapshotID(tenantID string) (string, error) {
	if c == nil {
		return "", nil
	}
	endpoint := fmt.Sprintf("%s/internal/rules/snapshot?tenant_id=%s", c.baseURL, url.QueryEscape(tenantID))
	resp, err := c.http.Get(endpoint)
	if err != nil {
		return "", fmt.Errorf("control plane request: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("control plane returned %d: %s", resp.StatusCode, string(body))
	}
	var parsed snapshotResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return "", fmt.Errorf("decode snapshot: %w", err)
	}
	return parsed.ID, nil
}
