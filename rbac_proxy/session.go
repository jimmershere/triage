package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strings"
	"time"
)

type sessionClaims struct {
	Username string   `json:"u"`
	Groups   []string `json:"g"`
	Expires  int64    `json:"exp"`
}

func (c sessionClaims) valid(now time.Time) bool {
	if c.Username == "" {
		return false
	}
	if c.Expires > 0 && now.Unix() > c.Expires {
		return false
	}
	return true
}

func signSession(claims sessionClaims, secret []byte) (string, error) {
	if len(secret) == 0 {
		return "", errors.New("session secret not configured")
	}
	payload, err := json.Marshal(claims)
	if err != nil {
		return "", err
	}
	encoded := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, secret)
	if _, err := mac.Write([]byte(encoded)); err != nil {
		return "", err
	}
	sig := mac.Sum(nil)
	token := encoded + "." + base64.RawURLEncoding.EncodeToString(sig)
	return token, nil
}

func parseSession(raw string, secret []byte) (*sessionClaims, error) {
	if len(secret) == 0 {
		return nil, errors.New("session secret not configured")
	}
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil, errors.New("empty session token")
	}
	parts := strings.Split(raw, ".")
	if len(parts) != 2 {
		return nil, errors.New("invalid session token")
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return nil, err
	}
	sig, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return nil, err
	}
	mac := hmac.New(sha256.New, secret)
	if _, err := mac.Write([]byte(parts[0])); err != nil {
		return nil, err
	}
	expected := mac.Sum(nil)
	if !hmac.Equal(sig, expected) {
		return nil, errors.New("invalid session signature")
	}
	var claims sessionClaims
	if err := json.Unmarshal(payload, &claims); err != nil {
		return nil, err
	}
	return &claims, nil
}
