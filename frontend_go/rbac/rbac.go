package rbac

import (
	"net/http"
	"strings"
)

const (
	RoleAdmin  = "hedi-admin"
	RoleSubmit = "hedi-submit"
	RoleView   = "hedi-view"

	headerUser   = "X-Forwarded-User"
	headerGroups = "X-Forwarded-Groups"
)

type Identity struct {
	Username string
	groups   map[string]bool
}

func groupsFromRequest(r *http.Request) map[string]bool {
	raw := r.Header.Get(headerGroups)
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
		Username: strings.TrimSpace(r.Header.Get(headerUser)),
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
		if !IdentityFromRequest(r).IsViewer() {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func RequireSubmitterForWrite(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := IdentityFromRequest(r)
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
		if !IdentityFromRequest(r).IsAdmin() {
			http.Error(w, "admin only", http.StatusForbidden)
			return
		}
		next.ServeHTTP(w, r)
	})
}
