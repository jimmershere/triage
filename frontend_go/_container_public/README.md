# Sassy Claims Portal (HTML bundle)

Cute, fast, and enterprise‑ready Medicare claim portal UI. Static HTML/CSS/JS with drop‑in Apache (RHEL 8) configs, TLS 1.2+, multi‑tenant virtual hosts, and open‑source AAA (Keycloak OIDC or LDAP). PostgreSQL backs page content via your API.

## What’s inside
- `public/` static site (index, portal, admin)
- `deploy/httpd/` hardened Apache + TLS + vhosts + OIDC/LDAP samples
- `db/schema.sql` Postgres schema for announcements/faq and optional role mapping
- `.env.example` for runtime settings (if you add a backend later)

## Quick start (local preview)
```bash
python3 -m http.server -d public 8080
# open http://localhost:8080
```

## RHEL 8 deployment (Apache httpd)
```bash
sudo yum install -y httpd mod_ssl policycoreutils-python-utils
sudo mkdir -p /var/www/sassy-claims
sudo cp -r public /var/www/sassy-claims/
sudo cp deploy/httpd/httpd.conf.sample /etc/httpd/conf/httpd.conf
sudo cp deploy/httpd/ssl.conf.sample /etc/httpd/conf.d/ssl.conf
sudo cp deploy/httpd/vhosts-claims.conf /etc/httpd/conf.d/vhosts-claims.conf
sudo chcon -R -t httpd_sys_content_t /var/www/sassy-claims
sudo systemctl enable --now httpd
sudo firewall-cmd --permanent --add-service=https && sudo firewall-cmd --reload
```

### Enable Keycloak (recommended)
```bash
sudo yum install -y mod_auth_openidc
sudo cp deploy/httpd/oidc.conf.sample /etc/httpd/conf.d/oidc.conf
# Edit provider URL, client id/secret, redirect URI
sudo systemctl restart httpd
```

### Or LDAP / FreeIPA
```bash
sudo yum install -y mod_ldap
sudo cp deploy/httpd/ldap.conf.sample /etc/httpd/conf.d/ldap.conf
# Edit LDAP URL/bind credentials and group names
sudo systemctl restart httpd
```

### Multi‑tenant virtual hosts
- Add DNS records (e.g., `portal1.sassy-claims.local`, `portal2.sassy-claims.local`)
- Put certs/keys in `/etc/pki/tls/{certs,private}`
- VirtualHost examples live in `deploy/httpd/vhosts-claims.conf`

## PostgreSQL content
```bash
sudo -u postgres psql -f db/schema.sql
# Then expose a tiny API (FastAPI, Nginx Unit, or php-fpm) that reads `announcements/faqs`
# and returns JSON consumed by the front-end (replace demo JS calls).
```

## Scale & performance
- Enable HTTP/2, KeepAlive=On, short timeouts, and a reverse proxy (httpd or nginx) in front of app servers
- Serve static assets via CDN
- Run Keycloak in HA mode with a proper database (e.g., Postgres)
- Autoscale app/API layer; Apache can front multiple portals via name‑based vhosts
- Load test with k6 or wrk; expect thousands of concurrent users with proper sizing

## Security checklist
- Enforce TLS 1.2+ (done), disable legacy protocols (done)
- Strong CSP, referrer policy, security headers (included)
- Use OIDC (Keycloak) or LDAP for authN/Z; gate admin/portal paths by role
- Keep SELinux enforcing; set contexts for `/var/www/sassy-claims`
- Rotate credentials; store secrets in environment or system key store

---

Have fun making Medicare claims ✨sassy✨!
