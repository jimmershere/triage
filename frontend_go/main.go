package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"flag"
	"fmt"
	//"html/template"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/crypto/bcrypt"
)

var (
	publicDir = env("PUBLIC_DIR", "/app/public") // bind-mounted in the container
	// session settings
	cookieName    = "hedi_session"
	sessionTTL    = 24 * time.Hour
	sessionSecret = []byte(env("HEDI_SESSION_SECRET", "dev-secret-change-me"))
	secureCookies = envBool("HEDI_SECURE_COOKIES", false)

	apiProxyTarget  *url.URL
	apiProxyEnabled bool
)

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

func isProtectedPath(p string) bool {
	p = strings.ToLower(p)
	if i := strings.Index(p, "?"); i >= 0 {
		p = p[:i]
	}
	p = strings.TrimSuffix(p, "/")
	if p == "" {
		return false
	}
	protected := map[string]bool{
		"/portal":           true,
		"/portal.html":      true,
		"/edi-mapping":      true,
		"/edi-mapping.html": true,
		"/claim-entry":      true,
		"/claim-entry.html": true,
		"/processed":        true,
		"/processed.html":   true,
		"/admin":            true,
		"/admin.html":       true,
	}
	if protected[p] {
		return true
	}
	prefixes := []string{"/portal/", "/processed/", "/admin/"}
	for _, pref := range prefixes {
		if strings.HasPrefix(p+"/", pref) { // ensure trailing slash for exact matches too
			return true
		}
	}
	return false
}

func serveFile(w http.ResponseWriter, r *http.Request, rel string) {
	full := filepath.Join(publicDir, filepath.Clean(rel))
	http.ServeFile(w, r, full)
}

// ---------- htpasswd (bcrypt or literal for dev) ----------

func verifyHtpasswd(user, pass string) bool {
	// Choose which file based on "user type" if you need. For now we accept either file.
	paths := []string{
		env("PORTAL_HTPASSWD", "/app/auth/portal.htpasswd"),
		env("ADMIN_HTPASSWD", "/app/auth/admin.htpasswd"),
	}
	for _, p := range paths {
		if checkHtpasswd(p, user, pass) {
			return true
		}
	}
	return false
}

func checkHtpasswd(path, user, pass string) bool {
	b, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	lines := strings.Split(strings.ReplaceAll(string(b), "\r\n", "\n"), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		if len(parts) != 2 {
			continue
		}
		u := parts[0]
		h := parts[1]
		if u != user {
			continue
		}
		// try bcrypt first
		if bcrypt.CompareHashAndPassword([]byte(h), []byte(pass)) == nil {
			return true
		}
		// allow literal match for dev-only cases
		if subtle.ConstantTimeCompare([]byte(h), []byte(pass)) == 1 {
			return true
		}
		return false
	}
	return false
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

func isValidSession(r *http.Request) bool {
	c, err := r.Cookie(cookieName)
	if err != nil || c.Value == "" {
		return false
	}
	parts := strings.SplitN(c.Value, ".", 2)
	if len(parts) != 2 {
		return false
	}
	rawPayload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return false
	}
	payload := string(rawPayload)
	want := makeSignature(payload)
	if subtle.ConstantTimeCompare([]byte(parts[1]), []byte(want)) != 1 {
		return false
	}
	// payload = user|timestamp
	ps := strings.SplitN(payload, "|", 2)
	if len(ps) != 2 {
		return false
	}
	t, err := time.Parse(time.RFC3339, ps[1])
	if err != nil {
		return false
	}
	if time.Since(t) > sessionTTL {
		return false
	}
	return true
}

func requireAuth(w http.ResponseWriter, r *http.Request) bool {
	if !isValidSession(r) {
		http.Redirect(w, r, "/login?next="+url.QueryEscape(r.URL.Path), http.StatusFound)
		return false
	}
	return true
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
		if !requireAuth(w, r) {
			return
		}
	}
	// serve from /app/public
	if strings.HasPrefix(p, "/") {
		p = p[1:]
	}
	serveFile(w, r, p)
}

func configHandler(w http.ResponseWriter, r *http.Request) {
	apiBase := strings.TrimRight(env("HEDI_API_BASE", ""), "/")
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

func loginPostHandler(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad form", http.StatusBadRequest)
		return
	}
	user := strings.TrimSpace(r.FormValue("username"))
	pass := r.FormValue("password")
	next := sanitizeNext(r.FormValue("next"))
	if !verifyHtpasswd(user, pass) {
		time.Sleep(300 * time.Millisecond)
		http.Redirect(w, r, "/login?err=1&next="+url.QueryEscape(next), http.StatusFound)
		return
	}
	setSessionCookie(w, user)
	http.Redirect(w, r, next, http.StatusFound)
}

func logoutHandler(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, &http.Cookie{
		Name:     cookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		Secure:   secureCookies,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   -1,
	})
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
	if raw := strings.TrimSpace(os.Getenv("HEDI_API_BASE")); raw != "" {
		if u, err := url.Parse(raw); err == nil {
			apiProxyTarget = u
		} else {
			fmt.Printf("Invalid HEDI_API_BASE %q: %v\n", raw, err)
		}
	}

	mux := http.NewServeMux()

	// static & auth endpoints
	mux.HandleFunc("/login", staticHandler)         // GET -> /app/public/login.html
	mux.HandleFunc("/logout", logoutHandler)        // clear cookie
	mux.HandleFunc("/auth/login", loginPostHandler) // POST from login form
	mux.HandleFunc("/healthz", healthz)
	mux.HandleFunc("/config.js", configHandler)

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

	// Optional: scoped Basic Auth trees (keep if you use /portal/* or /admin/* folders)
	portalFS := http.StripPrefix("/portal/", http.FileServer(http.Dir(filepath.Join(publicDir, "portal"))))
	mux.Handle("/portal/", basicAuth(env("PORTAL_HTPASSWD", "/app/auth/portal.htpasswd"), "HEDI Claims", portalFS))

	adminFS := http.StripPrefix("/admin/", http.FileServer(http.Dir(filepath.Join(publicDir, "admin"))))
	mux.Handle("/admin/", basicAuth(env("ADMIN_HTPASSWD", "/app/auth/admin.htpasswd"), "HEDI Admin", adminFS))

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
func basicAuth(htpasswdPath, realm string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		user, pass, ok := r.BasicAuth()
		if !ok || !checkHtpasswd(htpasswdPath, user, pass) {
			w.Header().Set("WWW-Authenticate", fmt.Sprintf(`Basic realm="%s"`, realm))
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func sanitizeNext(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "/portal.html"
	}
	if strings.Contains(raw, "://") {
		return "/portal.html"
	}
	if !strings.HasPrefix(raw, "/") {
		return "/portal.html"
	}
	if strings.HasPrefix(raw, "//") {
		return "/portal.html"
	}
	return raw
}

func apiProxyHandler(proxy *httputil.ReverseProxy) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !isValidSession(r) {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		// prevent backend from seeing frontend session cookie
		r.Header.Del("Cookie")
		proxy.ServeHTTP(w, r)
	})
}
