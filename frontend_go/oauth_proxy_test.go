package main

import (
	"crypto/tls"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
)

func TestOAuth2ProxyHandlerRewritesBasePath(t *testing.T) {
	var receivedPath, receivedQuery, forwardedHost, forwardedProto string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		receivedQuery = r.URL.RawQuery
		forwardedHost = r.Header.Get("X-Forwarded-Host")
		forwardedProto = r.Header.Get("X-Forwarded-Proto")
		w.WriteHeader(http.StatusNoContent)
	}))
	defer backend.Close()

	targetURL, err := url.Parse(backend.URL + "/auth/oauth")
	if err != nil {
		t.Fatalf("parse target: %v", err)
	}

	proxy := newOAuth2ReverseProxy(targetURL)
	handler := oauth2ProxyHandler(proxy)

	req := httptest.NewRequest(http.MethodGet, "https://frontend.local/oauth2/start?rd=%2Fportal.html", nil)
	req.TLS = &tls.ConnectionState{}

	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("expected status %d, got %d", http.StatusNoContent, rr.Code)
	}
	if receivedPath != "/auth/oauth/start" {
		t.Fatalf("expected backend path /auth/oauth/start, got %q", receivedPath)
	}
	if receivedQuery != "rd=%2Fportal.html" {
		t.Fatalf("expected backend query rd=%%2Fportal.html, got %q", receivedQuery)
	}
	if forwardedHost != "frontend.local" {
		t.Fatalf("expected X-Forwarded-Host frontend.local, got %q", forwardedHost)
	}
	if forwardedProto != "https" {
		t.Fatalf("expected X-Forwarded-Proto https, got %q", forwardedProto)
	}
}

func TestOAuth2ProxyHandlerKeepsPathWithoutBase(t *testing.T) {
	var receivedPath string
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		receivedPath = r.URL.Path
		w.WriteHeader(http.StatusNoContent)
	}))
	defer backend.Close()

	targetURL, err := url.Parse(backend.URL)
	if err != nil {
		t.Fatalf("parse target: %v", err)
	}

	proxy := newOAuth2ReverseProxy(targetURL)
	handler := oauth2ProxyHandler(proxy)

	req := httptest.NewRequest(http.MethodGet, "http://frontend.local/oauth2/callback", nil)

	rr := httptest.NewRecorder()
	handler.ServeHTTP(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("expected status %d, got %d", http.StatusNoContent, rr.Code)
	}
	if receivedPath != "/oauth2/callback" {
		t.Fatalf("expected backend path /oauth2/callback, got %q", receivedPath)
	}
}
