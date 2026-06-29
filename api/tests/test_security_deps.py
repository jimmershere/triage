"""Tests for server-side role enforcement and rate limiting."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request

from api.security_deps import (
    _reset_rate_limits,
    rate_limit,
    require_role,
)


class _FakeClient:
    host = "1.2.3.4"


def _req():
    scope = {"type": "http", "headers": [], "client": ("1.2.3.4", 1234)}
    return Request(scope)


def test_roles_not_enforced_by_default(monkeypatch):
    monkeypatch.delenv("TRIAGE_ENFORCE_ROLES", raising=False)
    monkeypatch.delenv("TRIAGE_ENV", raising=False)
    dep = require_role("administrator")
    # No header, not enforced -> allowed (proxy handles it).
    assert dep(None) is None


def test_role_enforced_denies_missing(monkeypatch):
    monkeypatch.setenv("TRIAGE_ENFORCE_ROLES", "true")
    dep = require_role("administrator")
    with pytest.raises(HTTPException) as exc:
        dep(None)
    assert exc.value.status_code == 403


def test_role_hierarchy(monkeypatch):
    monkeypatch.setenv("TRIAGE_ENFORCE_ROLES", "true")
    admin_dep = require_role("administrator")
    submit_dep = require_role("submit")
    # administrator satisfies submit and administrator
    assert submit_dep("admin") is None
    assert admin_dep("administrator") is None
    # submit does NOT satisfy administrator
    with pytest.raises(HTTPException):
        admin_dep("submit")
    # view does not satisfy submit
    with pytest.raises(HTTPException):
        submit_dep("view")


def test_rate_limit_blocks_after_limit(monkeypatch):
    monkeypatch.delenv("TRIAGE_RATE_LIMIT_DISABLED", raising=False)
    monkeypatch.delenv("TRIAGE_RATE_LIMIT_LOGIN", raising=False)
    _reset_rate_limits()
    dep = rate_limit("login", limit=3, window_seconds=60)
    req = _req()
    for _ in range(3):
        assert dep(req) is None
    with pytest.raises(HTTPException) as exc:
        dep(req)
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_rate_limit_disabled(monkeypatch):
    monkeypatch.setenv("TRIAGE_RATE_LIMIT_DISABLED", "true")
    _reset_rate_limits()
    dep = rate_limit("login", limit=1, window_seconds=60)
    req = _req()
    for _ in range(5):
        assert dep(req) is None  # never blocks when disabled
