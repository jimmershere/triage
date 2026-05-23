"""Command line utilities for verifying LDAP-backed role access."""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from . import ldap_utils

DEFAULT_ROLE_HIERARCHY = ["view", "submit", "administrator"]
DEFAULT_ROLE_ALIASES = {
    "admin": "administrator",
    "administrator": "administrator",
    "create": "submit",
    "update": "submit",
    "submitter": "submit",
    "editor": "submit",
    "submit": "submit",
    "view": "view",
}
DEFAULT_ADMIN_ROLE = "administrator"
DEFAULT_BOOTSTRAP_HASH = "{SSHA}X7IBzbN9pqFRQwwPu37o7OppFD69OTUK"


def _env_value(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_optional(name: str) -> Optional[str]:
    value = _env_value(name)
    return value or None


def _load_role_configuration() -> Tuple[List[str], Dict[str, str], str]:
    try:  # pragma: no cover - defensive import, exercised in integration
        from . import app as app_module  # type: ignore
    except Exception:
        return DEFAULT_ROLE_HIERARCHY, DEFAULT_ROLE_ALIASES, DEFAULT_ADMIN_ROLE
    return (
        list(getattr(app_module, "ROLE_HIERARCHY", DEFAULT_ROLE_HIERARCHY)),
        dict(getattr(app_module, "ROLE_ALIASES", DEFAULT_ROLE_ALIASES)),
        getattr(app_module, "ROLE_ADMINISTRATOR", DEFAULT_ADMIN_ROLE),
    )


def build_config_from_env() -> ldap_utils.LDAPConfig:
    bootstrap_admin = _env_value("TRIAGE_BOOTSTRAP_ADMIN_USER", "admin") or "admin"
    return ldap_utils.LDAPConfig(
        uri=_env_value("TRIAGE_LDAP_URI", "ldap://ldap:389") or "ldap://ldap:389",
        base_dn=_env_value("TRIAGE_LDAP_BASE_DN", "dc=example,dc=com") or "dc=example,dc=com",
        root_cn=_env_value("TRIAGE_LDAP_ROOT_CN", "ou=TRIAGE") or "ou=TRIAGE",
        users_ou=_env_value("TRIAGE_LDAP_USERS_OU", "ou=users") or "ou=users",
        roles_ou=_env_value("TRIAGE_LDAP_ROLES_OU", "ou=roles") or "ou=roles",
        trading_partners_ou=_env_value("TRIAGE_LDAP_TRADING_OU", "ou=trading-partners")
        or "ou=trading-partners",
        bind_dn=_env_value("TRIAGE_LDAP_BIND_DN"),
        bind_password=_env_value("TRIAGE_LDAP_BIND_PASSWORD"),
        timeout=int(_env_value("TRIAGE_LDAP_TIMEOUT", "10") or "10"),
        bootstrap_username=_env_value("TRIAGE_LDAP_BOOTSTRAP_USERNAME", bootstrap_admin) or bootstrap_admin,
        bootstrap_password_hash=_env_value(
            "TRIAGE_LDAP_BOOTSTRAP_PASSWORD_HASH", DEFAULT_BOOTSTRAP_HASH
        )
        or DEFAULT_BOOTSTRAP_HASH,
        bootstrap_password_plain=_env_optional("TRIAGE_LDAP_BOOTSTRAP_PASSWORD"),
        admin_dn=_env_optional("TRIAGE_LDAP_ADMIN_DN"),
        admin_username=_env_optional("TRIAGE_LDAP_ADMIN_USERNAME")
        or _env_optional("TRIAGE_BOOTSTRAP_ADMIN_USER")
        or bootstrap_admin,
    )


def build_manager() -> ldap_utils.LDAPManager:
    hierarchy, aliases, admin_role = _load_role_configuration()
    config = build_config_from_env()
    return ldap_utils.LDAPManager(
        config,
        role_hierarchy=hierarchy,
        role_aliases=aliases,
        administrator_role=admin_role,
    )


def _format_user(user: Dict[str, Any]) -> str:
    portal = "yes" if user.get("allow_portal") else "no"
    admin = "yes" if user.get("allow_admin") else "no"
    return f"{user.get('username')}: role={user.get('role')} portal={portal} admin={admin}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run LDAP RBAC diagnostics for TurboEDI.")
    parser.add_argument("--list-users", action="store_true", help="List users and effective roles")
    parser.add_argument("--check-user", metavar="USERNAME", help="Inspect a specific user profile")
    parser.add_argument(
        "--authenticate",
        metavar="USERNAME",
        help="Attempt to authenticate a user and report the resulting access profile",
    )
    parser.add_argument(
        "--password",
        help="Password to pair with --authenticate (prompts if omitted)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)

    if not any([args.list_users, args.check_user, args.authenticate]):
        parser.print_help()
        return 1

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    try:
        manager = build_manager()
    except ldap_utils.LDAPUnavailableError as exc:
        parser.error(f"ldap3 is required for diagnostics: {exc}")
    except Exception as exc:  # pragma: no cover - defensive path for CLI usage
        parser.error(f"failed to initialize LDAP manager: {exc}")

    output: Dict[str, Any] = {}

    if args.list_users:
        users = manager.list_users()
        if args.json:
            output["users"] = users
        else:
            for user in users:
                print(_format_user(user))

    if args.check_user:
        profile = manager.fetch_user(args.check_user)
        if args.json:
            output["profile"] = profile
        else:
            if profile:
                print(_format_user(profile))
            else:
                print(f"{args.check_user}: not found")

    if args.authenticate:
        password = args.password
        if password is None:
            password = getpass.getpass(f"Password for {args.authenticate}: ")
        profile = manager.authenticate(args.authenticate, password)
        result = {
            "username": args.authenticate,
            "authenticated": profile is not None,
        }
        if profile:
            result["profile"] = profile
        if args.json:
            output["authentication"] = result
        else:
            status = "success" if profile else "failed"
            print(f"Authentication {status} for {args.authenticate}")
            if profile:
                print(_format_user(profile))

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))

    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
