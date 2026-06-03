package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"mime"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"triage/frontend_go/rbac"
)

var (
	publicDir = env("PUBLIC_DIR", "/app/public") // bind-mounted in the container

	rawAPIBase               = strings.TrimRight(env("TRIAGE_API_BASE", ""), "/")
	rawOAuthProxyURL         = strings.TrimSpace(env("TRIAGE_OAUTH2_PROXY_URL", ""))
	rawOAuthProxyInternalURL = strings.TrimSpace(env("TRIAGE_OAUTH2_PROXY_INTERNAL_URL", ""))
	rawOAuthProxyInsecure    = strings.TrimSpace(env("TRIAGE_OAUTH2_PROXY_INSECURE_SKIP_VERIFY", ""))
	sessionSecretRaw         = strings.TrimSpace(env("TRIAGE_SESSION_SECRET", ""))
	sessionSecret            = []byte(sessionSecretRaw)
	backendAPIBase           string
	sharedSecret             = env("TRIAGE_SHARED_SECRET", "change-me")
	oauthProxyInsecureConfig bool

	apiProxyTarget     *url.URL
	oauthProxyTarget   *url.URL
	oauthProxyInternal *url.URL
	apiProxyEnabled    bool

	httpClient = &http.Client{Timeout: 10 * time.Second}
)

const sessionCookieName = "triage_session"

func init() {
	backendAPIBase = rawAPIBase
	if backendAPIBase == "" {
		backendAPIBase = "http://localhost:8000"
	}
	if rawOAuthProxyInsecure != "" {
		if v, err := strconv.ParseBool(rawOAuthProxyInsecure); err == nil {
			oauthProxyInsecureConfig = v
		} else {
			fmt.Printf("Invalid TRIAGE_OAUTH2_PROXY_INSECURE_SKIP_VERIFY %q: %v\n", rawOAuthProxyInsecure, err)
		}
	}
	mime.AddExtensionType(".svg", "image/svg+xml")
	mime.AddExtensionType(".webp", "image/webp")
	rbac.ResolveIdentity = identityFromRequest
}

// ---------- small helpers ----------

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

type sessionClaims struct {
	Username string   `json:"u"`
	Groups   []string `json:"g"`
	Expires  int64    `json:"exp"`
}

func (c sessionClaims) valid(now time.Time) bool {
	if strings.TrimSpace(c.Username) == "" {
		return false
	}
	if c.Expires > 0 && now.Unix() > c.Expires {
		return false
	}
	return true
}

func decodeSessionToken(raw string) (*sessionClaims, error) {
	if len(sessionSecret) == 0 {
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
	mac := hmac.New(sha256.New, sessionSecret)
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

func identityFromRequest(r *http.Request) rbac.Identity {
	id := rbac.IdentityFromRequest(r)
	if id.Username != "" || len(id.Groups()) > 0 {
		return id
	}
	if len(sessionSecret) == 0 {
		return id
	}
	cookie, err := r.Cookie(sessionCookieName)
	if err != nil || cookie.Value == "" {
		return id
	}
	claims, err := decodeSessionToken(cookie.Value)
	if err != nil {
		return id
	}
	if !claims.valid(time.Now()) {
		return id
	}
	return rbac.NewIdentity(claims.Username, claims.Groups)
}

func writeJSON(w http.ResponseWriter, status int, payload interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		fmt.Printf("json encode error: %v\n", err)
	}
}

func isProtectedPath(p string) bool {
	return classifyPath(p) != accessNone
}

func serveFile(w http.ResponseWriter, r *http.Request, rel string) {
	full := filepath.Join(publicDir, filepath.Clean(rel))
	http.ServeFile(w, r, full)
}

// ---------- handlers ----------

func staticHandler(w http.ResponseWriter, r *http.Request) {
	p := r.URL.Path
	// root
	if p == "/" || p == "" {
		serveFile(w, r, "index.html")
		return
	}
	// nice path for login
	if p == "/login" {
		serveFile(w, r, "login.html")
		return
	}
	// prevent direct access to protected pages unless authed
	if isProtectedPath(p) {
		if _, ok := requireAuth(w, r); !ok {
			return
		}
	}
	// serve from /app/public
	if strings.HasPrefix(p, "/") {
		p = p[1:]
	}
	serveFile(w, r, p)
}

func meHandler(w http.ResponseWriter, r *http.Request) {
	profile := profileFromRequest(r)
	if profile == nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"username":            profile.Username,
		"role":                profile.Role,
		"allow_portal":        profile.AllowPortal,
		"allow_submit":        profile.AllowSubmit,
		"allow_admin":         profile.AllowAdmin,
		"default_destination": defaultDestination(profile),
	})
}

