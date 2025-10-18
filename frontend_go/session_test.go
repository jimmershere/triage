package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func signTestSession(t *testing.T, claims sessionClaims, secret []byte) string {
	t.Helper()
	payload, err := json.Marshal(claims)
	if err != nil {
		t.Fatalf("marshal claims: %v", err)
	}
	encoded := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, secret)
	if _, err := mac.Write([]byte(encoded)); err != nil {
		t.Fatalf("write mac: %v", err)
	}
	sig := mac.Sum(nil)
	return encoded + "." + base64.RawURLEncoding.EncodeToString(sig)
}

func TestIdentityFromSessionCookie(t *testing.T) {
	original := sessionSecret
	sessionSecret = []byte("test-secret")
	defer func() { sessionSecret = original }()

	claims := sessionClaims{
		Username: "alice",
		Groups:   []string{"hedi-view", "hedi-submit"},
		Expires:  time.Now().Add(time.Hour).Unix(),
	}
	token := signTestSession(t, claims, sessionSecret)

	req := httptest.NewRequest(http.MethodGet, "http://example.com/portal.html", nil)
	req.AddCookie(&http.Cookie{Name: sessionCookieName, Value: token})

	id := identityFromRequest(req)
	if id.Username != "alice" {
		t.Fatalf("expected username alice, got %q", id.Username)
	}
	if !id.IsSubmitter() {
		t.Fatalf("expected submitter role")
	}
}

func TestIdentityFromExpiredSession(t *testing.T) {
	original := sessionSecret
	sessionSecret = []byte("test-secret")
	defer func() { sessionSecret = original }()

	claims := sessionClaims{
		Username: "alice",
		Groups:   []string{"hedi-view"},
		Expires:  time.Now().Add(-time.Hour).Unix(),
	}
	token := signTestSession(t, claims, sessionSecret)

	req := httptest.NewRequest(http.MethodGet, "http://example.com/portal.html", nil)
	req.AddCookie(&http.Cookie{Name: sessionCookieName, Value: token})

	id := identityFromRequest(req)
	if id.Username != "" {
		t.Fatalf("expected empty username for expired session, got %q", id.Username)
	}
}
