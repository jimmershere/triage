package main

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var (
	publicDir = env("PUBLIC_DIR", "/app/public") // bind-mounted in the container
	// session settings
	cookieName    = "hedi_session"
	sessionTTL    = 24 * time.Hour
	sessionSecret = []byte(env("HEDI_SESSION_SECRET", "dev-secret-change-me"))
	secureCookies = envBool("HEDI_SECURE_COOKIES", false)

	rawAPIBase     = strings.TrimRight(env("HEDI_API_BASE", ""), "/")
	backendAPIBase string
	sharedSecret   = env("HEDI_SHARED_SECRET", "change-me")

	apiProxyTarget  *url.URL
	apiProxyEnabled bool

	httpClient = &http.Client{Timeout: 10 * time.Second}
)

func init() {
	backendAPIBase = rawAPIBase
	if backendAPIBase == "" {
		backendAPIBase = "http://localhost:8000"
	}
}

func envBool(key string, def bool) bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	if v == "" {
		return def
	}
	switch v {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return def
	}
}

// ---------- small helpers ----------

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
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

// ---------- sessions (HMAC-signed cookie) ----------

func makeSignature(payload string) string {
	m := hmac.New(sha256.New, sessionSecret)
	m.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

func setSessionCookie(w http.ResponseWriter, user string) {
	ts := time.Now().UTC().Format(time.RFC3339)
	payload := user + "|" + ts
	sig := makeSignature(payload)
	val := base64.RawURLEncoding.EncodeToString([]byte(payload)) + "." + sig

	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    val,
		Path:     "/",
		HttpOnly: true,
		Secure:   secureCookies,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   int(sessionTTL.Seconds()),
	})
}

func clearSessionCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		Secure:   secureCookies,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   -1,
	})
}

func sessionUser(r *http.Request) (string, error) {
	c, err := r.Cookie(cookieName)
	if err != nil || c.Value == "" {
		return "", errors.New("missing session")
	}
	parts := strings.SplitN(c.Value, ".", 2)
	if len(parts) != 2 {
		return "", errors.New("malformed session")
	}
	rawPayload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return "", fmt.Errorf("invalid payload: %w", err)
	}
	payload := string(rawPayload)
	want := makeSignature(payload)
	if subtle.ConstantTimeCompare([]byte(parts[1]), []byte(want)) != 1 {
		return "", errors.New("signature mismatch")
	}
	ps := strings.SplitN(payload, "|", 2)
	if len(ps) != 2 {
		return "", errors.New("bad payload structure")
	}
	t, err := time.Parse(time.RFC3339, ps[1])
	if err != nil {
		return "", fmt.Errorf("invalid timestamp: %w", err)
	}
	if time.Since(t) > sessionTTL {
		return "", errors.New("session expired")
	}
	return ps[0], nil
}

func isValidSession(r *http.Request) bool {
	_, err := sessionUser(r)
	return err == nil
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
	username, err := sessionUser(r)
	if err != nil {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	profile, err := fetchUserProfile(r.Context(), username)
	if err != nil {
		http.Error(w, "failed to load profile", http.StatusInternalServerError)
		return
	}
	if profile == nil {
		clearSessionCookie(w)
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"username":            profile.Username,
		"role":                profile.Role,
		"allow_portal":        profile.AllowPortal,
		"allow_admin":         profile.AllowAdmin,
		"default_destination": defaultDestination(profile),
	})
}

