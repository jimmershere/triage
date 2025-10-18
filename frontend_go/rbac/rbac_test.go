package rbac

import (
	"net/http/httptest"
	"testing"
)

func TestIdentityFromRequestPrimaryHeaders(t *testing.T) {
	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("X-Auth-Request-User", "alice")
	r.Header.Set("X-Auth-Request-Groups", "hedi-view,hedi-submit")

	id := IdentityFromRequest(r)
	if id.Username != "alice" {
		t.Fatalf("expected username 'alice', got %q", id.Username)
	}
	if !id.groups[RoleView] || !id.groups[RoleSubmit] {
		t.Fatalf("expected groups to include %q and %q, got %#v", RoleView, RoleSubmit, id.groups)
	}
}

func TestIdentityFromRequestFallbackHeaders(t *testing.T) {
	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("X-Forwarded-User", "bob")
	r.Header.Set("X-Forwarded-Groups", "hedi-admin")

	id := IdentityFromRequest(r)
	if id.Username != "bob" {
		t.Fatalf("expected username 'bob', got %q", id.Username)
	}
	if !id.groups[RoleAdmin] {
		t.Fatalf("expected groups to include %q, got %#v", RoleAdmin, id.groups)
	}
}
