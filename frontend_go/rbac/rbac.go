package rbac

import (
	"net/http"
	"strings"
)

const (
	RoleAdmin  = "triage-admin"
	RoleSubmit = "triage-submit"
	RoleView   = "triage-view"

	headerUserPrimary    = "X-Auth-Request-User"
	headerUserFallback   = "X-Forwarded-User"
	headerGroupsPrimary  = "X-Auth-Request-Groups"
	headerGroupsFallback = "X-Forwarded-Groups"
)

func firstHeader(r *http.Request, names ...string) string {
	for _, name := range names {
		if v := strings.TrimSpace(r.Header.Get(name)); v != "" {
			return v
		}
	}
	return ""
}

type Identity struct {
	Username string
	groups   map[string]bool
}

// ResolveIdentity provides a hook for callers to override how identities are
// extracted from each request. By default it reads the standard oauth2-proxy
// headers, but the frontend can replace it with a cookie-backed resolver when
// running alongside the bundled RBAC service.
var ResolveIdentity = IdentityFromRequest

// NewIdentity constructs an Identity from the provided username and groups.
// Group names are normalized by trimming whitespace and dropping empty values.
func NewIdentity(username string, groups []string) Identity {
	cleaned := strings.TrimSpace(username)
	out := make(map[string]bool, len(groups))
	for _, g := range groups {
		if trimmed := strings.TrimSpace(g); trimmed != "" {
			out[trimmed] = true
		}
	}
	return Identity{Username: cleaned, groups: out}
}

func groupsFromRequest(r *http.Request) map[string]bool {
	raw := firstHeader(r, headerGroupsPrimary, headerGroupsFallback)
	if raw == "" {
		return map[string]bool{}
	}
	parts := strings.Split(raw, ",")
	groups := make(map[string]bool, len(parts))
	for _, part := range parts {
		g := strings.TrimSpace(part)
		if g != "" {
			groups[g] = true
		}
	}
	return groups
}

func IdentityFromRequest(r *http.Request) Identity {
	return Identity{
		Username: firstHeader(r, headerUserPrimary, headerUserFallback),
		groups:   groupsFromRequest(r),
	}
}

func (id Identity) Groups() map[string]bool {
	copy := make(map[string]bool, len(id.groups))
	for k, v := range id.groups {
		copy[k] = v
	}
	return copy
}

func (id Identity) IsAdmin() bool {
	return id.groups[RoleAdmin]
}

func (id Identity) IsSubmitter() bool {
	return id.groups[RoleSubmit] || id.IsAdmin()
}

func (id Identity) IsViewer() bool {
	if id.groups[RoleView] {
		return true
	}
	if id.IsSubmitter() || id.IsAdmin() {
		return true
	}
	return false
}

func RequireViewer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !ResolveIdentity(r).IsViewer() {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func RequireSubmitterForWrite(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := ResolveIdentity(r)
		switch r.Method {
		case http.MethodGet, http.MethodHead, http.MethodOptions:
			if !id.IsViewer() {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
		default:
			if !id.IsSubmitter() {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
		}
		next.ServeHTTP(w, r)
	})
}

func RequireAdmin(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !ResolveIdentity(r).IsAdmin() {
			http.Error(w, "admin only", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}
