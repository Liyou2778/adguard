package auth

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"testing"
	"time"
)

func signTestToken(t *testing.T, secret string, claims map[string]any) string {
	t.Helper()
	header, _ := json.Marshal(map[string]any{"alg": "HS256", "typ": "JWT"})
	payload, _ := json.Marshal(claims)
	headerPart := base64.RawURLEncoding.EncodeToString(header)
	payloadPart := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(headerPart + "." + payloadPart))
	return headerPart + "." + payloadPart + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func TestVerifyValidTenantToken(t *testing.T) {
	secret := "test-secret"
	token := signTestToken(t, secret, map[string]any{
		"sub": "user-1", "tenant_id": "demo-platform", "username": "merchant",
		"role": "owner", "exp": time.Now().Add(time.Hour).Unix(),
	})
	claims, err := Verify(token, secret)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if claims.TenantID != "demo-platform" || !claims.TenantUser() {
		t.Fatalf("unexpected claims: %+v", claims)
	}
}

func TestVerifyRejectsTamperedExpiredAndWrongSecret(t *testing.T) {
	secret := "test-secret"
	valid := signTestToken(t, secret, map[string]any{
		"sub": "user-1", "tenant_id": "demo-platform", "role": "owner",
		"exp": time.Now().Add(time.Hour).Unix(),
	})
	if _, err := Verify(valid, "other-secret"); err == nil {
		t.Fatal("wrong secret must be rejected")
	}
	if _, err := Verify(valid+"x", secret); err == nil {
		t.Fatal("tampered signature must be rejected")
	}
	expired := signTestToken(t, secret, map[string]any{
		"sub": "user-1", "tenant_id": "demo-platform", "role": "owner",
		"exp": time.Now().Add(-time.Minute).Unix(),
	})
	if _, err := Verify(expired, secret); err == nil {
		t.Fatal("expired token must be rejected")
	}
	if _, err := Verify(valid, ""); err == nil {
		t.Fatal("empty secret must be rejected")
	}
}

func TestAdminTokenIsNotATenantUser(t *testing.T) {
	secret := "test-secret"
	token := signTestToken(t, secret, map[string]any{
		"sub": "admin-1", "username": "admin", "role": "admin",
		"exp": time.Now().Add(time.Hour).Unix(),
	})
	claims, err := Verify(token, secret)
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if claims.TenantUser() {
		t.Fatal("admin token must not be accepted for tenant asset APIs")
	}
}
