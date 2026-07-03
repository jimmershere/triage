package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"strings"
	"testing"
	"time"

	"triage/frontend_go/rbac"
)

func TestSecretInjectingProxyRoutesRequireAuth(t *testing.T) {
	originalSharedSecret := sharedSecret
	sharedSecret = "backend-only-secret"
	defer func() { sharedSecret = originalSharedSecret }()

	backendHit := false
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		backendHit = true
		w.WriteHeader(http.StatusNoContent)
	}))
	defer backend.Close()

	targetURL, err := url.Parse(backend.URL)
	if err != nil {
		t.Fatalf("parse backend URL: %v", err)
	}
	// Exactly how main.go now wires /ops, /partners, /era, /helpdesk, /turbo.
	handler := rbac.RequireSubmitterForWrite(apiProxyHandler(httputil.NewSingleHostReverseProxy(targetURL)))

	cases := []struct{ method, path string }{
		{http.MethodGet, "http://frontend.local/ops/summary"},
		{http.MethodGet, "http://frontend.local/partners"},
		{http.MethodPost, "http://frontend.local/partners"},
		{http.MethodGet, "http://frontend.local/era"},
	}
	for _, c := range cases {
		req := httptest.NewRequest(c.method, c.path, nil)
		rr := httptest.NewRecorder()
		handler.ServeHTTP(rr, req)
		if rr.Code != http.StatusForbidden {
			t.Fatalf("%s %s: expected 403 for unauthenticated request, got %d", c.method, c.path, rr.Code)
		}
	}
	if backendHit {
		t.Fatal("unauthenticated request reached the backend through the secret-injecting proxy")
	}
}

func TestConfigHandlerDoesNotExposeSharedSecret(t *testing.T) {
	originalProxy := apiProxyEnabled
	originalRawAPIBase := rawAPIBase
	originalSharedSecret := sharedSecret
	apiProxyEnabled = true
	rawAPIBase = "http://api.internal:8000"
	sharedSecret = "test-shared-secret"
	defer func() {
		apiProxyEnabled = originalProxy
		rawAPIBase = originalRawAPIBase
		sharedSecret = originalSharedSecret
	}()

	req := httptest.NewRequest(http.MethodGet, "http://frontend.local/config.js", nil)
	rr := httptest.NewRecorder()
	configHandler(rr, req)

	body := rr.Body.String()
	if strings.Contains(body, "TRIAGE_SHARED_SECRET") || strings.Contains(body, "test-shared-secret") {
		t.Fatalf("config.js exposed shared secret: %s", body)
	}
	if !strings.Contains(body, `window.TRIAGE_API_BASE = "";`) {
		t.Fatalf("expected proxied API base to be relative/empty, got %s", body)
	}
	if !strings.Contains(body, `window.TRIAGE_INGEST_URL = "/ingest";`) {
		t.Fatalf("expected proxied ingest URL, got %s", body)
	}
}

func TestAPIProxyInjectsSecretAndStripsCookie(t *testing.T) {
	originalSessionSecret := sessionSecret
	originalSharedSecret := sharedSecret
	sessionSecret = []byte("test-session-secret")
	sharedSecret = "backend-only-secret"
	defer func() {
		sessionSecret = originalSessionSecret
		sharedSecret = originalSharedSecret
	}()

	var gotSecret, gotCookie string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotSecret = r.Header.Get("X-TRIAGE-SECRET")
		gotCookie = r.Header.Get("Cookie")
		w.WriteHeader(http.StatusNoContent)
	}))
	defer backend.Close()

	targetURL, err := url.Parse(backend.URL)
	if err != nil {
		t.Fatalf("parse backend URL: %v", err)
	}
	proxy := httputil.NewSingleHostReverseProxy(targetURL)
	handler := apiProxyHandler(proxy)

	token := signTestSession(t, sessionClaims{
		Username: "alice",
		Groups:   []string{"triage-submit"},
		Expires:  time.Now().Add(time.Hour).Unix(),
	}, sessionSecret)
	req := httptest.NewRequest(http.MethodPost, "http://frontend.local/ingest", strings.NewReader("payload"))
	req.AddCookie(&http.Cookie{Name: sessionCookieName, Value: token})
	req.AddCookie(&http.Cookie{Name: "unrelated", Value: "client-cookie"})

	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		data, _ := io.ReadAll(rr.Result().Body)
		t.Fatalf("expected status %d, got %d: %s", http.StatusNoContent, rr.Code, string(data))
	}
	if gotSecret != "backend-only-secret" {
		t.Fatalf("expected injected backend secret, got %q", gotSecret)
	}
	if gotCookie != "" {
		t.Fatalf("expected frontend cookies to be stripped, got %q", gotCookie)
	}
}

func TestLogoutRejectsOpenRedirects(t *testing.T) {
	cases := []string{
		"http://evil.example/callback",
		"//evil.example/callback",
	}
	for _, target := range cases {
		t.Run(target, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, "http://frontend.local/logout?next="+url.QueryEscape(target), nil)
			rr := httptest.NewRecorder()
			logoutHandler(rr, req)

			location := rr.Header().Get("Location")
			if rr.Code != http.StatusFound {
				t.Fatalf("expected redirect, got status %d", rr.Code)
			}
			if !strings.Contains(location, "rd=%2F") {
				t.Fatalf("expected unsafe redirect target to be replaced with root, got %q", location)
			}
			if strings.Contains(location, "evil.example") {
				t.Fatalf("unsafe redirect target leaked into Location: %q", location)
			}
		})
	}
}
