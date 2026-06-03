module triage/frontend_go

go 1.22

// Toolchain pinned to a stdlib patch level that fixes the CVEs flagged by
// govulncheck against go1.23 (crypto/x509, net/http, net/url, etc.).
// Bump this together with rbac_proxy/go.mod when newer fixes ship.
toolchain go1.25.11
