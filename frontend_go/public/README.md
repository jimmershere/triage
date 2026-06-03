# Triage Claims Portal Static Assets

This directory contains the static HTML, CSS, JavaScript, images, and optional Apache sample files served by the Go frontend in `frontend_go/`.

## Primary serving path

The supported local/deployment entry point is the Go frontend service, not a bare static file server. The Go service serves this directory, provides `/config.js`, proxies API requests, injects server-side shared-secret headers for protected backend calls, validates sessions, and applies security headers.

Use one of these from the repository root:

```bash
docker compose up --build frontend_go
```

or:

```bash
bash scripts/run-local-stack.sh
```

Then open http://localhost:8080.

## What is inside

- Command Center dashboard: `index.html`
- Ingest and import review: `ingest.html`, `processed.html`
- Claims Portal transaction pages: `portal.html`, `portal/*.html`
- Claimtrace dashboard: `claimtrace.html`
- Workspace tools: `claim-entry.html`, `edits.html`, `mapping.html`
- Operations/admin/reference pages: `partners.html`, `admin.html`, `about.html`, `edi-news.html`
- Shared scripts and styles: `static/js/`, `static/css/`
- Image assets: `img/`
- Optional Apache/RHEL samples: `deploy/httpd/`

## Daily UI enhancements

`static/js/site-enhancements.js` injects a daily operations guide into each page, including two EDI/X12/medical-claim graphics and seven page-specific tips. It also applies rotating panel tint classes and focus-state styling.

## Support configuration

`static/js/support_config.js` contains deployment placeholders for support email and phone values. Replace them with environment-specific contact points before production use.

## Apache samples

The files under `deploy/httpd/` are optional examples for organizations that require Apache httpd as an entry point. They are not the default application server. The default frontend runtime is the Go binary in `frontend_go/`.

Example Apache document root used by the sample files:

```bash
/var/www/triage-portal/public
```

Example virtual host names use `triage-portal.local`.

## Security notes

- Do not expose `TRIAGE_SHARED_SECRET` to browser JavaScript.
- Use the Go frontend proxy for protected backend calls.
- Rotate OAuth/OIDC client secrets, session secrets, and any sample credentials before shared deployment.
- Keep generated assets, scratch files, and backup originals out of version control.
