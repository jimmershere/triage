#!/usr/bin/env python3
"""Guided text installer for Triage.

The installer is intentionally stdlib-only so it can run before project
requirements are installed. It can run interactively, replay an answer file,
write configuration only, or execute the generated setup command plan.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROLE_CHOICES = ["view", "submit", "administrator"]
ROLE_LABELS = {
    "view": "Viewer - can sign in and review information",
    "submit": "Submitter - can upload and manage claim files",
    "administrator": "Administrator - can manage users and system settings",
}
INSTALL_TYPES = ["native", "docker", "podman", "config-only"]
PROFILES = ["local-demo", "small-office", "custom"]
PROVISION_MODES = ["existing", "latest-supported", "pinned", "skip"]
LDAP_IMPLEMENTATIONS = ["openldap", "389ds", "external", "disabled"]
SSL_MODES = ["disable", "prefer", "require", "verify-ca", "verify-full"]
TLS_MODES = ["off", "starttls", "ldaps"]

SUPPORTED_DEFAULTS = {
    "postgres_image": "postgres:16",
    "rabbitmq_image": "rabbitmq:3-management",
    "openldap_image": "osixia/openldap:1.5.0",
    "ds389_image": "389ds/dirsrv:2.5",
    "postgres_native_package": "postgresql postgresql-contrib postgresql-client",
    "rabbitmq_native_package": "rabbitmq-server",
    "openldap_native_package": "slapd ldap-utils",
    "ds389_native_package": "389-ds-base",
}

SENSITIVE_FRAGMENTS = ("password", "secret", "key", "token", "hash")


@dataclass
class InstallerContext:
    root: Path
    answers: dict[str, Any]
    non_interactive: bool = False
    dry_run: bool = False
    execute: bool = False
    output_dir: Path = field(default_factory=lambda: Path(".runtime/installer"))
    generated_admin_password: str | None = None


class Wizard:
    def __init__(self, ctx: InstallerContext):
        self.ctx = ctx

    def _get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.ctx.answers
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def _set(self, dotted: str, value: Any) -> Any:
        node = self.ctx.answers
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        return value

    def text(self, dotted: str, prompt: str, default: str = "", *, secret: bool = False) -> str:
        existing = self._get(dotted)
        if self.ctx.non_interactive:
            return self._set(dotted, str(existing if existing not in (None, "") else default))
        if existing not in (None, ""):
            default = str(existing)
        label = f"{prompt}"
        if default:
            label += f" [{default}]"
        label += ": "
        if secret:
            value = getpass.getpass(label)
        else:
            value = input(label).strip()
        if not value:
            value = default
        return self._set(dotted, value)

    def choice(self, dotted: str, prompt: str, choices: list[str], default: str) -> str:
        existing = self._get(dotted)
        if self.ctx.non_interactive:
            return self._set(dotted, str(existing if existing in choices else default))
        if existing in choices:
            default = str(existing)
        print(f"\n{prompt}")
        for i, item in enumerate(choices, 1):
            print(f"  {i}) {item}")
        while True:
            raw = input(f"Choose 1-{len(choices)} [{default}]: ").strip()
            if not raw:
                return self._set(dotted, default)
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return self._set(dotted, choices[int(raw) - 1])
            if raw in choices:
                return self._set(dotted, raw)
            print("Please choose one of the listed options.")

    def yesno(self, dotted: str, prompt: str, default: bool = False) -> bool:
        existing = self._get(dotted)
        if self.ctx.non_interactive:
            return self._set(dotted, existing if isinstance(existing, bool) else default)
        if isinstance(existing, bool):
            default = existing
        suffix = "Y/n" if default else "y/N"
        raw = input(f"{prompt} [{suffix}]: ").strip().lower()
        if not raw:
            return self._set(dotted, default)
        return self._set(dotted, raw in ("y", "yes", "true", "1", "on"))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def random_secret(label: str, nbytes: int = 32) -> str:
    return f"triage-{label}-" + secrets.token_urlsafe(nbytes)


def pbkdf2_hash(password: str, iterations: int = 180000) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(derived).decode("ascii"),
    )


def quote_url(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def database_url(a: dict[str, Any], *, internal: bool) -> str:
    db = a["database"]
    host = "postgres" if internal else db["host"]
    port = "5432" if internal else str(db["port"])
    user = quote_url(db["user"])
    password = quote_url(db["password"])
    name = quote_url(db["name"])
    query: dict[str, str] = {"sslmode": db.get("sslmode", "disable")}
    if query["sslmode"] == "disable":
        query = {}
    else:
        for source, param in (
            ("sslrootcert", "sslrootcert"),
            ("sslcert", "sslcert"),
            ("sslkey", "sslkey"),
        ):
            if db.get(source):
                query[param] = db[source]
    suffix = "?" + urllib.parse.urlencode(query) if query else ""
    return f"postgresql://{user}:{password}@{host}:{port}/{name}{suffix}"


def rabbit_url(a: dict[str, Any], *, internal: bool) -> str:
    rmq = a["rabbitmq"]
    host = "rabbitmq" if internal else rmq["host"]
    port = "5672" if internal else str(rmq["amqp_port"])
    user = quote_url(rmq["app_user"])
    password = quote_url(rmq["app_password"])
    vhost = quote_url(rmq["vhost"])
    return f"amqp://{user}:{password}@{host}:{port}/{vhost}"


def chmod_0600(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def write_text(path: Path, content: str, *, backup: bool = True, mode_0600: bool = False, dry_run: bool = False) -> None:
    if dry_run:
        print(f"[dry-run] would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = path.with_suffix(path.suffix + time.strftime(".%Y%m%d%H%M%S.bak"))
        shutil.copy2(path, backup_path)
        print(f"Backed up {path} -> {backup_path}")
    path.write_text(content, encoding="utf-8")
    if mode_0600:
        chmod_0600(path)
    print(f"Wrote {path}")


def redact_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return {k: redact_value(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_value(key, v) for v in value]
    if any(fragment in key.lower() for fragment in SENSITIVE_FRAGMENTS):
        return "<redacted>" if value else value
    return value


def render_env(a: dict[str, Any], *, container: bool) -> str:
    db_url = database_url(a, internal=container)
    rmq_url = rabbit_url(a, internal=container)
    db = a["database"]
    rmq = a["rabbitmq"]
    ports = a["ports"]
    ldap = a["ldap"]
    pg_host = "postgres" if container else db["host"]
    rmq_host = "rabbitmq" if container else rmq["host"]
    ldap_uri = ldap.get("uri") or ("ldap://ldap:389" if container else f"ldap://127.0.0.1:{ldap['ldap_port']}")
    lines = {
        "POSTGRES_USER": db["user"],
        "POSTGRES_PASSWORD": db["password"],
        "POSTGRES_DB": db["name"],
        "POSTGRES_HOST": pg_host,
        "POSTGRES_HOST_PORT": str(db["port"]),
        "POSTGRES_SSLMODE": db["sslmode"],
        "POSTGRES_SSL_ENABLED": "true" if db.get("server_ssl") else "false",
        "POSTGRES_SSL_CA_FILE": db.get("sslrootcert", ""),
        "POSTGRES_SSL_CERT_FILE": db.get("sslcert", ""),
        "POSTGRES_SSL_KEY_FILE": db.get("sslkey", ""),
        "DATABASE_URL": db_url,
        "RABBITMQ_DEFAULT_USER": rmq["admin_user"],
        "RABBITMQ_DEFAULT_PASS": rmq["admin_password"],
        "RABBITMQ_AMQP_HOST_PORT": str(rmq["amqp_port"]),
        "RABBITMQ_MGMT_HOST_PORT": str(rmq["management_port"]),
        "RMQ_HOST": rmq_host,
        "RMQ_PORT": "5672" if container else str(rmq["amqp_port"]),
        "RMQ_USER": rmq["app_user"],
        "RMQ_PASS": rmq["app_password"],
        "RMQ_VHOST": rmq["vhost"],
        "RMQ_QUEUE": rmq["ingest_queue"],
        "RMQ_ACKS_QUEUE": rmq["acks_queue"],
        "RABBITMQ_URL": rmq_url,
        "RABBITMQ_TLS_ENABLED": "true" if rmq.get("tls_enabled") else "false",
        "RABBITMQ_TLS_PORT": str(rmq.get("tls_port", "5671")),
        "RABBITMQ_TLS_CA_FILE": rmq.get("tls_ca", ""),
        "RABBITMQ_TLS_CERT_FILE": rmq.get("tls_cert", ""),
        "RABBITMQ_TLS_KEY_FILE": rmq.get("tls_key", ""),
        "TRIAGE_API_BASE": "http://api:8000" if container else f"http://127.0.0.1:{ports['api']}",
        "TRIAGE_SHARED_SECRET": a["security"]["shared_secret"],
        "TRIAGE_SESSION_SECRET": a["security"]["session_secret"],
        "RBAC_COOKIE_SECURE": "true" if a["security"].get("secure_cookie") else "false",
        "TRIAGE_CORS_ORIGINS": a["security"]["cors_origins"],
        "TRIAGE_PASSWORD_ITERATIONS": str(a["security"]["password_iterations"]),
        "TRIAGE_MIN_PASSWORD_LENGTH": str(a["security"]["min_password_length"]),
        "TRIAGE_BOOTSTRAP_ADMIN_USER": a["admin"]["username"],
        "TRIAGE_BOOTSTRAP_ADMIN_HASH": a["admin"]["password_hash"],
        "TRIAGE_LDAP_ENABLED": "true" if ldap["enabled"] else "false",
        "TRIAGE_LDAP_URI": ldap_uri,
        "TRIAGE_LDAP_BASE_DN": ldap["base_dn"],
        "TRIAGE_LDAP_ROOT_CN": ldap["root_ou"],
        "TRIAGE_LDAP_USERS_OU": ldap["users_ou"],
        "TRIAGE_LDAP_ROLES_OU": ldap["roles_ou"],
        "TRIAGE_LDAP_TRADING_OU": ldap["trading_ou"],
        "TRIAGE_LDAP_BIND_DN": ldap.get("bind_dn", ""),
        "TRIAGE_LDAP_BIND_PASSWORD": ldap.get("bind_password", ""),
        "TRIAGE_LDAP_TIMEOUT": str(ldap.get("timeout", 10)),
        "TRIAGE_LDAP_ADMIN_DN": ldap.get("admin_dn", ""),
        "TRIAGE_LDAP_ADMIN_USERNAME": ldap.get("admin_username", a["admin"]["username"]),
        "TRIAGE_LDAP_BOOTSTRAP_USERNAME": ldap.get("bootstrap_username", a["admin"]["username"]),
        "TRIAGE_LDAP_BOOTSTRAP_PASSWORD": ldap.get("bootstrap_password", ""),
        "LDAP_ORGANISATION": ldap.get("organization", "Triage"),
        "LDAP_DOMAIN": ldap.get("domain", "example.com"),
        "LDAP_ADMIN_PASSWORD": ldap.get("admin_password", ""),
        "LDAP_CONFIG_PASSWORD": ldap.get("config_password", ""),
        "LDAP_HOST_PORT": str(ldap["ldap_port"]),
        "LDAP_LDAPS_HOST_PORT": str(ldap.get("ldaps_port", 636)),
        "LDAP_TLS": "true" if ldap.get("tls_mode") in ("starttls", "ldaps") else "false",
        "LDAP_TLS_CRT_FILENAME": Path(ldap.get("tls_cert", "ldap.crt")).name,
        "LDAP_TLS_KEY_FILENAME": Path(ldap.get("tls_key", "ldap.key")).name,
        "LDAP_TLS_CA_CRT_FILENAME": Path(ldap.get("tls_ca", "ca.crt")).name,
        "FRONTEND_HTTP_PORT": str(ports["frontend_http"]),
        "FRONTEND_HTTPS_PORT": str(ports["frontend_https"]),
        "RBAC_PORT": str(ports["rbac"]),
        "API_PORT": str(ports["api"]),
        "ARCHIVE_DIR": a["paths"]["archive_dir"],
        "OLLAMA_URL": a["optional"]["ollama_url"],
        "OLLAMA_MODEL": a["optional"]["ollama_model"],
        "TURBO_PIPELINE_ENABLED": "1" if a["optional"]["turbo_pipeline_enabled"] else "0",
        "LOG_LEVEL": a["optional"]["log_level"],
        "TRIAGE_POSTGRES_IMAGE": a["dependencies"]["postgres"]["image"],
        "TRIAGE_RABBITMQ_IMAGE": a["dependencies"]["rabbitmq"]["image"],
        "TRIAGE_LDAP_IMAGE": a["dependencies"]["ldap"]["image"],
    }
    return "\n".join(f"{key}={shell_env_quote(str(value))}" for key, value in lines.items()) + "\n"


def shell_env_quote(value: str) -> str:
    if value == "":
        return ""
    if any(ch.isspace() or ch in "'\"#$`\\" for ch in value):
        return "'" + value.replace("'", "'\"'\"'") + "'"
    return value


def render_compose_override(a: dict[str, Any]) -> str:
    db = a["database"]
    dep = a["dependencies"]
    pg_command = []
    pg_volumes = []
    if db.get("server_ssl"):
        pg_command = [
            "-c", "ssl=on",
            "-c", "ssl_cert_file=/run/secrets/postgres/server.crt",
            "-c", "ssl_key_file=/run/secrets/postgres/server.key",
        ]
        cert_dir = str(Path(db.get("server_cert", "deploy/generated/postgres/server.crt")).parent)
        pg_volumes.append(f"      - ./{cert_dir}:/run/secrets/postgres:ro")
    pg_command_yaml = ""
    if pg_command:
        pg_command_yaml = "\n    command:\n" + "\n".join(f"      - {json.dumps(item)}" for item in ["postgres", *pg_command])
    pg_volumes_yaml = ""
    if pg_volumes:
        pg_volumes_yaml = "\n    volumes:\n" + "\n".join(pg_volumes)
    return textwrap.dedent(f"""
    # Generated by scripts/triage-installer.py. Review before production use.
    # Ports, credentials, and URLs are read from the generated .env file.
    services:
      postgres:
        image: {dep['postgres']['image']}{pg_command_yaml}{pg_volumes_yaml}
      rabbitmq:
        image: {dep['rabbitmq']['image']}
      ldap:
        image: {dep['ldap']['image']}
    """).strip() + "\n"

def render_ssl_snippets(a: dict[str, Any]) -> tuple[str, str]:
    db = a["database"]
    ssl_on = "on" if db.get("server_ssl") else "off"
    conf = textwrap.dedent(f"""
    # Generated PostgreSQL SSL snippet for Triage.
    # Include these settings in postgresql.conf only after confirming paths and ownership.
    ssl = {ssl_on}
    ssl_ca_file = '{db.get('sslrootcert', '')}'
    ssl_cert_file = '{db.get('server_cert', '')}'
    ssl_key_file = '{db.get('server_key', '')}'
    """).strip() + "\n"
    method = "scram-sha-256"
    host_rule = "hostssl" if db.get("require_ssl") else "host"
    hba = textwrap.dedent(f"""
    # Generated pg_hba.conf entries for Triage.
    {host_rule} {db['name']} {db['user']} 0.0.0.0/0 {method}
    {host_rule} {db['name']} {db['user']} ::/0 {method}
    """).strip() + "\n"
    return conf, hba


def render_ldap_ldif(a: dict[str, Any]) -> str:
    ldap = a["ldap"]
    base = ldap["base_dn"]
    root_value = ldap["root_ou"].split("=", 1)[-1]
    users_value = ldap["users_ou"].split("=", 1)[-1]
    roles_value = ldap["roles_ou"].split("=", 1)[-1]
    trading_value = ldap["trading_ou"].split("=", 1)[-1]
    admin = a["admin"]["username"]
    return textwrap.dedent(f"""
    # Generated LDAP bootstrap LDIF for Triage.
    dn: {ldap['root_ou']},{base}
    objectClass: top
    objectClass: organizationalUnit
    ou: {root_value}

    dn: {ldap['users_ou']},{ldap['root_ou']},{base}
    objectClass: top
    objectClass: organizationalUnit
    ou: {users_value}

    dn: {ldap['roles_ou']},{ldap['root_ou']},{base}
    objectClass: top
    objectClass: organizationalUnit
    ou: {roles_value}

    dn: {ldap['trading_ou']},{ldap['root_ou']},{base}
    objectClass: top
    objectClass: organizationalUnit
    ou: {trading_value}

    dn: cn=view,{ldap['roles_ou']},{ldap['root_ou']},{base}
    objectClass: top
    objectClass: groupOfNames
    cn: view
    member: uid={admin},{ldap['users_ou']},{ldap['root_ou']},{base}

    dn: cn=submit,{ldap['roles_ou']},{ldap['root_ou']},{base}
    objectClass: top
    objectClass: groupOfNames
    cn: submit
    member: uid={admin},{ldap['users_ou']},{ldap['root_ou']},{base}

    dn: cn=administrator,{ldap['roles_ou']},{ldap['root_ou']},{base}
    objectClass: top
    objectClass: groupOfNames
    cn: administrator
    member: uid={admin},{ldap['users_ou']},{ldap['root_ou']},{base}
    """).strip() + "\n"


def native_command_plan(a: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    deps = a["dependencies"]
    install_deps = bool(a["actions"].get("install_dependencies"))
    start_services = bool(a["actions"].get("start_services"))
    if install_deps and any(deps[name]["mode"] in ("latest-supported", "pinned") for name in ("postgres", "rabbitmq", "ldap")):
        commands.append("sudo apt-get update")
        commands.append("sudo apt-get install -y ca-certificates curl gnupg lsb-release")
    if install_deps:
        pg = deps["postgres"]
        if pg["mode"] in ("latest-supported", "pinned"):
            if pg.get("package_source") == "pgdg":
                commands.extend([
                    "sudo apt-get install -y postgresql-common",
                    "sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y",
                ])
            pkg = pg.get("package") or SUPPORTED_DEFAULTS["postgres_native_package"]
            commands.append(f"sudo apt-get install -y {pkg}")
        rmq = deps["rabbitmq"]
        if rmq["mode"] in ("latest-supported", "pinned"):
            commands.append(f"sudo apt-get install -y {rmq.get('package') or SUPPORTED_DEFAULTS['rabbitmq_native_package']}")
        ldap_dep = deps["ldap"]
        if ldap_dep["mode"] in ("latest-supported", "pinned") and a["ldap"]["implementation"] != "disabled":
            commands.append(f"sudo apt-get install -y {ldap_dep.get('package') or SUPPORTED_DEFAULTS['openldap_native_package']}")
        commands.extend([
            "sudo systemctl enable --now postgresql || true",
            "sudo systemctl enable --now rabbitmq-server || true",
            "sudo rabbitmq-plugins enable rabbitmq_management || true",
            f"sudo rabbitmqctl add_vhost {shell_quote(a['rabbitmq']['vhost'])} || true",
            f"sudo rabbitmqctl add_user {shell_quote(a['rabbitmq']['app_user'])} '<app-password-redacted>' || sudo rabbitmqctl change_password {shell_quote(a['rabbitmq']['app_user'])} '<app-password-redacted>'",
            f"sudo rabbitmqctl set_permissions -p {shell_quote(a['rabbitmq']['vhost'])} {shell_quote(a['rabbitmq']['app_user'])} '.*' '.*' '.*'",
            f"TRIAGE_ENV_FILE={shell_quote(str(a['paths']['env_file']))} bash scripts/install-native.sh",
        ])
    if start_services:
        commands.extend(postgres_dev_cert_commands(a))
        commands.append(f"TRIAGE_ENV_FILE={shell_quote(str(a['paths']['env_file']))} bash scripts/run-local-stack.sh")
    return commands

def container_command_plan(a: dict[str, Any]) -> list[str]:
    install_type = a["install_type"]
    compose = a["container"].get("compose_command")
    if not compose:
        compose = "docker compose" if install_type == "docker" else "podman compose"
    commands: list[str] = []
    if a["actions"].get("install_dependencies"):
        commands.append(f"{compose} -f docker-compose.yml -f compose.override.yml pull postgres rabbitmq ldap")
    if a["actions"].get("start_services"):
        commands.extend(postgres_dev_cert_commands(a))
        commands.append(f"{compose} -f docker-compose.yml -f compose.override.yml up --build -d")
    return commands


def postgres_dev_cert_commands(a: dict[str, Any]) -> list[str]:
    db = a["database"]
    if not db.get("generate_dev_cert"):
        return []
    cert = Path(db.get("server_cert") or "deploy/generated/postgres/server.crt")
    key = Path(db.get("server_key") or "deploy/generated/postgres/server.key")
    ca = Path(db.get("sslrootcert") or cert)
    return [
        f"mkdir -p {shell_quote(str(cert.parent))}",
        (
            "openssl req -new -x509 -days 365 -nodes "
            f"-subj '/CN=localhost' -keyout {shell_quote(str(key))} -out {shell_quote(str(cert))}"
        ),
        f"cp {shell_quote(str(cert))} {shell_quote(str(ca))}" if ca != cert else "true",
        f"chmod 600 {shell_quote(str(key))}",
    ]

def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def check_http(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def run_commands(commands: Iterable[str], *, dry_run: bool, execute: bool) -> None:
    for cmd in commands:
        print(f"$ {cmd}")
        if dry_run or not execute:
            continue
        subprocess.run(cmd, shell=True, check=True)


def bootstrap_users(a: dict[str, Any], *, dry_run: bool, execute: bool) -> None:
    users = a.get("users", [])
    if not users:
        return
    api_base = a["runtime_urls"]["api_base"]
    secret = a["security"]["shared_secret"]
    for user in users:
        payload = {
            "username": user["username"],
            "password": user["password"],
            "role": user["role"],
        }
        print(f"Apply user {user['username']} as role {user['role']}")
        if dry_run or not execute:
            continue
        request = urllib.request.Request(
            api_base.rstrip("/") + "/admin/users",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-TRIAGE-SECRET": secret},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status not in (200, 201, 409):
                    print(f"Warning: user {user['username']} returned HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code != 409:
                print(f"Warning: user {user['username']} failed with HTTP {exc.code}")
        except urllib.error.URLError as exc:
            print(f"Warning: unable to create user {user['username']}: {exc}")


def default_answers(root: Path) -> dict[str, Any]:
    shared = random_secret("shared")
    session = random_secret("session")
    admin_password = random_secret("admin", 18)
    return {
        "install_type": "docker" if shutil.which("docker") else "native",
        "profile": "local-demo",
        "paths": {
            "repo": str(root),
            "runtime_dir": ".runtime",
            "archive_dir": "/archive",
            "log_dir": ".runtime/logs",
            "env_file": ".env.localhost",
            "answer_file": ".runtime/installer/triage-installer.answers.json",
        },
        "ports": {
            "frontend_http": 8080,
            "frontend_https": 8443,
            "api": 8000,
            "rbac": 4180,
        },
        "dependencies": {
            "postgres": {"mode": "latest-supported", "version": "16", "image": SUPPORTED_DEFAULTS["postgres_image"], "package_source": "distro", "package": SUPPORTED_DEFAULTS["postgres_native_package"]},
            "rabbitmq": {"mode": "latest-supported", "version": "3-management", "image": SUPPORTED_DEFAULTS["rabbitmq_image"], "package_source": "distro", "package": SUPPORTED_DEFAULTS["rabbitmq_native_package"]},
            "ldap": {"mode": "latest-supported", "version": "openldap", "image": SUPPORTED_DEFAULTS["openldap_image"], "package_source": "distro", "package": SUPPORTED_DEFAULTS["openldap_native_package"]},
        },
        "database": {
            "host": "127.0.0.1",
            "port": 15432,
            "name": "edi",
            "user": "edi",
            "password": random_secret("pg", 18),
            "admin_password": random_secret("pgadmin", 18),
            "listen_address": "*",
            "sslmode": "disable",
            "server_ssl": False,
            "require_ssl": False,
            "generate_dev_cert": False,
            "sslrootcert": "",
            "sslcert": "",
            "sslkey": "",
            "server_cert": "deploy/generated/postgres/server.crt",
            "server_key": "deploy/generated/postgres/server.key",
            "data_dir": "pgdata",
            "backup_dir": ".runtime/backups/postgres",
        },
        "rabbitmq": {
            "host": "127.0.0.1",
            "amqp_port": 15673,
            "management_port": 35672,
            "tls_port": 5671,
            "vhost": "/",
            "app_user": "ediapp",
            "app_password": random_secret("rmq", 18),
            "admin_user": "ediapp",
            "admin_password": random_secret("rmqadmin", 18),
            "ingest_queue": "edi_files",
            "acks_queue": "acks",
            "management_enabled": True,
            "tls_enabled": False,
            "tls_ca": "",
            "tls_cert": "",
            "tls_key": "",
            "create_queues": True,
        },
        "security": {
            "shared_secret": shared,
            "session_secret": session,
            "cors_origins": "*",
            "min_password_length": 8,
            "password_iterations": 180000,
            "network_facing": False,
            "secure_cookie": False,
        },
        "admin": {
            "username": "admin",
            "password": admin_password,
            "password_hash": pbkdf2_hash(admin_password),
        },
        "users": [],
        "ldap": {
            "enabled": False,
            "implementation": "openldap",
            "uri": "",
            "ldap_port": 3389,
            "ldaps_port": 3636,
            "tls_mode": "off",
            "base_dn": "dc=example,dc=com",
            "organization": "Triage",
            "domain": "example.com",
            "root_ou": "ou=TRIAGE",
            "users_ou": "ou=users",
            "roles_ou": "ou=roles",
            "trading_ou": "ou=trading-partners",
            "bind_dn": "",
            "bind_password": "",
            "admin_dn": "",
            "admin_username": "admin",
            "admin_password": random_secret("ldapadmin", 18),
            "config_password": random_secret("ldapcfg", 18),
            "bootstrap_username": "admin",
            "bootstrap_password": admin_password,
            "tls_ca": "deploy/generated/ldap/ca.crt",
            "tls_cert": "deploy/generated/ldap/ldap.crt",
            "tls_key": "deploy/generated/ldap/ldap.key",
            "create_role_groups": True,
            "timeout": 10,
        },
        "container": {"compose_command": ""},
        "optional": {
            "ollama_url": "http://localhost:11434",
            "ollama_model": "qwen2.5:3b",
            "turbo_pipeline_enabled": True,
            "ingest_samples": False,
            "log_level": "INFO",
        },
        "actions": {
            "write_config": True,
            "install_dependencies": False,
            "start_services": False,
            "create_users": False,
            "run_health_checks": True,
            "save_answer_file": True,
        },
        "runtime_urls": {},
    }


def collect_answers(ctx: InstallerContext) -> dict[str, Any]:
    w = Wizard(ctx)
    print("\nTriage Guided Installer")
    print("This wizard uses simple prompts to prepare a working Triage system.")
    a = ctx.answers
    a["install_type"] = w.choice("install_type", "How do you want to run Triage?", INSTALL_TYPES, a["install_type"])
    a["profile"] = w.choice("profile", "Which setup profile fits best?", PROFILES, a["profile"])
    container = a["install_type"] in ("docker", "podman")
    a["paths"]["env_file"] = ".env" if container else ".env.localhost"
    if not container:
        if int(a["database"].get("port", 15432)) == 15432:
            a["database"]["port"] = 5432
        if int(a["rabbitmq"].get("amqp_port", 15673)) == 15673:
            a["rabbitmq"]["amqp_port"] = 5672
        if int(a["rabbitmq"].get("management_port", 35672)) == 35672:
            a["rabbitmq"]["management_port"] = 15672

    print("\nPaths")
    w.text("paths.archive_dir", "Where should Triage store archived files?", a["paths"]["archive_dir"])
    w.text("paths.runtime_dir", "Where should runtime files/logs go?", a["paths"]["runtime_dir"])

    print("\nService ports")
    for key, label in (
        ("frontend_http", "Frontend HTTP port"),
        ("frontend_https", "Frontend HTTPS port"),
        ("api", "API port"),
        ("rbac", "RBAC login port"),
    ):
        a["ports"][key] = int(w.text(f"ports.{key}", label, str(a["ports"][key])))

    print("\nSystem services")
    for service in ("postgres", "rabbitmq", "ldap"):
        mode = w.choice(f"dependencies.{service}.mode", f"{service}: install/use service mode", PROVISION_MODES, a["dependencies"][service]["mode"])
        if mode == "pinned":
            w.text(f"dependencies.{service}.version", f"{service}: supported version/tag to pin", a["dependencies"][service]["version"])
        if container:
            w.text(f"dependencies.{service}.image", f"{service}: container image tag", a["dependencies"][service]["image"])
        elif mode in ("latest-supported", "pinned"):
            w.text(f"dependencies.{service}.package_source", f"{service}: package source (distro/vendor)", a["dependencies"][service].get("package_source", "distro"))
            w.text(f"dependencies.{service}.package", f"{service}: package names", a["dependencies"][service].get("package", ""))

    print("\nPostgreSQL")
    w.text("database.host", "Postgres host", a["database"]["host"])
    a["database"]["port"] = int(w.text("database.port", "Postgres host port", str(a["database"]["port"])))
    w.text("database.name", "Postgres database name", a["database"]["name"])
    w.text("database.user", "Postgres app user", a["database"]["user"])
    w.text("database.password", "Postgres app password", a["database"]["password"], secret=not ctx.non_interactive)
    w.text("database.admin_password", "Postgres admin password", a["database"]["admin_password"], secret=not ctx.non_interactive)
    sslmode = w.choice("database.sslmode", "Postgres client SSL mode", SSL_MODES, a["database"]["sslmode"])
    a["database"]["server_ssl"] = w.yesno("database.server_ssl", "Enable SSL on the Postgres server?", a["database"]["server_ssl"])
    if sslmode != "disable" or a["database"]["server_ssl"]:
        a["database"]["require_ssl"] = w.yesno("database.require_ssl", "Require SSL for Triage database connections?", a["database"]["require_ssl"])
        a["database"]["generate_dev_cert"] = w.yesno("database.generate_dev_cert", "Generate local development database certificates?", a["database"]["generate_dev_cert"])
        w.text("database.sslrootcert", "Postgres CA/root certificate path", a["database"].get("sslrootcert", ""))
        w.text("database.server_cert", "Postgres server certificate path", a["database"].get("server_cert", ""))
        w.text("database.server_key", "Postgres server key path", a["database"].get("server_key", ""))
        w.text("database.sslcert", "Optional client certificate path", a["database"].get("sslcert", ""))
        w.text("database.sslkey", "Optional client key path", a["database"].get("sslkey", ""))

    print("\nRabbitMQ")
    w.text("rabbitmq.host", "RabbitMQ host", a["rabbitmq"]["host"])
    a["rabbitmq"]["amqp_port"] = int(w.text("rabbitmq.amqp_port", "RabbitMQ AMQP host port", str(a["rabbitmq"]["amqp_port"])))
    a["rabbitmq"]["management_port"] = int(w.text("rabbitmq.management_port", "RabbitMQ management UI port", str(a["rabbitmq"]["management_port"])))
    w.text("rabbitmq.vhost", "RabbitMQ vhost", a["rabbitmq"]["vhost"])
    w.text("rabbitmq.app_user", "RabbitMQ application user", a["rabbitmq"]["app_user"])
    w.text("rabbitmq.app_password", "RabbitMQ application password", a["rabbitmq"]["app_password"], secret=not ctx.non_interactive)
    w.text("rabbitmq.admin_user", "RabbitMQ administrator user", a["rabbitmq"]["admin_user"])
    w.text("rabbitmq.admin_password", "RabbitMQ administrator password", a["rabbitmq"]["admin_password"], secret=not ctx.non_interactive)
    w.text("rabbitmq.ingest_queue", "Ingest queue name", a["rabbitmq"]["ingest_queue"])
    w.text("rabbitmq.acks_queue", "Acknowledgement queue name", a["rabbitmq"]["acks_queue"])
    a["rabbitmq"]["tls_enabled"] = w.yesno("rabbitmq.tls_enabled", "Enable RabbitMQ TLS listener?", a["rabbitmq"]["tls_enabled"])
    if a["rabbitmq"]["tls_enabled"]:
        a["rabbitmq"]["tls_port"] = int(w.text("rabbitmq.tls_port", "RabbitMQ TLS port", str(a["rabbitmq"]["tls_port"])))
        w.text("rabbitmq.tls_ca", "RabbitMQ CA certificate path", a["rabbitmq"].get("tls_ca", ""))
        w.text("rabbitmq.tls_cert", "RabbitMQ server certificate path", a["rabbitmq"].get("tls_cert", ""))
        w.text("rabbitmq.tls_key", "RabbitMQ server key path", a["rabbitmq"].get("tls_key", ""))

    print("\nIdentity and users")
    ldap_impl = w.choice("ldap.implementation", "LDAP option", LDAP_IMPLEMENTATIONS, a["ldap"]["implementation"])
    if ldap_impl == "389ds":
        if a["dependencies"]["ldap"].get("package") == SUPPORTED_DEFAULTS["openldap_native_package"]:
            a["dependencies"]["ldap"]["package"] = SUPPORTED_DEFAULTS["ds389_native_package"]
        if a["dependencies"]["ldap"].get("image") == SUPPORTED_DEFAULTS["openldap_image"]:
            a["dependencies"]["ldap"]["image"] = SUPPORTED_DEFAULTS["ds389_image"]
    elif ldap_impl == "openldap":
        if a["dependencies"]["ldap"].get("package") == SUPPORTED_DEFAULTS["ds389_native_package"]:
            a["dependencies"]["ldap"]["package"] = SUPPORTED_DEFAULTS["openldap_native_package"]
        if a["dependencies"]["ldap"].get("image") == SUPPORTED_DEFAULTS["ds389_image"]:
            a["dependencies"]["ldap"]["image"] = SUPPORTED_DEFAULTS["openldap_image"]
    a["ldap"]["enabled"] = ldap_impl in ("openldap", "389ds", "external") and w.yesno("ldap.enabled", "Enable LDAP authentication in Triage?", a["ldap"]["enabled"])
    if a["ldap"]["enabled"]:
        w.text("ldap.base_dn", "LDAP base DN", a["ldap"]["base_dn"])
        w.text("ldap.organization", "LDAP organization name", a["ldap"]["organization"])
        w.text("ldap.root_ou", "LDAP root OU", a["ldap"]["root_ou"])
        w.text("ldap.users_ou", "LDAP users OU", a["ldap"]["users_ou"])
        w.text("ldap.roles_ou", "LDAP roles OU", a["ldap"]["roles_ou"])
        w.text("ldap.trading_ou", "LDAP trading partners OU", a["ldap"]["trading_ou"])
        a["ldap"]["ldap_port"] = int(w.text("ldap.ldap_port", "LDAP host port", str(a["ldap"]["ldap_port"])))
        a["ldap"]["tls_mode"] = w.choice("ldap.tls_mode", "LDAP TLS mode", TLS_MODES, a["ldap"]["tls_mode"])
        if a["ldap"]["tls_mode"] != "off":
            a["ldap"]["ldaps_port"] = int(w.text("ldap.ldaps_port", "LDAPS host port", str(a["ldap"]["ldaps_port"])))
            w.text("ldap.tls_ca", "LDAP CA certificate path", a["ldap"].get("tls_ca", ""))
            w.text("ldap.tls_cert", "LDAP server certificate path", a["ldap"].get("tls_cert", ""))
            w.text("ldap.tls_key", "LDAP server key path", a["ldap"].get("tls_key", ""))
        w.text("ldap.admin_dn", "Optional external LDAP admin DN", a["ldap"].get("admin_dn", ""))
        w.text("ldap.bind_dn", "Optional LDAP bind DN", a["ldap"].get("bind_dn", ""))
        w.text("ldap.bind_password", "Optional LDAP bind password", a["ldap"].get("bind_password", ""), secret=not ctx.non_interactive)

    w.text("admin.username", "First administrator username", a["admin"]["username"])
    admin_pwd = w.text("admin.password", "First administrator password", a["admin"]["password"], secret=not ctx.non_interactive)
    if admin_pwd != a["admin"].get("password") or not a["admin"].get("password_hash"):
        a["admin"]["password_hash"] = pbkdf2_hash(admin_pwd, int(a["security"]["password_iterations"]))
    if ctx.non_interactive and "CHANGE_ME" not in admin_pwd:
        ctx.generated_admin_password = admin_pwd
    if not ctx.non_interactive:
        while w.yesno("_add_user_prompt", "Add another user now?", False):
            username = input("  Username: ").strip()
            role = w.choice("_tmp_role", "  Role", ROLE_CHOICES, "view")
            password = getpass.getpass("  Initial password: ")
            a.setdefault("users", []).append({"username": username, "role": role, "password": password})
            a.pop("_add_user_prompt", None)
            a.pop("_tmp_role", None)

    print("\nSecurity")
    w.text("security.cors_origins", "Allowed browser origins (comma-separated or *)", a["security"]["cors_origins"])
    a["security"]["network_facing"] = w.yesno("security.network_facing", "Will users connect from other machines?", a["security"]["network_facing"])
    a["security"]["secure_cookie"] = w.yesno("security.secure_cookie", "Require secure HTTPS cookies?", a["security"]["secure_cookie"])

    print("\nOptional processing")
    w.text("optional.ollama_url", "Ollama URL", a["optional"]["ollama_url"])
    w.text("optional.ollama_model", "Ollama model", a["optional"]["ollama_model"])
    a["optional"]["turbo_pipeline_enabled"] = w.yesno("optional.turbo_pipeline_enabled", "Enable validation/scrubbing pipeline on ingest?", a["optional"]["turbo_pipeline_enabled"])
    a["optional"]["ingest_samples"] = w.yesno("optional.ingest_samples", "Queue sample files after startup?", a["optional"]["ingest_samples"])

    print("\nFinal actions")
    for key, label in (
        ("write_config", "Write generated configuration files?"),
        ("install_dependencies", "Install/download selected system services?"),
        ("start_services", "Start Triage services after writing configuration?"),
        ("create_users", "Create additional users after startup?"),
        ("run_health_checks", "Run health checks?"),
        ("save_answer_file", "Save an answer file for repeat installs?"),
    ):
        a["actions"][key] = w.yesno(f"actions.{key}", label, a["actions"][key])

    api_base = f"http://127.0.0.1:{a['ports']['api']}"
    frontend_base = f"http://127.0.0.1:{a['ports']['frontend_http']}"
    rbac_base = f"http://127.0.0.1:{a['ports']['rbac']}"
    a["runtime_urls"] = {"api_base": api_base, "frontend_base": frontend_base, "rbac_base": rbac_base}
    return a


def write_outputs(ctx: InstallerContext) -> None:
    a = ctx.answers
    container = a["install_type"] in ("docker", "podman")
    output_dir = ctx.root / ctx.output_dir
    env_path = ctx.root / a["paths"]["env_file"]
    if a["actions"].get("write_config"):
        write_text(env_path, render_env(a, container=container), mode_0600=True, dry_run=ctx.dry_run)
        if container:
            write_text(ctx.root / "compose.override.yml", render_compose_override(a), dry_run=ctx.dry_run)
        pg_conf, pg_hba = render_ssl_snippets(a)
        write_text(output_dir / "postgresql-ssl.conf.snippet", pg_conf, dry_run=ctx.dry_run)
        write_text(output_dir / "pg_hba.triage.conf.snippet", pg_hba, dry_run=ctx.dry_run)
        write_text(output_dir / "ldap-bootstrap.ldif", render_ldap_ldif(a), dry_run=ctx.dry_run)
        public_users = [{"username": u["username"], "role": u["role"], "password": "<set during installer>"} for u in a.get("users", [])]
        write_text(output_dir / "installer-users.json", json.dumps(public_users, indent=2) + "\n", mode_0600=True, dry_run=ctx.dry_run)
    if a["actions"].get("save_answer_file"):
        answer_path = ctx.root / a["paths"]["answer_file"]
        write_text(answer_path, json.dumps(a, indent=2, sort_keys=True) + "\n", mode_0600=True, dry_run=ctx.dry_run)
    if ctx.generated_admin_password and not ctx.dry_run:
        cred_path = output_dir / "initial-admin-credentials.txt"
        write_text(cred_path, f"username={a['admin']['username']}\npassword={ctx.generated_admin_password}\n", backup=False, mode_0600=True, dry_run=False)
    write_text(output_dir / "post-install-summary.txt", render_summary(a), dry_run=ctx.dry_run)


def render_summary(a: dict[str, Any]) -> str:
    return textwrap.dedent(f"""
    Triage installer summary
    ========================
    Install type: {a['install_type']}
    Profile: {a['profile']}
    Frontend: {a['runtime_urls'].get('frontend_base')}
    API: {a['runtime_urls'].get('api_base')}
    RBAC login: {a['runtime_urls'].get('rbac_base')}
    Admin username: {a['admin']['username']}
    Environment file: {a['paths']['env_file']}
    Archive directory: {a['paths']['archive_dir']}
    Runtime logs: {a['paths']['log_dir']}
    Postgres SSL mode: {a['database']['sslmode']}
    Postgres server SSL: {a['database']['server_ssl']}
    RabbitMQ management port: {a['rabbitmq']['management_port']}
    LDAP enabled: {a['ldap']['enabled']} ({a['ldap']['implementation']})

    Secrets were written to generated configuration files and are not printed here.
    Review .runtime/installer for snippets, answer files, and setup notes.
    """).lstrip()


def print_review(a: dict[str, Any]) -> None:
    print("\nReview configuration")
    print("--------------------")
    summary = {
        "install_type": a["install_type"],
        "profile": a["profile"],
        "env_file": a["paths"]["env_file"],
        "frontend_port": a["ports"]["frontend_http"],
        "api_port": a["ports"]["api"],
        "postgres": {"mode": a["dependencies"]["postgres"]["mode"], "host_port": a["database"]["port"], "sslmode": a["database"]["sslmode"], "server_ssl": a["database"]["server_ssl"]},
        "rabbitmq": {"mode": a["dependencies"]["rabbitmq"]["mode"], "amqp_port": a["rabbitmq"]["amqp_port"], "management_port": a["rabbitmq"]["management_port"], "tls": a["rabbitmq"]["tls_enabled"]},
        "ldap": {"mode": a["dependencies"]["ldap"]["mode"], "enabled": a["ldap"]["enabled"], "implementation": a["ldap"]["implementation"], "tls_mode": a["ldap"]["tls_mode"]},
        "actions": a["actions"],
    }
    print(json.dumps(summary, indent=2))


def run_health_checks(a: dict[str, Any]) -> None:
    print("\nHealth checks")
    checks = [
        ("API OpenAPI", check_http(a["runtime_urls"]["api_base"].rstrip("/") + "/openapi.json")),
        ("Frontend health", check_http(a["runtime_urls"]["frontend_base"].rstrip("/") + "/healthz")),
        ("RBAC login", check_http(a["runtime_urls"]["rbac_base"].rstrip("/") + "/oauth2/start")),
        ("Postgres port", check_port("127.0.0.1", int(a["database"]["port"]))),
        ("RabbitMQ AMQP port", check_port("127.0.0.1", int(a["rabbitmq"]["amqp_port"]))),
        ("RabbitMQ management port", check_port("127.0.0.1", int(a["rabbitmq"]["management_port"]))),
    ]
    if a["ldap"]["enabled"]:
        checks.append(("LDAP port", check_port("127.0.0.1", int(a["ldap"]["ldap_port"]))))
        if a["ldap"]["tls_mode"] == "ldaps":
            checks.append(("LDAPS port", check_port("127.0.0.1", int(a["ldap"]["ldaps_port"]))))
    for name, ok in checks:
        print(f"  {'OK' if ok else 'WARN'}  {name}")


def load_answers(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Guided text installer for Triage")
    parser.add_argument("--answer-file", type=Path, help="Read answers from a JSON file")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt; use answer file/defaults")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written/run without changing files")
    parser.add_argument("--execute", action="store_true", help="Run generated install/start commands after writing files")
    parser.add_argument("--output-dir", type=Path, default=Path(".runtime/installer"), help="Directory for generated helper files")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    root = Path(__file__).resolve().parents[1]
    loaded = load_answers(args.answer_file)
    ctx = InstallerContext(
        root=root,
        answers=deep_merge(default_answers(root), loaded),
        non_interactive=args.non_interactive,
        dry_run=args.dry_run,
        execute=args.execute,
        output_dir=args.output_dir,
    )
    if args.dry_run:
        ctx.non_interactive = True if args.non_interactive or args.answer_file else ctx.non_interactive
    answers = collect_answers(ctx)
    print_review(answers)
    if not ctx.non_interactive:
        proceed = input("\nContinue and write/apply this configuration? [y/N]: ").strip().lower() in ("y", "yes")
        if not proceed:
            print("Cancelled before changes.")
            return 1
    write_outputs(ctx)
    commands: list[str] = []
    if answers["actions"].get("install_dependencies") or answers["actions"].get("start_services"):
        commands = native_command_plan(answers) if answers["install_type"] in ("native", "config-only") else container_command_plan(answers)
        run_commands(commands, dry_run=ctx.dry_run, execute=ctx.execute)
    if answers["actions"].get("create_users"):
        bootstrap_users(answers, dry_run=ctx.dry_run, execute=ctx.execute)
    if answers["actions"].get("run_health_checks") and not ctx.dry_run:
        run_health_checks(answers)
    elif answers["actions"].get("run_health_checks"):
        print("[dry-run] would run API, frontend, RBAC, Postgres, RabbitMQ, and LDAP checks")
    print("\nInstaller workflow complete.")
    if commands and not ctx.execute:
        print("Commands were printed but not executed. Re-run with --execute to run them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
