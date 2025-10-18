package main

import (
	"testing"
	"time"
)

func TestSessionRoundTrip(t *testing.T) {
	secret := []byte("super-secret")
	claims := sessionClaims{
		Username: "alice",
		Groups:   []string{"hedi-view", "hedi-submit"},
		Expires:  time.Now().Add(time.Hour).Unix(),
	}
	token, err := signSession(claims, secret)
	if err != nil {
		t.Fatalf("signSession error: %v", err)
	}
	parsed, err := parseSession(token, secret)
	if err != nil {
		t.Fatalf("parseSession error: %v", err)
	}
	if parsed.Username != claims.Username {
		t.Fatalf("expected username %q, got %q", claims.Username, parsed.Username)
	}
	if parsed.Expires != claims.Expires {
		t.Fatalf("expected exp %d, got %d", claims.Expires, parsed.Expires)
	}
	if len(parsed.Groups) != len(claims.Groups) {
		t.Fatalf("expected %d groups, got %d", len(claims.Groups), len(parsed.Groups))
	}
}

func TestInvalidSignature(t *testing.T) {
	secret := []byte("super-secret")
	claims := sessionClaims{Username: "alice", Expires: time.Now().Add(time.Hour).Unix()}
	token, err := signSession(claims, secret)
	if err != nil {
		t.Fatalf("signSession error: %v", err)
	}
	// flip a character
	token += "a"
	if _, err := parseSession(token, secret); err == nil {
		t.Fatalf("expected error for tampered token")
	}
}