func adminFallbackPath(path string) string {
	if strings.HasPrefix(path, "/admin/api/") {
		suffix := strings.TrimPrefix(path, "/admin/api/")
		if suffix == "" {
			return path
		}
		return "/admin/" + suffix
	}
	if strings.HasPrefix(path, "/admin/") {
		suffix := strings.TrimPrefix(path, "/admin/")
		if suffix == "" {
			return path
		}
		return "/admin/api/" + suffix
	}
	return path
}

func callAdminEndpoint(ctx context.Context, method, path string, payload interface{}, out interface{}) (int, error) {
	status, err := callBackend(ctx, method, path, payload, out)
	if err == nil || status != http.StatusNotFound {
		return status, err
	}
	fallback := adminFallbackPath(path)
	if fallback == path {
		return status, err
	}
	return callBackend(ctx, method, fallback, payload, out)
}

func handleAdminUsers(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		ctxGet, cancelGet := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelGet()
		var resp userListResponse
		if _, err := callAdminEndpoint(ctxGet, http.MethodGet, "/admin/users", nil, &resp); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		writeJSON(w, http.StatusOK, resp)
	case http.MethodPost:
		var payload userCreateRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, "invalid json", http.StatusBadRequest)
			return
		}
		payload.Username = strings.TrimSpace(payload.Username)
		if !isSafeUsername(payload.Username) {
			http.Error(w, "invalid username", http.StatusBadRequest)
			return
		}
		payload.Role = normalizeRole(payload.Role)
		if !isValidRole(payload.Role) {
			http.Error(w, "invalid role", http.StatusBadRequest)
			return
		}
		payload.Password = strings.TrimSpace(payload.Password)
		if len(payload.Password) < 8 {
			http.Error(w, "password must be at least 8 characters", http.StatusBadRequest)
			return
		}
		ctxPost, cancelPost := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelPost()
		var resp userEnvelope
		if _, err := callAdminEndpoint(ctxPost, http.MethodPost, "/admin/users", payload, &resp); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		status := http.StatusCreated
		if resp.User == nil {
			status = http.StatusOK
		}
		writeJSON(w, status, resp)
	default:
		w.Header().Set("Allow", "GET, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func handleAdminUserDetail(w http.ResponseWriter, r *http.Request, suffix string) {
	if suffix == "" || strings.Contains(suffix, "/") {
		http.Error(w, "invalid user path", http.StatusBadRequest)
		return
	}
	username, err := url.PathUnescape(suffix)
	if err != nil {
		http.Error(w, "invalid username", http.StatusBadRequest)
		return
	}
	switch r.Method {
	case http.MethodPut:
		var payload userUpdateRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			http.Error(w, "invalid json", http.StatusBadRequest)
			return
		}
		payload.Role = normalizeRole(payload.Role)
		if !isValidRole(payload.Role) {
			http.Error(w, "invalid role", http.StatusBadRequest)
			return
		}
		if payload.Password != nil {
			trimmed := strings.TrimSpace(*payload.Password)
			if trimmed != "" && len(trimmed) < 8 {
				http.Error(w, "password must be at least 8 characters", http.StatusBadRequest)
				return
			}
			payload.Password = &trimmed
		}
		ctxPut, cancelPut := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelPut()
		var resp userEnvelope
		if _, err := callAdminEndpoint(ctxPut, http.MethodPut, "/admin/users/"+url.PathEscape(username), payload, &resp); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		writeJSON(w, http.StatusOK, resp)
	case http.MethodDelete:
		ctxDelete, cancelDelete := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelDelete()
		if _, err := callAdminEndpoint(ctxDelete, http.MethodDelete, "/admin/users/"+url.PathEscape(username), nil, nil); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	default:
		w.Header().Set("Allow", "PUT, DELETE")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func adminUsersAPIRouter(w http.ResponseWriter, r *http.Request) {
	user, ok := requireAuth(w, r)
	if !ok {
		return
	}
	if !isAdminUser(user) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	handleAdminUsers(w, r)
}

func adminUserDetailAPIRouter(w http.ResponseWriter, r *http.Request) {
	user, ok := requireAuth(w, r)
	if !ok {
		return
	}
	if !isAdminUser(user) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	suffix := strings.TrimPrefix(r.URL.Path, "/admin/api/users/")
	if suffix == r.URL.Path {
		suffix = strings.TrimPrefix(r.URL.Path, "/admin/users/")
	}
	handleAdminUserDetail(w, r, suffix)
}

func redirectTo(target string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target, http.StatusMovedPermanently)
	}
}