func adminUsersHandler(w http.ResponseWriter, r *http.Request) {
	user, ok := requireAuth(w, r)
	if !ok {
		return
	}
	if !isAdminUser(user) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	switch r.Method {
	case http.MethodGet:
		ctxGet, cancelGet := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelGet()
		var resp userListResponse
		if _, err := callBackend(ctxGet, http.MethodGet, "/admin/users", nil, &resp); err != nil {
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
		payload.Role = strings.ToLower(strings.TrimSpace(payload.Role))
		if !isValidRole(payload.Role) {
			http.Error(w, "invalid role", http.StatusBadRequest)
			return
		}
		payload.Password = strings.TrimSpace(payload.Password)
		if len(payload.Password) < 8 {
			http.Error(w, "password must be at least 8 characters", http.StatusBadRequest)
			return
		}
		if !payload.AllowPortal && !payload.AllowAdmin {
			http.Error(w, "grant portal or admin access", http.StatusBadRequest)
			return
		}
		ctxPost, cancelPost := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelPost()
		var resp userEnvelope
		if _, err := callBackend(ctxPost, http.MethodPost, "/admin/users", payload, &resp); err != nil {
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

func adminUserDetailHandler(w http.ResponseWriter, r *http.Request) {
	user, ok := requireAuth(w, r)
	if !ok {
		return
	}
	if !isAdminUser(user) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	suffix := strings.TrimPrefix(r.URL.Path, "/admin/api/users/")
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
		payload.Role = strings.ToLower(strings.TrimSpace(payload.Role))
		if !isValidRole(payload.Role) {
			http.Error(w, "invalid role", http.StatusBadRequest)
			return
		}
		if !payload.AllowPortal && !payload.AllowAdmin {
			http.Error(w, "grant portal or admin access", http.StatusBadRequest)
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
		if _, err := callBackend(ctxPut, http.MethodPut, "/admin/users/"+url.PathEscape(username), payload, &resp); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		writeJSON(w, http.StatusOK, resp)
	case http.MethodDelete:
		ctxDelete, cancelDelete := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancelDelete()
		if _, err := callBackend(ctxDelete, http.MethodDelete, "/admin/users/"+url.PathEscape(username), nil, nil); err != nil {
			http.Error(w, err.Error(), http.StatusBadGateway)
			return
		}
		writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	default:
		w.Header().Set("Allow", "PUT, DELETE")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func configHandler(w http.ResponseWriter, r *http.Request) {
	apiBase := rawAPIBase
	ingest := env("HEDI_INGEST_URL", "")
	jobs := env("HEDI_JOBS_URL", "")
	useProxy := apiProxyEnabled
	if ingest == "" {
		if useProxy || apiBase == "" {
			ingest = "/ingest"
		} else {
			ingest = apiBase + "/ingest"
		}
	}
	if jobs == "" {
		if useProxy || apiBase == "" {
			jobs = "/jobs"
		} else {
			jobs = apiBase + "/jobs"
		}
	}
	w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	fmt.Fprintf(w, "window.HEDI_API_BASE = %q;\nwindow.HEDI_INGEST_URL = %q;\nwindow.HEDI_JOBS_URL = %q;\n", apiBase, ingest, jobs)
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

func isValidRole(role string) bool {
	switch strings.ToLower(strings.TrimSpace(role)) {
	case "view", "update", "create", "admin":
		return true
	default:
		return false
	}
}

type userProfile struct {
	Username    string `json:"username"`
	Role        string `json:"role"`
	AllowPortal bool   `json:"allow_portal"`
	AllowAdmin  bool   `json:"allow_admin"`
}

type loginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type loginResponse struct {
	OK   bool         `json:"ok"`
	User *userProfile `json:"user"`
	Err  string       `json:"error,omitempty"`
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
	Username    string `json:"username"`
	Password    string `json:"password"`
	Role        string `json:"role"`
	AllowPortal bool   `json:"allow_portal"`
	AllowAdmin  bool   `json:"allow_admin"`
}

type userUpdateRequest struct {
	Password    *string `json:"password,omitempty"`
	Role        string  `json:"role"`
	AllowPortal bool    `json:"allow_portal"`
	AllowAdmin  bool    `json:"allow_admin"`
}

type apiError struct {
	Message string `json:"detail"`
}

func callBackend(ctx context.Context, method, path string, payload interface{}, out interface{}) (int, error) {
	if backendAPIBase == "" {
		return 0, errors.New("HEDI_API_BASE not configured")
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
		req.Header.Set("X-HEDI-SECRET", sharedSecret)
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

func fetchUserProfile(ctx context.Context, username string) (*userProfile, error) {
	if username == "" {
		return nil, errors.New("empty username")
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var resp userEnvelope
	status, err := callBackend(ctx, http.MethodGet, "/auth/users/"+url.PathEscape(username), nil, &resp)
	if err != nil {
		if status == http.StatusNotFound {
			return nil, nil
		}
		return nil, err
	}
	if resp.User == nil {
		return nil, nil
	}
	return resp.User, nil
}

func authenticateUser(ctx context.Context, username, password string) (*userProfile, error) {
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	payload := loginRequest{Username: username, Password: password}
	var resp loginResponse
	status, err := callBackend(ctx, http.MethodPost, "/auth/login", payload, &resp)
	if err != nil {
		if status == http.StatusUnauthorized {
			return nil, nil
		}
		return nil, err
	}
	if !resp.OK || resp.User == nil {
		return nil, errors.New("authentication failed")
	}
	return resp.User, nil
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
	portalExact := map[string]bool{
		"/portal":           true,
		"/portal.html":      true,
		"/processed":        true,
		"/processed.html":   true,
		"/claim-entry":      true,
		"/claim-entry.html": true,
		"/edi-mapping":      true,
		"/edi-mapping.html": true,
	}
	if portalExact[p] {
		return accessPortal
	}
	portalPrefixes := []string{"/portal/", "/processed/", "/claim-entry/", "/edi-mapping/"}
	for _, pref := range portalPrefixes {
		if strings.HasPrefix(p+"/", pref) {
			return accessPortal
		}
	}
	return accessNone
}

func isAdminUser(user *userProfile) bool {
	if user == nil {
		return false
	}
	return user.AllowAdmin && strings.EqualFold(user.Role, "admin")
}

func userCanAccess(user *userProfile, path string) bool {
	switch classifyPath(path) {
	case accessAdmin:
		return isAdminUser(user)
	case accessPortal:
		return user != nil && user.AllowPortal
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
	username, err := sessionUser(r)
	if err != nil {
		clearSessionCookie(w)
		http.Redirect(w, r, "/login?next="+url.QueryEscape(r.URL.Path), http.StatusFound)
		return nil, false
	}
	profile, err := fetchUserProfile(r.Context(), username)
	if err != nil {
		http.Error(w, "failed to load user profile", http.StatusInternalServerError)
		return nil, false
	}
	if profile == nil {
		clearSessionCookie(w)
		http.Redirect(w, r, "/login?next="+url.QueryEscape(r.URL.Path), http.StatusFound)
		return nil, false
	}
	if !userCanAccess(profile, r.URL.Path) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return nil, false
	}
	return profile, true
}

func loginPostHandler(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad form", http.StatusBadRequest)
		return
	}
	user := strings.TrimSpace(r.FormValue("username"))
	pass := r.FormValue("password")
	nextRaw := r.FormValue("next")
	profile, err := authenticateUser(r.Context(), user, pass)
	if err != nil {
		http.Error(w, "authentication backend error", http.StatusBadGateway)
		return
	}
	if profile == nil {
		time.Sleep(300 * time.Millisecond)
		sanitized := sanitizeNext(nextRaw, nil)
		http.Redirect(w, r, "/login?err=1&next="+url.QueryEscape(sanitized), http.StatusFound)
		return
	}
	setSessionCookie(w, profile.Username)
	target := sanitizeNext(nextRaw, profile)
	if !userCanAccess(profile, target) {
		target = defaultDestination(profile)
	}
	http.Redirect(w, r, target, http.StatusFound)
}

func logoutHandler(w http.ResponseWriter, r *http.Request) {
	clearSessionCookie(w)
	http.Redirect(w, r, "/", http.StatusFound)
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
			fmt.Printf("Invalid HEDI_API_BASE %q: %v\n", rawAPIBase, err)
		}
	}

	mux := http.NewServeMux()

	// static & auth endpoints
	mux.HandleFunc("/login", staticHandler)         // GET -> /app/public/login.html
	mux.HandleFunc("/logout", logoutHandler)        // clear cookie
	mux.HandleFunc("/auth/login", loginPostHandler) // POST from login form
	mux.HandleFunc("/auth/me", meHandler)
	mux.HandleFunc("/healthz", healthz)
	mux.HandleFunc("/config.js", configHandler)
	mux.HandleFunc("/admin/api/users", adminUsersHandler)
	mux.HandleFunc("/admin/api/users/", adminUserDetailHandler)

	// everything else
	mux.HandleFunc("/", staticHandler)

	if apiProxyTarget != nil {
		proxy := httputil.NewSingleHostReverseProxy(apiProxyTarget)
		proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
			fmt.Printf("proxy error for %s: %v\n", r.URL.Path, err)
			http.Error(w, "upstream unavailable", http.StatusBadGateway)
		}
		mux.Handle("/ingest", apiProxyHandler(proxy))
		mux.Handle("/jobs", apiProxyHandler(proxy))
		mux.Handle("/jobs/", apiProxyHandler(proxy))
		apiProxyEnabled = true
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
func sanitizeNext(raw string, user *userProfile) string {
	raw = strings.TrimSpace(raw)
	def := defaultDestination(user)
	if raw == "" {
		return def
	}
	if strings.Contains(raw, "://") {
		return def
	}
	if !strings.HasPrefix(raw, "/") {
		return def
	}
	if strings.HasPrefix(raw, "//") {
		return def
	}
	return raw
}

func apiProxyHandler(proxy *httputil.ReverseProxy) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		username, err := sessionUser(r)
		if err != nil {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		profile, err := fetchUserProfile(r.Context(), username)
		if err != nil {
			http.Error(w, "profile lookup failed", http.StatusBadGateway)
			return
		}
		if profile == nil || !profile.AllowPortal {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		// prevent backend from seeing frontend session cookie
		r.Header.Del("Cookie")
		proxy.ServeHTTP(w, r)
	})
}
