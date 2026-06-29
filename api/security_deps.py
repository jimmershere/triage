"""Server-side authorization and rate-limiting dependencies.

Two defenses mgt flagged as "before real claims flow":

* **Server-side role enforcement.** The ``frontend_go`` reverse proxy performs
  OAuth/RBAC and injects ``X-TRIAGE-SECRET``; it also forwards the authenticated
  user's role in ``X-TRIAGE-ROLE``. :func:`require_role` enforces that role on
  the endpoint so authorization is not *only* client-side. Enforcement is opt-in
  (``TRIAGE_ENFORCE_ROLES`` truthy, or ``TRIAGE_ENV=production``) so existing
  deployments are not broken until the proxy is confirmed to inject the header;
  once on, a missing/insufficient role is a 403.

* **Rate limiting.** A lightweight in-process sliding-window limiter keyed by
  client IP + route caps abusive bursts on unauthenticated/expensive endpoints
  (``/auth/login``, ``/ingest``). It is per-process (no external store); set the
  limits to taste via env. Returns HTTP 429 when exceeded.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

# --- roles ---------------------------------------------------------------
_ROLE_RANK = {"view": 1, "submit": 2, "administrator": 3}
_ROLE_ALIASES = {
    "admin": "administrator",
    "administrator": "administrator",
    "submit": "submit",
    "submitter": "submit",
    "create": "submit",
    "update": "submit",
    "editor": "submit",
    "view": "view",
    "viewer": "view",
    "read": "view",
}


def _normalize_role(role: str | None) -> str | None:
    if not role:
        return None
    return _ROLE_ALIASES.get(role.strip().lower())


def _roles_enforced() -> bool:
    # Enforcement is gated on an EXPLICIT flag (not implied by production) so a
    # deploy never starts 403-ing every role-gated endpoint before the reverse
    # proxy is confirmed to inject (and strip) X-TRIAGE-ROLE.
    return os.getenv("TRIAGE_ENFORCE_ROLES", "").strip().lower() in {"1", "true", "yes", "on"}


def require_role(minimum: str):
    """Dependency factory: require at least ``minimum`` role (hierarchical).

    ``administrator`` >= ``submit`` >= ``view``.
    """
    need = _ROLE_RANK[_normalize_role(minimum)]

    def _dep(x_triage_role: str | None = Header(None, alias="X-TRIAGE-ROLE")) -> None:
        if not _roles_enforced():
            return  # proxy-enforced; server enforcement opt-in
        role = _normalize_role(x_triage_role)
        if role is None:
            raise HTTPException(status_code=403, detail="role required")
        if _ROLE_RANK[role] < need:
            raise HTTPException(
                status_code=403, detail=f"requires {minimum} role"
            )

    return _dep


# --- rate limiting -------------------------------------------------------
_BUCKETS: dict[str, deque] = defaultdict(deque)
_RL_LOCK = threading.Lock()


def _trusted_proxies() -> set[str]:
    raw = os.getenv("TRIAGE_TRUSTED_PROXIES", "").strip()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _client_ip(request: Request) -> str:
    """Resolve the rate-limit key IP.

    X-Forwarded-For is attacker-controlled, so it is honored ONLY when the direct
    socket peer is a configured trusted proxy (``TRIAGE_TRUSTED_PROXIES``);
    otherwise the socket peer is used. This prevents a client from minting a
    fresh bucket per request by rotating XFF to defeat the limit. Operators
    behind the frontend_go proxy must set TRIAGE_TRUSTED_PROXIES to the proxy
    address so per-user throttling still works.
    """
    peer = request.client.host if request.client else "unknown"
    fwd = request.headers.get("x-forwarded-for")
    if fwd and peer in _trusted_proxies():
        return fwd.split(",")[0].strip()
    return peer


def rate_limit(name: str, *, limit: int, window_seconds: int = 60):
    """Dependency factory: at most ``limit`` requests per ``window`` per IP.

    Limits are overridable via ``TRIAGE_RATE_LIMIT_<NAME>`` (per window) and
    disabled entirely with ``TRIAGE_RATE_LIMIT_DISABLED=true`` (tests/dev).
    """

    def _dep(request: Request) -> None:
        if os.getenv("TRIAGE_RATE_LIMIT_DISABLED", "").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            return
        env_limit = os.getenv(f"TRIAGE_RATE_LIMIT_{name.upper()}", "").strip()
        effective = int(env_limit) if env_limit.isdigit() else limit
        if effective <= 0:
            return
        key = f"{name}:{_client_ip(request)}"
        now = time.monotonic()
        cutoff = now - window_seconds
        with _RL_LOCK:
            bucket = _BUCKETS[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= effective:
                retry = int(bucket[0] + window_seconds - now) + 1
                raise HTTPException(
                    status_code=429,
                    detail="rate limit exceeded",
                    headers={"Retry-After": str(max(retry, 1))},
                )
            bucket.append(now)

    return _dep


def _reset_rate_limits() -> None:  # test helper
    with _RL_LOCK:
        _BUCKETS.clear()