func configHandler(w http.ResponseWriter, r *http.Request) {
	publicAPIBase := rawAPIBase
	ingest := env("TRIAGE_INGEST_URL", "")
	jobs := env("TRIAGE_JOBS_URL", "")
	oauthStart := env("TRIAGE_OAUTH2_START", "/oauth2/start")
	useProxy := apiProxyEnabled
	if useProxy {
		publicAPIBase = ""
	}
	if ingest == "" {
		if useProxy || publicAPIBase == "" {
			ingest = "/ingest"
		} else {
			ingest = publicAPIBase + "/ingest"
		}
	}
	if jobs == "" {
		if useProxy || publicAPIBase == "" {
			jobs = "/jobs"
		} else {
			jobs = publicAPIBase + "/jobs"
		}
	}
	w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	fmt.Fprintf(w, "window.TRIAGE_API_BASE = %q;\nwindow.TRIAGE_INGEST_URL = %q;\nwindow.TRIAGE_JOBS_URL = %q;\nwindow.TRIAGE_OAUTH2_START = %q;\n", publicAPIBase, ingest, jobs, oauthStart)
}

func isSafeUsername(v string) bool {
	if v == "" {
		return false
	}
	if len(v) > 96 {
		return false
	}
	for _, r := range v {
		if r >= 'a' && r <= 'z' {
			continue
		}
		if r >= 'A' && r <= 'Z' {
			continue
		}
		if r >= '0' && r <= '9' {
			continue
		}
		switch r {
		case '.', '-', '_', '@':
			continue
		default:
			return false
		}
	}
	return true
}

func normalizeRole(role string) string {
	cleaned := strings.ToLower(strings.TrimSpace(role))
	switch cleaned {
	case "administrator", "admin":
		return "administrator"
	case "submit", "submitter", "create", "update", "editor":
		return "submit"
	default:
		return "view"
	}
}

func isValidRole(role string) bool {
	switch strings.ToLower(strings.TrimSpace(role)) {
	case "view", "submit", "admin", "administrator", "submitter", "create", "update", "editor":
		return true
	default:
		return false
	}
}

type userProfile struct {
	Username    string `json:"username"`
	Role        string `json:"role"`
	AllowPortal bool   `json:"allow_portal"`
	AllowSubmit bool   `json:"allow_submit"`
	AllowAdmin  bool   `json:"allow_admin"`
}

func profileFromRequest(r *http.Request) *userProfile {
	id := identityFromRequest(r)
	switch {
	case id.IsAdmin():
		return &userProfile{
			Username:    strings.TrimSpace(id.Username),
			Role:        "administrator",
			AllowPortal: true,
			AllowSubmit: true,
			AllowAdmin:  true,
		}
	case id.IsSubmitter():
		return &userProfile{
			Username:    strings.TrimSpace(id.Username),
			Role:        "submit",
			AllowPortal: true,
			AllowSubmit: true,
			AllowAdmin:  false,
		}
	case id.IsViewer():
		return &userProfile{
			Username:    strings.TrimSpace(id.Username),
			Role:        "view",
			AllowPortal: true,
			AllowSubmit: false,
			AllowAdmin:  false,
		}
	default:
		return nil
	}
}

type userEnvelope struct {
	User *userProfile `json:"user"`
	Err  string       `json:"error,omitempty"`
}

type userListResponse struct {
	Users []userProfile `json:"users"`
	Err   string        `json:"error,omitempty"`
}

type userCreateRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
	Role     string `json:"role"`
}

type userUpdateRequest struct {
	Password *string `json:"password,omitempty"`
	Role     string  `json:"role"`
}

type apiError struct {
	Message string `json:"detail"`
}

func callBackend(ctx context.Context, method, path string, payload interface{}, out interface{}) (int, error) {
	if backendAPIBase == "" {
		return 0, errors.New("TRIAGE_API_BASE not configured")
	}
	var body io.Reader
	if payload != nil {
		buf := &bytes.Buffer{}
		if err := json.NewEncoder(buf).Encode(payload); err != nil {
			return 0, fmt.Errorf("encode payload: %w", err)
		}
		body = buf
	}
	req, err := http.NewRequestWithContext(ctx, method, backendAPIBase+path, body)
	if err != nil {
		return 0, err
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json; charset=utf-8")
	}
	req.Header.Set("Accept", "application/json")
	if sharedSecret != "" {
		req.Header.Set("X-TRIAGE-SECRET", sharedSecret)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return resp.StatusCode, fmt.Errorf("read response: %w", err)
	}
	if resp.StatusCode >= 400 {
		var ae apiError
		if len(data) > 0 {
			if err := json.Unmarshal(data, &ae); err == nil && ae.Message != "" {
				return resp.StatusCode, errors.New(ae.Message)
			}
			trimmed := strings.TrimSpace(string(data))
			if trimmed != "" {
				return resp.StatusCode, errors.New(trimmed)
			}
		}
		return resp.StatusCode, fmt.Errorf("api %s %s returned %d", method, path, resp.StatusCode)
	}
	if out != nil && len(data) > 0 {
		if err := json.Unmarshal(data, out); err != nil {
			return resp.StatusCode, fmt.Errorf("decode response: %w", err)
		}
	}
	return resp.StatusCode, nil
}

type accessLevel int

const (
	accessNone accessLevel = iota
	accessPortal
	accessAdmin
)

func classifyPath(p string) accessLevel {
	p = strings.ToLower(p)
	if i := strings.Index(p, "?"); i >= 0 {
		p = p[:i]
	}
	if p == "" {
		return accessNone
	}
	p = strings.TrimSuffix(p, "/")
	switch p {
	case "/admin", "/admin.html":
		return accessAdmin
	}
	if strings.HasPrefix(p+"/", "/admin/") {
		return accessAdmin
	}
	// Portal and workspace pages are now public (read-only).
	// Write operations are protected at the API level.
	// Only admin pages remain behind auth.
	return accessNone
}

func isAdminUser(user *userProfile) bool {
	if user == nil {
		return false
	}
	return user.AllowAdmin && (strings.EqualFold(user.Role, "administrator") || strings.EqualFold(user.Role, "admin"))
}

func userCanAccess(user *userProfile, path string) bool {
	switch classifyPath(path) {
	case accessAdmin:
		return isAdminUser(user)
	case accessPortal:
		return user != nil && (user.AllowPortal || isAdminUser(user))
	default:
		return true
	}
}

func defaultDestination(user *userProfile) string {
	switch {
	case isAdminUser(user):
		return "/admin.html"
	case user != nil && user.AllowPortal:
		return "/portal.html"
	default:
		return "/"
	}
}

func requireAuth(w http.ResponseWriter, r *http.Request) (*userProfile, bool) {
	profile := profileFromRequest(r)
	if profile == nil {
		http.Error(w, "forbidden", http.StatusForbidden)
		return nil, false
	}
	if !userCanAccess(profile, r.URL.Path) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return nil, false
	}
	return profile, true
}

func logoutHandler(w http.ResponseWriter, r *http.Request) {
	target := r.URL.Query().Get("next")
	if target == "" {
		target = "/"
	}
	if strings.HasPrefix(target, "//") || strings.Contains(target, "://") {
		target = "/"
	}
	http.Redirect(w, r, "/oauth2/sign_out?rd="+url.QueryEscape(target), http.StatusFound)
}

func healthz(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("ok"))
}

// ---------- servers ----------

