package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type apiUser struct {
	Username    string `json:"username"`
	Role        string `json:"role"`
	AllowPortal bool   `json:"allow_portal"`
	AllowSubmit bool   `json:"allow_submit"`
	AllowAdmin  bool   `json:"allow_admin"`
}

type loginResponse struct {
	OK    bool     `json:"ok"`
	User  *apiUser `json:"user"`
	Error string   `json:"error"`
}

var (
	listenAddr    = env("RBAC_LISTEN_ADDR", ":4180")
	apiBase       = strings.TrimRight(env("RBAC_API_BASE", "http://api:8000"), "/")
	sharedSecret  = strings.TrimSpace(env("RBAC_SHARED_SECRET", ""))
	sessionSecret = []byte(env("TRIAGE_SESSION_SECRET", ""))
	cookieName    = env("RBAC_COOKIE_NAME", "triage_session")
	cookieDomain  = strings.TrimSpace(os.Getenv("RBAC_COOKIE_DOMAIN"))
	secureCookie  = envBool("RBAC_COOKIE_SECURE", false)
	sessionTTL    = envDuration("RBAC_SESSION_TTL", 8*time.Hour)

	httpClient = &http.Client{Timeout: 10 * time.Second}

	loginTemplate = template.Must(template.New("login").Parse(loginTemplateHTML))
)

func env(key, def string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return def
}

func envBool(key string, def bool) bool {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return def
	}
	switch strings.ToLower(raw) {
	case "1", "true", "yes", "y", "on":
		return true
	case "0", "false", "no", "n", "off":
		return false
	default:
		return def
	}
}

func envDuration(key string, def time.Duration) time.Duration {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return def
	}
	dur, err := time.ParseDuration(raw)
	if err != nil {
		log.Printf("invalid duration for %s: %v", key, err)
		return def
	}
	if dur <= 0 {
		return def
	}
	return dur
}

func sanitizeRedirect(target string) string {
	cleaned := strings.TrimSpace(target)
	if cleaned == "" {
		return "/"
	}
	if strings.HasPrefix(cleaned, "//") {
		return "/"
	}
	if strings.Contains(cleaned, "://") {
		return "/"
	}
	if !strings.HasPrefix(cleaned, "/") {
		return "/"
	}
	return cleaned
}

func groupsForUser(user *apiUser) []string {
	if user == nil {
		return nil
	}
	groups := []string{"triage-view"}
	if user.AllowSubmit {
		groups = append(groups, "triage-submit")
	}
	if user.AllowAdmin {
		groups = append(groups, "triage-admin")
	}
	return groups
}

