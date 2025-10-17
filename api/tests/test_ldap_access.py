"""Tests for LDAP access fallbacks and special-case handling."""

from __future__ import annotations

import sys
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import ldap_utils


@pytest.fixture
def manager_factory(monkeypatch):
    """Return a factory that builds LDAPManager instances with stubbed deps."""

    class DummyServer:  # pragma: no cover - trivial stub
        def __init__(self, *args, **kwargs):
            pass

    class DummyConnection:  # pragma: no cover - trivial stub
        def __init__(self, *args, **kwargs):
            pass

        def unbind(self):
            pass

    monkeypatch.setattr(ldap_utils, "Server", DummyServer)
    monkeypatch.setattr(ldap_utils, "Connection", DummyConnection)

    def factory():
        config = ldap_utils.LDAPConfig(
            uri="ldap://example.com",
            base_dn="dc=example,dc=com",
            root_cn="cn=HEDI",
            users_ou="ou=users",
            roles_ou="ou=roles",
            trading_partners_ou="ou=trading",
            bind_dn="cn=admin,dc=example,dc=com",
            bind_password="secret",
            timeout=5,
            bootstrap_username="admin",
            bootstrap_password_hash="{SSHA}dummy",
            admin_dn="cn=admin,dc=example,dc=com",
            admin_username="admin",
        )
        return ldap_utils.LDAPManager(
            config,
            role_hierarchy=["view", "submit", "administrator"],
            role_aliases={},
            administrator_role="administrator",
        )

    return factory


def test_user_highest_role_defaults_to_submit(manager_factory):
    manager = manager_factory()

    class NoGroupsConn:
        def __init__(self):
            self.entries = []

        def search(self, *args, **kwargs):
            self.entries = []
            return False

    result = manager._user_highest_role(
        NoGroupsConn(), "uid=jim.doe,ou=users,cn=HEDI,dc=example,dc=com"
    )
    assert result == "submit"


def test_fetch_user_without_groups_returns_submit(monkeypatch, manager_factory):
    manager = manager_factory()

    class FetchConn:
        def __init__(self):
            self.entries = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def unbind(self):
            pass

        def search(self, base, flt, search_scope=None, attributes=None):
            if base == manager.users_dn:
                self.entries = [SimpleNamespace(uid="jim.doe")]
                return True
            self.entries = []
            return False

    def fake_connection(self, *, user=None, password=None):
        return FetchConn()

    monkeypatch.setattr(manager, "connection", MethodType(fake_connection, manager))

    profile = manager.fetch_user("jim.doe")
    assert profile is not None
    assert profile["role"] == "submit"
    assert profile["allow_portal"] is True
    assert profile["allow_admin"] is False


def test_authenticate_admin_uses_admin_profile(monkeypatch, manager_factory):
    manager = manager_factory()

    def fake_connection(self, *, user=None, password=None):
        assert user == manager.admin_dn

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def unbind(self):
                pass

        return Conn()

    monkeypatch.setattr(manager, "connection", MethodType(fake_connection, manager))

    profile = manager.authenticate("admin", "super-secret")
    assert profile is not None
    assert profile["username"] == "admin"
    assert profile["role"] == "administrator"
    assert profile["allow_admin"] is True


def test_admin_dn_returns_canonical_username(monkeypatch, manager_factory):
    manager = manager_factory()

    def fake_connection(self, *, user=None, password=None):
        assert user == manager.admin_dn

        class Conn:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                pass

            def unbind(self):
                pass

        return Conn()

    monkeypatch.setattr(manager, "connection", MethodType(fake_connection, manager))

    profile = manager.authenticate("cn=admin,dc=example,dc=com", "super-secret")
    assert profile is not None
    assert profile["username"] == "admin"
    assert profile["allow_admin"] is True


def test_fetch_admin_user_returns_admin_profile(monkeypatch, manager_factory):
    manager = manager_factory()

    class AdminConn:
        def __init__(self):
            self.entries = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

        def unbind(self):
            pass

        def search(self, base, flt, search_scope=None, attributes=None):
            if base == manager.admin_dn:
                self.entries = [SimpleNamespace(cn="Admin User")]
                return True
            self.entries = []
            return False

    def fake_connection(self, *, user=None, password=None):
        return AdminConn()

    monkeypatch.setattr(manager, "connection", MethodType(fake_connection, manager))

    profile = manager.fetch_user("admin")
    assert profile is not None
    assert profile["username"] == "admin"
    assert profile["role"] == "administrator"
    assert profile["allow_admin"] is True