func main() {
	addrHTTP := flag.String("http", ":8080", "HTTP listen addr")
	addrHTTPS := flag.String("https", ":8443", "HTTPS listen addr")
	flag.Parse()

	cert := os.Getenv("CERT_FILE")
	key := os.Getenv("KEY_FILE")
	if rawAPIBase != "" {
		if u, err := url.Parse(rawAPIBase); err == nil {
			apiProxyTarget = u
		} else {
			fmt.Printf("Invalid TRIAGE_API_BASE %q: %v\n", rawAPIBase, err)
		}
	}
	if rawOAuthProxyURL != "" {
		if u, err := url.Parse(rawOAuthProxyURL); err == nil {
			oauthProxyTarget = u
		} else {
			fmt.Printf("Invalid TRIAGE_OAUTH2_PROXY_URL %q: %v\n", rawOAuthProxyURL, err)
		}
	}
	if rawOAuthProxyInternalURL != "" {
		if u, err := url.Parse(rawOAuthProxyInternalURL); err == nil {
			oauthProxyInternal = u
		} else {
			fmt.Printf("Invalid TRIAGE_OAUTH2_PROXY_INTERNAL_URL %q: %v\n", rawOAuthProxyInternalURL, err)
		}
	} else {
		oauthProxyInternal = oauthProxyTarget
	}

	mux := http.NewServeMux()

	// static & auth endpoints
	mux.HandleFunc("/login", staticHandler)  // GET -> /app/public/login.html
	mux.HandleFunc("/logout", logoutHandler) // delegate to oauth2-proxy sign out
	mux.HandleFunc("/auth/me", meHandler)
	mux.HandleFunc("/healthz", healthz)
	mux.HandleFunc("/config.js", configHandler)
	mux.HandleFunc("/admin/api/users", adminUsersAPIRouter)
	mux.HandleFunc("/admin/api/users/", adminUserDetailAPIRouter)
	mux.HandleFunc("/admin/users", adminUsersAPIRouter)
	mux.HandleFunc("/admin/users/", adminUserDetailAPIRouter)

	// Legacy route redirects (HEDI → Triage rebrand, then SVG → PNG logo).
	mux.HandleFunc("/hedi-edit.html", redirectTo("/edits.html"))
	mux.HandleFunc("/hedi-mapping.html", redirectTo("/mapping.html"))
	mux.HandleFunc("/img/hedi-logo.svg", redirectTo("/img/triage-logo.png"))
	mux.HandleFunc("/img/triage-logo.svg", redirectTo("/img/triage-logo.png"))

	// everything else
	mux.HandleFunc("/", staticHandler)

	if apiProxyTarget != nil {
		proxy := httputil.NewSingleHostReverseProxy(apiProxyTarget)
		proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
			fmt.Printf("proxy error for %s: %v\n", r.URL.Path, err)
			http.Error(w, "upstream unavailable", http.StatusBadGateway)
		}
		handler := rbac.RequireSubmitterForWrite(apiProxyHandler(proxy))
		mux.Handle("/ingest", handler)
		mux.Handle("/jobs", handler)
		mux.Handle("/jobs/", handler)
		mux.Handle("/claimtrace", handler)
		mux.Handle("/claimtrace/", handler)
		protectedProxy := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			r.Header.Del("Cookie")
			if sharedSecret != "" {
				r.Header.Set("X-TRIAGE-SECRET", sharedSecret)
			}
			proxy.ServeHTTP(w, r)
		})
		mux.Handle("/ops/", protectedProxy)
		mux.Handle("/ops/summary", protectedProxy)
		mux.Handle("/ops/command-center", protectedProxy)
		mux.Handle("/partners", protectedProxy)
		mux.Handle("/partners/", protectedProxy)
		// WebSocket proxy for real-time updates
		mux.HandleFunc("/ws/", func(w http.ResponseWriter, r *http.Request) {
			wsTarget := *apiProxyTarget
			if wsTarget.Scheme == "http" {
				wsTarget.Scheme = "ws"
			} else {
				wsTarget.Scheme = "wss"
			}
			wsProxy := httputil.NewSingleHostReverseProxy(&wsTarget)
			wsProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
				fmt.Printf("websocket proxy error for %s: %v\n", r.URL.Path, err)
				http.Error(w, "websocket upstream unavailable", http.StatusBadGateway)
			}
			if sharedSecret != "" {
				r.Header.Set("X-TRIAGE-SECRET", sharedSecret)
			}
			wsProxy.ServeHTTP(w, r)
		})
		apiProxyEnabled = true
	}

	if oauthProxyInternal != nil {
		proxy := newOAuth2ReverseProxy(oauthProxyInternal)
		proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
			fmt.Printf("oauth proxy error for %s: %v\n", r.URL.Path, err)
			http.Error(w, "oauth upstream unavailable", http.StatusBadGateway)
		}
		handler := oauth2ProxyHandler(proxy)
		mux.Handle("/oauth2", handler)
		mux.Handle("/oauth2/", handler)
	}

	// security headers wrapper
	handler := securityHeaders(mux)

	// HTTP
	go func() {
		s := &http.Server{
			Addr:              *addrHTTP,
			Handler:           handler,
			ReadHeaderTimeout: 10 * time.Second,
		}
		fmt.Printf("Frontend listening on %s (public=%s)\n", *addrHTTP, publicDir)
		if err := s.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			fmt.Printf("http server error: %v\n", err)
		}
	}()

	// HTTPS
	if cert != "" && key != "" {
		ts := &http.Server{
			Addr:              *addrHTTPS,
			Handler:           handler,
			ReadHeaderTimeout: 10 * time.Second,
		}
		fmt.Printf("Frontend TLS listening on %s (cert=%s)\n", *addrHTTPS, cert)
		if err := ts.ListenAndServeTLS(cert, key); err != nil && err != http.ErrServerClosed {
			fmt.Printf("tls server error: %v\n", err)
		}
	}

	// Block forever (simple keep-alive)
	select {}
}