func authenticate(ctx context.Context, username, password string) (*apiUser, error) {
	if apiBase == "" {
		return nil, errors.New("RBAC_API_BASE not configured")
	}
	payload := map[string]string{
		"username": username,
		"password": password,
	}
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, apiBase+"/auth/login", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	if sharedSecret != "" {
		req.Header.Set("X-TRIAGE-SECRET", sharedSecret)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var lr loginResponse
	if err := json.NewDecoder(resp.Body).Decode(&lr); err != nil {
		return nil, err
	}
	if resp.StatusCode >= 400 || !lr.OK || lr.User == nil {
		msg := lr.Error
		if msg == "" {
			msg = fmt.Sprintf("login failed (%d)", resp.StatusCode)
		}
		return nil, errors.New(msg)
	}
	return lr.User, nil
}

func setSessionCookie(w http.ResponseWriter, token string) {
	cookie := &http.Cookie{
		Name:     cookieName,
		Value:    token,
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		Secure:   secureCookie,
		MaxAge:   int(sessionTTL.Seconds()),
	}
	if cookieDomain != "" {
		cookie.Domain = cookieDomain
	}
	http.SetCookie(w, cookie)
}

func clearSessionCookie(w http.ResponseWriter) {
	cookie := &http.Cookie{
		Name:     cookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		SameSite: http.SameSiteLaxMode,
		Secure:   secureCookie,
		MaxAge:   -1,
		Expires:  time.Unix(0, 0),
	}
	if cookieDomain != "" {
		cookie.Domain = cookieDomain
	}
	http.SetCookie(w, cookie)
}

func sessionFromRequest(r *http.Request) *sessionClaims {
	if len(sessionSecret) == 0 {
		return nil
	}
	cookie, err := r.Cookie(cookieName)
	if err != nil {
		return nil
	}
	claims, err := parseSession(cookie.Value, sessionSecret)
	if err != nil {
		return nil
	}
	if !claims.valid(time.Now()) {
		return nil
	}
	return claims
}

func loginPage(w http.ResponseWriter, data map[string]interface{}) {
	if data == nil {
		data = map[string]interface{}{}
	}
	if data["Redirect"] == nil {
		data["Redirect"] = "/"
	}
	if err := loginTemplate.Execute(w, data); err != nil {
		log.Printf("render login page: %v", err)
		http.Error(w, "unable to render login", http.StatusInternalServerError)
	}
}

func handleStart(w http.ResponseWriter, r *http.Request) {
	redirectTarget := sanitizeRedirect(r.URL.Query().Get("rd"))
	switch r.Method {
	case http.MethodGet:
		if session := sessionFromRequest(r); session != nil {
			http.Redirect(w, r, redirectTarget, http.StatusFound)
			return
		}
		loginPage(w, map[string]interface{}{"Redirect": redirectTarget})
	case http.MethodPost:
		if err := r.ParseForm(); err != nil {
			loginPage(w, map[string]interface{}{
				"Error":    "Invalid form submission",
				"Redirect": redirectTarget,
			})
			return
		}
		username := strings.TrimSpace(r.FormValue("username"))
		password := r.FormValue("password")
		if username == "" || password == "" {
			loginPage(w, map[string]interface{}{
				"Error":    "Username and password are required",
				"Redirect": redirectTarget,
				"Username": username,
			})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		user, err := authenticate(ctx, username, password)
		if err != nil {
			loginPage(w, map[string]interface{}{
				"Error":    err.Error(),
				"Redirect": redirectTarget,
				"Username": username,
			})
			return
		}
		claims := sessionClaims{
			Username: user.Username,
			Groups:   groupsForUser(user),
			Expires:  time.Now().Add(sessionTTL).Unix(),
		}
		token, err := signSession(claims, sessionSecret)
		if err != nil {
			log.Printf("sign session: %v", err)
			http.Error(w, "unable to create session", http.StatusInternalServerError)
			return
		}
		setSessionCookie(w, token)
		http.Redirect(w, r, redirectTarget, http.StatusFound)
	default:
		w.Header().Set("Allow", "GET, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func handleSignOut(w http.ResponseWriter, r *http.Request) {
	clearSessionCookie(w)
	target := sanitizeRedirect(r.URL.Query().Get("rd"))
	http.Redirect(w, r, target, http.StatusFound)
}

func handleSession(w http.ResponseWriter, r *http.Request) {
	claims := sessionFromRequest(r)
	if claims == nil {
		http.Error(w, "no active session", http.StatusUnauthorized)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{
		"username": claims.Username,
		"groups":   claims.Groups,
		"expires":  claims.Expires,
	})
}

func healthz(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	w.Write([]byte("ok"))
}

func handleCallback(w http.ResponseWriter, r *http.Request) {
	// Treat callbacks the same as login redirects.
	target := sanitizeRedirect(r.URL.Query().Get("rd"))
	if session := sessionFromRequest(r); session != nil {
		http.Redirect(w, r, target, http.StatusFound)
		return
	}
	http.Redirect(w, r, "/oauth2/start?rd="+url.QueryEscape(target), http.StatusFound)
}

const loginTemplateHTML = `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>TurboHEDI Sign In</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 0; padding: 0; background: #f2f4f8; }
      .container { max-width: 420px; margin: 64px auto; background: white; padding: 32px; border-radius: 12px; box-shadow: 0 12px 32px rgba(0,0,0,0.12); }
      h1 { margin-top: 0; font-size: 1.5rem; color: #1d3557; }
      form { display: flex; flex-direction: column; gap: 16px; }
      label { font-weight: 600; color: #1d3557; }
      input[type="text"], input[type="password"] { width: 100%; padding: 10px 12px; border: 1px solid #b0bec5; border-radius: 8px; font-size: 1rem; }
      input[type="text"]:focus, input[type="password"]:focus { outline: none; border-color: #457b9d; box-shadow: 0 0 0 3px rgba(69,123,157,0.2); }
      button { padding: 12px 16px; border: none; background: linear-gradient(135deg, #457b9d, #1d3557); color: white; font-size: 1rem; border-radius: 8px; cursor: pointer; font-weight: 600; }
      button:hover { background: linear-gradient(135deg, #1d3557, #0b1e36); }
      .error { color: #c1121f; background: rgba(193,18,31,0.1); padding: 12px; border-radius: 8px; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>Sign in to TurboHEDI</h1>
      {{if .Error}}
        <div class="error">{{.Error}}</div>
      {{end}}
      <form method="post" action="/oauth2/start">
        <input type="hidden" name="rd" value="{{.Redirect}}" />
        <label for="username">Username</label>
        <input id="username" name="username" type="text" value="{{.Username}}" autocomplete="username" required />
        <label for="password">Password</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required />
        <button type="submit">Continue</button>
      </form>
    </div>
  </body>
</html>`

func main() {
	if len(sessionSecret) == 0 {
		log.Fatal("TRIAGE_SESSION_SECRET must be configured")
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/oauth2/start", handleStart)
	mux.HandleFunc("/oauth2/callback", handleCallback)
	mux.HandleFunc("/oauth2/sign_out", handleSignOut)
	mux.HandleFunc("/oauth2/session", handleSession)
	mux.HandleFunc("/healthz", healthz)

	server := &http.Server{
		Addr:              listenAddr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Printf("RBAC service listening on %s (api=%s)", listenAddr, apiBase)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("server error: %v", err)
	}
}
