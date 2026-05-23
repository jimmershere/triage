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
		Groups:   []string{"triage-view", "triage-submit"},
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
		Groups:   []string{"triage-view"},
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

func TestProfileFromRequestAllowSubmit(t *testing.T) {
	original := sessionSecret
	sessionSecret = []byte("test-secret")
	defer func() { sessionSecret = original }()

	cases := []struct {
		name       string
		groups     []string
		wantRole   string
		wantSubmit bool
		wantAdmin  bool
	}{
		{name: "admin", groups: []string{"triage-admin"}, wantRole: "administrator", wantSubmit: true, wantAdmin: true},
		{name: "submitter", groups: []string{"triage-submit"}, wantRole: "submit", wantSubmit: true, wantAdmin: false},
		{name: "viewer", groups: []string{"triage-view"}, wantRole: "view", wantSubmit: false, wantAdmin: false},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			claims := sessionClaims{
				Username: "jane",
				Groups:   tc.groups,
				Expires:  time.Now().Add(time.Hour).Unix(),
			}
			token := signTestSession(t, claims, sessionSecret)
			req := httptest.NewRequest(http.MethodGet, "http://example.com/portal/837.html", nil)
			req.AddCookie(&http.Cookie{Name: sessionCookieName, Value: token})

			profile := profileFromRequest(req)
			if profile == nil {
				t.Fatalf("expected profile for %s", tc.name)
			}
			if profile.Role != tc.wantRole {
				t.Fatalf("expected role %s, got %s", tc.wantRole, profile.Role)
			}
			if profile.AllowSubmit != tc.wantSubmit {
				t.Fatalf("expected AllowSubmit=%v, got %v", tc.wantSubmit, profile.AllowSubmit)
			}
			if profile.AllowAdmin != tc.wantAdmin {
				t.Fatalf("expected AllowAdmin=%v, got %v", tc.wantAdmin, profile.AllowAdmin)
			}
		})
	}
}