// security headers
func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("Content-Security-Policy", "frame-ancestors 'none'; object-src 'none'; base-uri 'self';")
		next.ServeHTTP(w, r)
	})
}

// small Basic Auth wrapper for /portal/* and /admin/* trees
func apiProxyHandler(proxy *httputil.ReverseProxy) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		profile := profileFromRequest(r)
		if profile == nil || !profile.AllowPortal {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		// prevent backend from seeing frontend session cookie
		r.Header.Del("Cookie")
		if sharedSecret != "" {
			r.Header.Set("X-TRIAGE-SECRET", sharedSecret)
		}
		proxy.ServeHTTP(w, r)
	})
}

func oauth2ProxyHandler(proxy *httputil.ReverseProxy) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Forwarded-Host") == "" {
			r.Header.Set("X-Forwarded-Host", r.Host)
		}
		if r.Header.Get("X-Forwarded-Proto") == "" {
			scheme := "http"
			if r.TLS != nil {
				scheme = "https"
			}
			r.Header.Set("X-Forwarded-Proto", scheme)
		}
		proxy.ServeHTTP(w, r)
	})
}

func newOAuth2ReverseProxy(target *url.URL) *httputil.ReverseProxy {
	basePath := strings.TrimSuffix(target.Path, "/")

	clean := *target
	clean.Path = ""
	clean.RawPath = ""

	proxy := httputil.NewSingleHostReverseProxy(&clean)
	if oauthProxyInsecureConfig {
		if base, ok := http.DefaultTransport.(*http.Transport); ok {
			transport := base.Clone()
			if transport.TLSClientConfig == nil {
				transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
			} else {
				clone := transport.TLSClientConfig.Clone()
				clone.InsecureSkipVerify = true
				transport.TLSClientConfig = clone
			}
			proxy.Transport = transport
		} else {
			proxy.Transport = &http.Transport{ // fallback
				Proxy:           http.ProxyFromEnvironment,
				TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
			}
		}
	}
	if basePath == "" {
		return proxy
	}

	originalDirector := proxy.Director
	proxy.Director = func(req *http.Request) {
		originalDirector(req)
		req.URL.Path = rewriteOAuth2Path(req.URL.Path, basePath)
		req.URL.RawPath = req.URL.Path
	}
	return proxy
}

func rewriteOAuth2Path(currentPath, basePath string) string {
	if basePath == "" {
		return currentPath
	}

	suffix := strings.TrimPrefix(currentPath, "/oauth2")
	if suffix == "" {
		return basePath
	}
	if !strings.HasPrefix(suffix, "/") {
		suffix = "/" + suffix
	}
	return basePath + suffix
}
