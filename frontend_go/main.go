package main

import (
	"bytes"
	"context"
	"embed"
	"html/template"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"os"
	"path"
	"strings"
	"time"
	"path/filepath"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	//"github.com/go-chi/httprate"
	//"github.com/joho/godotenv"
)

//go:embed public/*
var publicFS embed.FS

//go:embed templates/*
var templatesFS embed.FS

var (
	apiBase     string
	listenAddr  string
	enableBasic bool
	usersFile   string
	templates   *template.Template
)

func mustEnv(name, def string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return def
}

type userPerms struct {
	Password string
	Roles    []string
	Perms    []string
}

func loadUsers(file string) (map[string]userPerms, error) {
	if file == "" {
		return map[string]userPerms{}, nil
	}
	b, err := os.ReadFile(file)
	if err != nil {
		return nil, err
	}
	out := map[string]userPerms{}
	lines := strings.Split(string(b), "\n")
	for _, ln := range lines {
		ln = strings.TrimSpace(ln)
		if ln == "" || strings.HasPrefix(ln, "#") {
			continue
		}
		// username:password:roles:perms
		parts := strings.Split(ln, ":")
		if len(parts) < 4 {
			continue
		}
		u := parts[0]
		p := parts[1]
		var roles, perms []string
		if parts[2] != "" {
			roles = strings.Split(parts[2], ",")
		}
		if parts[3] != "" {
			perms = strings.Split(parts[3], ",")
		}
		out[u] = userPerms{Password: p, Roles: roles, Perms: perms}
	}
	return out, nil
}

func hasPerm(u userPerms, perm string) bool {
	for _, p := range u.Perms {
		if p == perm {
			return true
		}
	}
	return false
}

func basicAuth(users map[string]userPerms, requiredPerm string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !enableBasic {
			next.ServeHTTP(w, r)
			return
		}
		u, p, ok := r.BasicAuth()
		if !ok {
			w.Header().Set("WWW-Authenticate", `Basic realm="claims"`)
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}
		user, ok := users[u]
		if !ok || p != user.Password {
			http.Error(w, "Unauthorized", http.StatusUnauthorized)
			return
		}
		if requiredPerm != "" && !hasPerm(user, requiredPerm) {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		ctx := context.WithValue(r.Context(), "user", u)
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func withSecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-XSS-Protection", "1; mode=block")
		w.Header().Set("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
		w.Header().Set("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
		next.ServeHTTP(w, r)
	})
}

func render(tmpl string, w http.ResponseWriter, data any) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := templates.ExecuteTemplate(w, tmpl, data); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}

func handleHome(w http.ResponseWriter, r *http.Request) {
	http.ServeFileFS(w, r, publicFS, "public/index.html")
}

func handlePortal(w http.ResponseWriter, r *http.Request) {
	render("portal.html", w, nil)
}

func handleAdmin(w http.ResponseWriter, r *http.Request) {
	render("admin.html", w, nil)
}

func handleUpload(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(50 << 20); err != nil {
		http.Error(w, "bad form: "+err.Error(), http.StatusBadRequest)
		return
	}
	file, hdr, err := r.FormFile("file")
	if err != nil {
		http.Error(w, "missing file: "+err.Error(), http.StatusBadRequest)
		return
	}
	defer file.Close()

	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	fw, err := mw.CreateFormFile("file", hdr.Filename)
	if err != nil {
		http.Error(w, "proxy form err: "+err.Error(), http.StatusInternalServerError)
		return
	}
	if _, err := io.Copy(fw, file); err != nil {
		http.Error(w, "copy err: "+err.Error(), http.StatusInternalServerError)
		return
	}
	_ = mw.Close()

	req, err := http.NewRequest(http.MethodPost, strings.TrimRight(apiBase, "/")+"/ingest", &buf)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	client := &http.Client{Timeout: 60 * time.Second}

	resp, err := client.Do(req)
	if err != nil {
		http.Error(w, "backend error: "+err.Error(), http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	for k, vals := range resp.Header {
		for _, v := range vals {
			w.Header().Add(k, v)
		}
	}
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

func main() {
	r := chi.NewRouter()
	r.Use(middleware.RequestID)
	r.Use(middleware.RealIP)
	r.Use(middleware.Logger)
	r.Use(middleware.Recoverer)

	// 1) Pretty routes
	r.Get("/", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, filepath.Join("public", "index.html"))
	})
	r.Get("/portal", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, filepath.Join("public", "portal.html"))
	})
	r.Get("/admin", func(w http.ResponseWriter, r *http.Request) {
		http.ServeFile(w, r, filepath.Join("public", "admin.html"))
	})

	// 2) Literal *.html aliases (so /index.html and /portal.html work)
	r.Get("/{page}.html", func(w http.ResponseWriter, r *http.Request) {
		page := chi.URLParam(r, "page")
		// sanitize path to avoid traversal
		clean := path.Clean(page + ".html")
		http.ServeFile(w, r, filepath.Join("public", clean))
	})

	// 3) Static assets under /static/*
	publicFS := http.StripPrefix("/static/",
		http.FileServer(http.Dir("public")),
	)
	r.Handle("/static/*", publicFS)

	// 4) Helpful 404: try to map to a public file if present
	r.NotFound(func(w http.ResponseWriter, r *http.Request) {
		// try serving a file under public for direct links like /logo.png
		try := filepath.Join("public", path.Clean(r.URL.Path))
		if _, err := filepath.Abs(try); err == nil {
			http.ServeFile(w, r, try)
			return
		}
		http.NotFound(w, r)
	})

	log.Println("Go frontend listening on :8080")
	log.Fatal(http.ListenAndServe(":8080", r))
}