// Package auth implements the gateway side of the FR-10 identity contract: it verifies the
// HS256 JWTs issued by the Java control plane and extracts the tenant/user claims.
package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

var (
	// ErrInvalidToken means the token is missing, malformed, tampered with or expired.
	ErrInvalidToken = errors.New("invalid token")
	// ErrNotConfigured means the gateway has no JWT secret configured.
	ErrNotConfigured = errors.New("jwt secret not configured")
)

// Claims are the JWT payload fields shared with the control plane.
type Claims struct {
	Subject  string `json:"sub"`
	TenantID string `json:"tenant_id"`
	Username string `json:"username"`
	Role     string `json:"role"`
	// PwdChange is true while the account still uses a temporary password; asset APIs are
	// rejected until the user changes it (enterprise first-login policy).
	PwdChange bool `json:"pwd_change"`
	IssuedAt int64  `json:"iat"`
	Expires  int64  `json:"exp"`
}

// TenantUser reports whether the token belongs to a merchant user (owner/member).
func (c *Claims) TenantUser() bool {
	return (c.Role == "owner" || c.Role == "member") && strings.TrimSpace(c.TenantID) != ""
}

// Verify validates signature and expiry, returning the claims when valid.
func Verify(token, secret string) (*Claims, error) {
	if strings.TrimSpace(secret) == "" {
		return nil, ErrNotConfigured
	}
	parts := strings.Split(strings.TrimSpace(token), ".")
	if len(parts) != 3 {
		return nil, ErrInvalidToken
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(parts[0] + "." + parts[1]))
	expected := mac.Sum(nil)
	actual, err := base64.RawURLEncoding.DecodeString(parts[2])
	if err != nil || !hmac.Equal(expected, actual) {
		return nil, ErrInvalidToken
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, ErrInvalidToken
	}
	var claims Claims
	if err := json.Unmarshal(payload, &claims); err != nil {
		return nil, ErrInvalidToken
	}
	if claims.Expires > 0 && time.Now().Unix() >= claims.Expires {
		return nil, fmt.Errorf("%w: expired", ErrInvalidToken)
	}
	if strings.TrimSpace(claims.Role) == "" {
		return nil, ErrInvalidToken
	}
	return &claims, nil
}
